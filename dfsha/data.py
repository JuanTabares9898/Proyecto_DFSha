"""Bloques cifrados inmutables con réplica S2S y confirmación durable."""
import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .common import Error, digest, packed, request, tls, verify


class Data:
    def __init__(self, config):
        self.c=config
        self.root=Path(config['storage'])
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.ctx=tls(config,'service')
        self.nodes={n['node_id']:n for n in config['nodes']}
        self.pool=ThreadPoolExecutor(max_workers=6)
        threading.Thread(target=self.heartbeat,daemon=True).start()

    def filename(self, bid):
        if not re.fullmatch('[a-f0-9-]{36}',bid):raise Error(400,'identificador inválido')
        return self.root/(bid+'.blk')

    def store(self, bid, body, sha, size):
        if len(body)!=size or digest(body)!=sha:raise Error(422,'checksum_mismatch')
        path=self.filename(bid)
        if path.exists():
            if digest(path.read_bytes())!=sha:raise Error(409,'el identificador de bloque es inmutable')
            return
        if shutil.disk_usage(self.root).free<len(body)+104857600:
            raise Error(507,'espacio insuficiente')
        temporary=self.root/(bid+'.'+str(uuid.uuid4())+'.part')
        try:
            with temporary.open('xb') as f:
                os.chmod(temporary,0o600)
                f.write(body);f.flush();os.fsync(f.fileno())
            # link crea el nombre final sin reemplazar un bloque existente.
            try:os.link(temporary,path)
            except FileExistsError:
                if digest(path.read_bytes())!=sha:raise Error(409,'colisión de bloque inmutable')
            directory=os.open(self.root,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def claims(self, headers, bid, op):
        raw=headers.get('Authorization','')
        if not raw.startswith('Bearer '):raise Error(401,'token de bloque requerido')
        claims=verify(self.c['cap_secret'],raw[7:],'block')
        if claims.get('block')!=bid or claims.get('op')!=op:raise Error(403,'capacidad no autoriza este bloque')
        if op=='put' and self.c['node_id'] not in claims['nodes']:
            raise Error(403,'nodo no autorizado por el plan')
        return claims

    def dispatch(self, method, path, query, body, headers, internal, cn):
        if internal:
            if cn not in set(self.nodes)|set(self.c['control_ids']):raise Error(403,'identidad interna no autorizada')
            if path.startswith('/internal/blocks/'):
                bid=path.rsplit('/',1)[1];file=self.filename(bid)
                if method=='GET':
                    if not file.exists():raise Error(404,'bloque ausente')
                    value=file.read_bytes()
                    return {'size':len(value),'sha256':digest(value)}
                if method=='PUT':
                    claims=self.claims(headers,bid,'put')
                    self.store(bid,body,claims['sha256'],claims['size'])
                    return 201,{'ok':True}
            if method=='GET' and path.startswith('/internal/raw/'):
                if cn not in set(self.nodes)|set(self.c['control_ids']):raise Error(403,'nodo no autorizado')
                file=self.filename(path.rsplit('/',1)[1])
                if not file.exists():raise Error(404,'bloque ausente')
                return file.read_bytes()
            if method=='POST' and path=='/internal/repair':
                if cn not in self.c['control_ids']:raise Error(403,'solo el control ordena reparación')
                args=json.loads(body)
                sources={n['internal'] for n in self.nodes.values()}
                if args['source'] not in sources:raise Error(403,'origen no registrado')
                raw=request(args['source']+'/internal/raw/'+args['block'],context=self.ctx,raw=True)
                self.store(args['block'],raw,args['sha256'],args['size'])
                logging.info('REPAIRED node=%s block=%s',self.c['node_id'],args['block'])
                return {'ok':True}
            raise Error(404,'ruta interna inexistente')
        if path=='/health' and method=='GET':return {'ok':True,'node':self.c['node_id'],'encrypted_blocks':True}
        if not path.startswith('/v1/blocks/'):raise Error(404,'ruta inexistente')
        bid=path.rsplit('/',1)[1]
        claims=self.claims(headers,bid,'put' if method=='PUT' else 'get')
        if method=='PUT':
            self.store(bid,body,claims['sha256'],claims['size'])
            def replicate(nid):
                try:
                    request(self.nodes[nid]['internal']+'/internal/blocks/'+bid,'PUT',body,
                            context=self.ctx,headers={'Authorization':headers['Authorization']},timeout=4)
                    return nid
                except Error:return None
            futures=[self.pool.submit(replicate,nid) for nid in claims['nodes'] if nid!=self.c['node_id']]
            confirmed=[self.c['node_id']]+[r for f in futures if (r:=f.result())]
            if len(confirmed)<self.c['write_quorum']:raise Error(503,'no se alcanzó el quórum de datos')
            return 201,{'stored_on':confirmed,'sha256':claims['sha256']}
        if method=='GET':
            path=self.filename(bid)
            if not path.exists():raise Error(404,'bloque ausente')
            raw=path.read_bytes()
            if digest(raw)!=claims['sha256']:raise Error(422,'corrupción de bloque detectada')
            return raw
        raise Error(405,'método incorrecto')

    def heartbeat(self):
        time.sleep(1)
        while True:
            descriptor=self.nodes[self.c['node_id']]
            for control in self.c['controls_internal']:
                try:
                    request(control+'/internal/heartbeat','POST',descriptor,context=self.ctx,timeout=2)
                    break
                except Error:pass
            time.sleep(3)
