"""Plano de control sin estado local: namespace, versiones, leases y CAS en etcd."""
import copy
import json
import logging
import math
import os
import posixpath
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .common import Error, Etcd, b64, unb64, digest, packed, request, tls, token, verify


class Control:
    def __init__(self, config):
        self.c = config
        self.db = Etcd(config)
        self.ctx = tls(config, 'service')
        self.hasher = PasswordHasher()
        self.pool = ThreadPoolExecutor(max_workers=12)
        threading.Thread(target=self.maintenance,daemon=True).start()

    def namespace(self, uid):
        key='namespace/'+uid
        record, rev=self.db.get(key)
        if record is None:
            self.db.cas(key,0,{'dirs':['/'],'files':{}})
            record,rev=self.db.get(key)
        return key,record,rev

    def path(self, uid, path):
        if not isinstance(path,str) or '\x00' in path or '\\' in path or len(path)>512:
            raise Error(400,'ruta inválida')
        if '..' in path.split('/'):
            raise Error(403,'no se permite atravesar directorios')
        if path.startswith('/users/'):
            prefix='/users/'+uid
            if path!=prefix and not path.startswith(prefix+'/'):
                raise Error(403,'el espacio pertenece a otro usuario')
            path=path[len(prefix):] or '/'
        return posixpath.normpath('/'+path.lstrip('/'))

    def identity(self, headers):
        raw=headers.get('Authorization','')
        if not raw.startswith('Bearer '):raise Error(401,'autenticación requerida')
        return verify(self.c['auth_secret'],raw[7:],'user')['sub']

    def file(self, uid, path):
        _,ns,_=self.namespace(uid)
        fid=ns['files'].get(path)
        if not fid:raise Error(404,'archivo inexistente')
        record,rev=self.db.get('files/'+fid)
        if not record or record['owner']!=uid:raise Error(404,'archivo inexistente')
        return fid,record,rev

    def handle(self, uid, hid):
        h,rev=self.db.get('handles/'+hid)
        if not h or h['owner']!=uid:raise Error(410,'handle vencido o inexistente')
        return h,rev

    def unwrap(self, owner, fid, wrapped):
        data=unb64(wrapped)
        return AESGCM(unb64(self.c['kek'])).decrypt(data[:12],data[12:],(owner+fid).encode())

    def wrap(self, owner, fid, key):
        nonce=os.urandom(12)
        return b64(nonce+AESGCM(unb64(self.c['kek'])).encrypt(nonce,key,(owner+fid).encode()))

    def alive(self):
        return {value['node_id']:value for _,value,_ in self.db.scan('nodes/')}

    def locations(self, uid, block):
        record,_=self.db.get('blocks/'+block['id'])
        if not record or record['owner']!=uid:raise Error(404,'bloque inexistente')
        live=self.alive()
        urls=[live[n]['url'] for n in record['replicas'] if n in live]
        claims={'sub':uid,'block':block['id'],'op':'get','sha256':block['sha256']}
        return {**block,'urls':urls,'token':token(self.c['cap_secret'],claims,'block',300)}

    def login(self, args):
        uid=args.get('username','')
        if not re.fullmatch('[a-zA-Z0-9_-]{1,40}',uid):raise Error(401,'credenciales inválidas')
        failkey='loginfail/'+uid
        failed,frev=self.db.get(failkey)
        if failed and failed['attempts']>=5:raise Error(429,'demasiados intentos; espere dos minutos')
        account,_=self.db.get('users/'+uid)
        good=False
        if account:
            try:good=self.hasher.verify(account['hash'],args.get('password',''))
            except (VerificationError,ValueError):pass
        if not good:
            lease=self.db.lease(120)
            self.db.cas(failkey,frev,{'attempts':(failed or {}).get('attempts',0)+1},lease)
            raise Error(401,'credenciales inválidas')
        self.db.txn([self.db.compare(failkey,frev)],[self.db.delete_op(failkey)])
        return {'token':token(self.c['auth_secret'],{'sub':uid},'user',900),'username':uid}

    def fs(self, uid, action, args):
        path=self.path(uid,args.get('path','/'))
        for _ in range(15):
            key,ns,rev=self.namespace(uid)
            if action=='ls':
                if path not in ns['dirs']:raise Error(404,'directorio inexistente')
                children=[{'name':posixpath.basename(p),'type':'dir'} for p in ns['dirs']
                          if p!='/' and posixpath.dirname(p)==path]
                children += [{'name':posixpath.basename(p),'type':'file'} for p in ns['files']
                             if posixpath.dirname(p)==path]
                return {'path':path,'entries':sorted(children,key=lambda x:x['name'])}
            if action=='stat':
                if path in ns['dirs']:return {'type':'dir','path':path}
                _,f,_=self.file(uid,path)
                current=f['versions'][str(f['version'])]
                return {'type':'file','path':path,'version':f['version'],'size':current['size']}
            if action=='mkdir':
                if path in ns['dirs'] or path in ns['files']:raise Error(409,'ruta ya existente')
                if posixpath.dirname(path) not in ns['dirs']:raise Error(404,'directorio padre inexistente')
                ns['dirs'].append(path)
            elif action=='rmdir':
                if path=='/':raise Error(403,'no se puede borrar la raíz')
                if path not in ns['dirs']:raise Error(404,'directorio inexistente')
                if any(p.startswith(path+'/') for p in ns['dirs']+list(ns['files'])):
                    raise Error(409,'directorio no vacío')
                ns['dirs'].remove(path)
            elif action=='rm':
                fid=ns['files'].get(path)
                if not fid:raise Error(404,'archivo inexistente')
                # El borrado y el commit compiten por la revisión del namespace.
                # Los handles de lectura existentes retienen su instantánea.
                del ns['files'][path]
            else:raise Error(404,'operación inexistente')
            if self.db.cas(key,rev,ns)['succeeded']:return {'ok':True,'path':path}
        raise Error(409,'conflicto de namespace; reintente')

    def open(self, uid, args):
        path=self.path(uid,args['path'])
        mode=args.get('mode','r')
        if mode not in ['r','rw','w','create','a']:raise Error(400,'modo desconocido')
        _,ns,_=self.namespace(uid)
        if path in ns['dirs']:raise Error(409,'la ruta es un directorio')
        if posixpath.dirname(path) not in ns['dirs']:raise Error(404,'directorio padre inexistente')
        exists=path in ns['files']
        if mode=='create' and exists:raise Error(409,'el archivo ya existe')
        if mode in ['r','rw','a'] and not exists:raise Error(404,'archivo inexistente')
        if exists:
            fid,f,_=self.file(uid,path)
            original=copy.deepcopy(f['versions'][str(f['version'])])
            wrapped=f['wrapped_key']; base=f['version']
        else:
            fid=str(uuid.uuid4());original={'size':0,'blocks':[]};base=0
            wrapped=self.wrap(uid,fid,os.urandom(32))
        lease=self.db.lease(3600)
        hid=str(uuid.uuid4())
        h={'id':hid,'owner':uid,'file_id':fid,'path':path,'mode':mode,'base':base,
           'original':original,'wrapped_key':wrapped,'lease':lease,'plans':{}}
        self.db.cas('handles/'+hid,0,h,lease)
        visible={'size':0,'blocks':[]} if mode in ['w','create'] else original
        return {'handle':hid,'file_id':fid,'version':base,'size':visible['size'],
                'block_size':self.c['block_size'],'key':b64(self.unwrap(uid,fid,wrapped)),
                'blocks':[self.locations(uid,b) for b in visible['blocks']]}

    def acquire_lock(self, uid, args):
        hid=args['handle'];h,_=self.handle(uid,hid)
        if h['mode']=='r':raise Error(403,'handle de solo lectura')
        start=int(args.get('start',0));end=int(args.get('end',2**40))
        shared=args.get('type','exclusive')=='shared'
        if start<0 or end<start:raise Error(400,'rango inválido')
        ttl=max(5,min(int(args.get('ttl',30)),300))
        # Un contador persistente serializa adquisición y commit, evitando carreras
        # entre locks en claves distintas. El lease lo controla etcd, no un reloj local.
        guard='lockguard/'+h['file_id']
        for _ in range(20):
            value,revision=self.db.get(guard)
            locks=self.db.scan('locks/'+h['file_id']+'/')
            for _,lock,_ in locks:
                overlap=not(end<lock['start'] or start>lock['end'])
                if overlap and lock['handle']!=hid and not(shared and lock['shared']):
                    raise Error(423,'rango bloqueado por otro handle')
            lid=str(uuid.uuid4());lease=self.db.lease(ttl)
            lock={'id':lid,'handle':hid,'owner':uid,'start':start,'end':end,'shared':shared,'lease':lease}
            result=self.db.txn([self.db.compare(guard,revision)],
                [self.db.put_op(guard,{'counter':(value or {}).get('counter',0)+1}),
                 self.db.put_op('locks/'+h['file_id']+'/'+lid,lock,lease)])
            if result['succeeded']:
                return {**lock,'fencing_token':int(result['header']['revision']),'ttl':ttl}
            self.db.revoke(lease)
        raise Error(409,'conflicto al adquirir bloqueo')

    def release_lock(self, uid, args):
        h,_=self.handle(uid,args['handle']);key='locks/'+h['file_id']+'/'+args['lock_id']
        lock,rev=self.db.get(key)
        if not lock or lock['owner']!=uid or lock['handle']!=h['id']:raise Error(410,'lock vencido')
        if not self.db.txn([self.db.compare(key,rev)],[self.db.delete_op(key)])['succeeded']:
            raise Error(409,'cambió el lock')
        self.db.revoke(lock['lease'])
        return {'ok':True}

    def renew_lock(self, uid, args):
        h,_=self.handle(uid,args['handle']);key='locks/'+h['file_id']+'/'+args['lock_id']
        guard='lockguard/'+h['file_id']
        for _ in range(20):
            lock,rev=self.db.get(key);counter,grev=self.db.get(guard)
            if not lock or lock['owner']!=uid or lock['handle']!=h['id']:
                raise Error(410,'lock vencido: no puede renovarse')
            if int(args['fencing_token'])!=rev:raise Error(410,'fencing token anterior')
            ttl=max(5,min(int(args.get('ttl',30)),300));lease=self.db.lease(ttl)
            updated={**lock,'lease':lease}
            result=self.db.txn([self.db.compare(key,rev),self.db.compare(guard,grev)],
                [self.db.put_op(guard,{'counter':(counter or {}).get('counter',0)+1}),
                 self.db.put_op(key,updated,lease)])
            if result['succeeded']:
                self.db.revoke(lock['lease'])
                return {**updated,'fencing_token':int(result['header']['revision']),'ttl':ttl}
            self.db.revoke(lease)
        raise Error(409,'conflicto al renovar bloqueo')

    def plan(self, uid, args):
        hid=args['handle'];h,hrev=self.handle(uid,hid)
        if h['mode']=='r':raise Error(403,'handle de solo lectura')
        live=self.alive()
        if len(live)<self.c['write_quorum']:raise Error(503,'menos de dos nodos disponibles')
        plans=[]
        for b in args['blocks']:
            index=int(b['index']);size=int(b['size']);plain=int(b['plain_size'])
            if index<0 or plain<1 or plain>self.c['block_size'] or size!=plain+28:
                raise Error(400,'tamaño o índice de bloque inválido')
            if not re.fullmatch('[0-9a-f]{64}',b['sha256']):raise Error(400,'hash inválido')
            bid=str(uuid.uuid4())
            ordered=sorted(live.values(),key=lambda n:digest((bid+n['node_id']).encode()),reverse=True)
            # Los cuatro nodos tienen dominios distintos; se evita duplicar host.
            selected=[];domains=set()
            for node in ordered:
                if node['domain'] not in domains:
                    selected.append(node);domains.add(node['domain'])
                if len(selected)==self.c['replication']:break
            if len(selected)<self.c['write_quorum']:raise Error(503,'dominios de fallo insuficientes')
            desc={'id':bid,'index':index,'plain_size':plain,'size':size,'sha256':b['sha256'],
                  'destinations':[n['node_id'] for n in selected]}
            h['plans'][bid]=desc
            cap=token(self.c['cap_secret'],{'sub':uid,'block':bid,'op':'put',
                       'sha256':b['sha256'],'size':size,'nodes':[n['node_id'] for n in selected]},'block',600)
            plans.append({**desc,'url':selected[0]['url'],'urls':[n['url'] for n in selected],'token':cap})
        if not self.db.cas('handles/'+hid,hrev,h,h['lease'])['succeeded']:
            raise Error(409,'handle modificado por otra petición')
        return {'blocks':plans}

    def receipt(self, node, block):
        try:
            result=request(node['internal']+'/internal/blocks/'+block['id'],context=self.ctx,timeout=3)
            return result.get('sha256')==block['sha256'] and result.get('size')==block['size']
        except Error:return False

    def commit(self, uid, args):
        hid=args['handle'];h,hrev=self.handle(uid,hid)
        if h['mode']=='r':raise Error(403,'handle de solo lectura')
        ids=args.get('blocks',[])
        if len(ids)!=len(set(ids)):raise Error(400,'bloque duplicado')
        updates={}
        live=self.alive()
        new_locations={}
        for bid in ids:
            b=h['plans'].get(bid)
            if not b:raise Error(403,'bloque no asignado a este handle')
            if b['index'] in updates:raise Error(400,'índice repetido')
            futures={node:self.pool.submit(self.receipt,live[node],b)
                     for node in b['destinations'] if node in live}
            confirmed=[node for node,future in futures.items() if future.result()]
            if len(confirmed)<self.c['write_quorum']:raise Error(503,'no hay dos copias durables verificadas')
            new_locations[bid]={'owner':uid,'replicas':confirmed,'sha256':b['sha256'],'size':b['size']}
            updates[b['index']]={k:v for k,v in b.items() if k!='destinations'}
        new_size=int(args['size'])
        if new_size<0:raise Error(400,'tamaño negativo')
        original={b['index']:b for b in h['original']['blocks']}
        for _ in range(15):
            nskey,ns,nsrev=self.namespace(uid)
            if posixpath.dirname(h['path']) not in ns['dirs']:raise Error(409,'directorio padre borrado')
            known=ns['files'].get(h['path'])
            if known and known!=h['file_id']:raise Error(409,'otra creación ganó la ruta')
            if h['base'] and known!=h['file_id']:raise Error(409,'archivo eliminado o reemplazado')
            fkey='files/'+h['file_id'];f,frev=self.db.get(fkey)
            if f is None:
                f={'owner':uid,'path':h['path'],'version':0,'versions':{},'wrapped_key':h['wrapped_key']}
            current=copy.deepcopy(f['versions'].get(str(f['version']),{'size':0,'blocks':[]}))
            latest={b['index']:b for b in current['blocks']}
            whole=h['mode'] in ['w','create','a']
            if whole and f['version']!=h['base']:raise Error(409,'version_conflict')
            if not whole:
                if new_size!=h['original']['size'] and f['version']!=h['base']:
                    raise Error(409,'conflicto al cambiar el tamaño')
                for index in updates:
                    if latest.get(index,{}).get('id')!=original.get(index,{}).get('id'):
                        raise Error(409,'version_conflict en bloque '+str(index))
            guard='lockguard/'+h['file_id'];_,guardrev=self.db.get(guard)
            locks=self.db.scan('locks/'+h['file_id']+'/')
            compare=[self.db.compare(nskey,nsrev),self.db.compare(fkey,frev),
                     self.db.compare('handles/'+hid,hrev),self.db.compare(guard,guardrev)]
            wanted=args.get('lock')
            active=None
            touched=set(updates)
            if whole:touched=set(range(max(math.ceil(current['size']/self.c['block_size']),math.ceil(new_size/self.c['block_size']))))
            for lk,lock,lrev in locks:
                overlaps=any(lock['start']<=i<=lock['end'] for i in touched)
                if overlaps and lock['handle']!=hid:
                    raise Error(423,'escritura rechazada por bloqueo de otro handle')
                if wanted and lock['id']==wanted['id'] and lock['handle']==hid:
                    active=(lk,lock,lrev)
            if wanted:
                if active is None or int(wanted['fencing_token'])!=active[2]:
                    raise Error(410,'lease vencido o fencing token inválido')
                if active[1]['shared'] or any(not(active[1]['start']<=i<=active[1]['end']) for i in touched):
                    raise Error(403,'lock sin cobertura exclusiva del rango')
                compare.append(self.db.compare(active[0],active[2]))
            merged={} if h['mode'] in ['w','create'] else latest
            merged.update(updates)
            if not whole and new_size==h['original']['size']:
                new_size=current['size']
            count=math.ceil(new_size/self.c['block_size'])
            blocks=[merged[i] for i in range(count) if i in merged]
            if len(blocks)!=count:raise Error(400,'faltan bloques: no se publica el archivo parcial')
            for i,b in enumerate(blocks):
                expected=min(self.c['block_size'],new_size-i*self.c['block_size'])
                if b['plain_size']!=expected:raise Error(400,'longitud de bloque incompatible con el archivo')
            version=f['version']+1;f['version']=version
            f['versions'][str(version)]={'size':new_size,'blocks':blocks}
            ns['files'][h['path']]=h['file_id']
            ops=[self.db.put_op(fkey,f),self.db.put_op(nskey,ns),self.db.delete_op('handles/'+hid)]
            for bid,record in new_locations.items():ops.append(self.db.put_op('blocks/'+bid,record))
            if active:ops.append(self.db.delete_op(active[0]))
            if self.db.txn(compare,ops)['succeeded']:
                logging.info('COMMIT owner=%s file=%s version=%s blocks=%s',uid,h['file_id'],version,len(blocks))
                return {'version':version,'file_id':h['file_id'],'size':new_size}
        raise Error(409,'demasiados commits concurrentes')

    def dispatch(self, method, path, query, body, headers, internal, cn):
        args=json.loads(body or '{}') if method in ['POST','PUT'] else {k:v[0] for k,v in query.items()}
        if internal:
            if method=='POST' and path=='/internal/heartbeat':
                nid=args['node_id'];allowed={n['node_id']:n for n in self.c['nodes']}
                if cn!=nid or nid not in allowed:raise Error(403,'identidad de nodo no autorizada')
                expected=allowed[nid]
                if any(args.get(k)!=expected[k] for k in ['url','internal','domain']):
                    raise Error(403,'ubicación no registrada por administrador')
                lease=self.db.lease(12);key='nodes/'+nid
                self.db.call('kv/put',{'key':self.db.key(key),'value':b64(packed(expected)),'lease':str(lease)})
                return {'ok':True}
            raise Error(404,'ruta interna inexistente')
        if method=='POST' and path=='/v1/auth/login':return self.login(args)
        if method=='GET' and path=='/health':
            self.db.get('health')
            return {'ok':True,'control':self.c['node_id'],'tls_min':'1.3'}
        uid=self.identity(headers)
        if path.startswith('/v1/fs/'):
            action=path.rsplit('/',1)[1]
            allowed={'ls':'GET','stat':'GET','mkdir':'POST','rmdir':'DELETE','rm':'DELETE'}
            if allowed.get(action)!=method:raise Error(405,'método incorrecto')
            return self.fs(uid,action,args)
        if path=='/v1/open' and method=='POST':return self.open(uid,args)
        if path=='/v1/plan' and method=='POST':return self.plan(uid,args)
        if path=='/v1/commit' and method=='POST':return self.commit(uid,args)
        if path=='/v1/lock' and method=='POST':return self.acquire_lock(uid,args)
        if path=='/v1/lock/renew' and method=='POST':return self.renew_lock(uid,args)
        if path=='/v1/unlock' and method=='POST':return self.release_lock(uid,args)
        if path=='/v1/close' and method=='POST':
            h,rev=self.handle(uid,args['handle'])
            self.db.txn([self.db.compare('handles/'+h['id'],rev)],[self.db.delete_op('handles/'+h['id'])])
            for key,lock,lrev in self.db.scan('locks/'+h['file_id']+'/'):
                if lock['handle']==h['id']:self.db.txn([self.db.compare(key,lrev)],[self.db.delete_op(key)])
            return {'ok':True}
        if path=='/v1/manifest' and method=='GET':
            h,_=self.handle(uid,args['handle'])
            return {'blocks':[self.locations(uid,b) for b in h['original']['blocks']],
                    'size':h['original']['size'],'version':h['base']}
        if path=='/v1/status' and method=='GET':return {'nodes':list(self.alive().values()),'control':self.c['node_id']}
        raise Error(404,'ruta inexistente')

    def maintenance(self):
        # Elección de trabajador por lease real de etcd. Nunca se crean procesos
        # de reparación rivales capaces de sobrescribir metadatos sin CAS.
        while True:
            try:
                lease=self.db.lease(15)
                won=self.db.cas('repair-leader',0,{'node':self.c['node_id']},lease)['succeeded']
                if won:
                    live=self.alive()
                    for key,block,rev in self.db.scan('blocks/'):
                        bid=key.split('/')[-1]
                        good=[]
                        desc={'id':bid,**block}
                        for nid in block['replicas']:
                            if nid in live and self.receipt(live[nid],desc):good.append(nid)
                        if good and len(good)<self.c['replication']:
                            candidates=[n for nid,n in live.items() if nid not in good]
                            for target in candidates:
                                if len(good)>=self.c['replication']:break
                                try:
                                    request(target['internal']+'/internal/repair','POST',
                                        {'block':bid,'source':live[good[0]]['internal'],
                                         'sha256':block['sha256'],'size':block['size']},context=self.ctx,timeout=8)
                                    if self.receipt(target,desc):good.append(target['node_id'])
                                except Error:pass
                            if len(good)>=len(block['replicas']):
                                self.db.cas(key,rev,{**block,'replicas':good})
                    self.db.revoke(lease)
                else:self.db.revoke(lease)
            except Error:
                logging.warning('reparación pendiente: metadatos o nodos no disponibles')
            except Exception:logging.exception('trabajador de reparación')
            time.sleep(5)
