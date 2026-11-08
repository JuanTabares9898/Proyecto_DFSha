"""Cliente por handles; cifra bloques y descubre ubicaciones desde el control."""
import copy
import os
from pathlib import Path
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .common import Error, unb64, digest, packed, request, tls


class Client:
    def __init__(self, config, username, password):
        self.config=config
        self.ctx=tls(config)
        self.uid=username
        self.auth=None
        self.auth=self.call('/v1/auth/login','POST',{'username':username,'password':password})['token']

    def call(self, path, method='GET', args=None):
        hdr={'Authorization':'Bearer '+self.auth} if self.auth else {}
        if method=='GET' and args:path+='?'+urllib.parse.urlencode(args)
        last=None
        for control in self.config['controls']:
            try:
                return request(control+path,method,args if method!='GET' else None,
                               context=self.ctx,headers=hdr,timeout=10)
            except Error as exc:
                if exc.status!=503:raise
                last=exc
        raise last or Error(503,'control no disponible')

    def fs(self, action, path='/'):
        method={'ls':'GET','stat':'GET','mkdir':'POST','rmdir':'DELETE','rm':'DELETE'}[action]
        if method=='DELETE':
            return self.call('/v1/fs/'+action+'?'+urllib.parse.urlencode({'path':path}),method)
        return self.call('/v1/fs/'+action,method,{'path':path})

    def open(self, path, mode='r'):
        info=self.call('/v1/open','POST',{'path':path,'mode':mode})
        return Handle(self,info,mode)

    def put(self, source, target, parallel=4):
        h=self.open(target,'w')
        try:
            size=Path(source).stat().st_size
            with open(source,'rb') as f:
                index=0
                with ThreadPoolExecutor(max_workers=parallel) as pool:
                    pending=[]
                    while raw:=f.read(h.block_size):
                        pending.append((index,raw));index+=1
                        if len(pending)>=parallel:
                            h.upload(pending,pool,keep_plain=False);pending=[]
                    if pending:h.upload(pending,pool,keep_plain=False)
            h.size=size;h.dirty=True
            return h.close()
        except Exception:
            h.abort();raise

    def get(self, source, target, parallel=4):
        h=self.open(source,'r')
        dest=Path(target)
        temp=dest.with_name(dest.name+'.part')
        try:
            with temp.open('wb') as f,ThreadPoolExecutor(max_workers=parallel) as pool:
                for raw in pool.map(h.fetch,range(len(h.blocks))):f.write(raw)
                f.flush();os.fsync(f.fileno())
            os.replace(temp,dest)
            return {'bytes':dest.stat().st_size}
        finally:
            temp.unlink(missing_ok=True);h.close()


class Handle:
    def __init__(self, client, info, mode):
        self.client=client;self.info=info;self.mode=mode
        self.id=info['handle'];self.block_size=info['block_size'];self.size=info['size']
        self.blocks={b['index']:copy.deepcopy(b) for b in info['blocks']}
        self.pending={};self.dirty=False;self.closed=False;self.lock_info=None
        self.key=unb64(info['key'])

    def aad(self, index):
        return packed({'file_id':self.info['file_id'],'index':index,'owner':self.client.uid})

    def fetch(self, index):
        if index in self.pending:return self.pending[index]['plain']
        block=self.blocks[index]
        last=None
        for attempt in range(2):
            for url in block['urls']:
                try:
                    raw=request(url+'/v1/blocks/'+block['id'],context=self.client.ctx,
                        headers={'Authorization':'Bearer '+block['token']},raw=True)
                    if digest(raw)!=block['sha256']:raise Error(422,'hash incorrecto en lectura')
                    return AESGCM(self.key).decrypt(raw[:12],raw[12:],self.aad(index))
                except (Error,ValueError) as exc:last=exc
            fresh=self.client.call('/v1/manifest',args={'handle':self.id})
            self.blocks.update({b['index']:b for b in fresh['blocks']});block=self.blocks[index]
        raise last or Error(503,'ninguna réplica confirmada disponible')

    def read(self, offset=0, length=None):
        if offset<0:raise Error(400,'offset negativo')
        if length is None:length=self.size-offset
        if length<0:raise Error(400,'longitud negativa')
        end=min(self.size,offset+length)
        if end<=offset:return b''
        first,last=offset//self.block_size,(end-1)//self.block_size
        chunks=[self.fetch(i) for i in range(first,last+1)]
        joined=b''.join(chunks)
        return joined[offset-first*self.block_size:end-first*self.block_size]

    def upload(self, entries, pool=None, keep_plain=True):
        encrypted=[]
        for index,plain in entries:
            nonce=os.urandom(12)
            raw=nonce+AESGCM(self.key).encrypt(nonce,plain,self.aad(index))
            encrypted.append({'index':index,'plain':plain,'cipher':raw,'sha256':digest(raw),
                              'size':len(raw),'plain_size':len(plain)})
        plans=self.client.call('/v1/plan','POST',{'handle':self.id,
                   'blocks':[{k:b[k] for k in ['index','size','plain_size','sha256']} for b in encrypted]})['blocks']
        def send(pair):
            block,plan=pair
            last=None
            for url in plan.get('urls',[plan['url']]):
                try:
                    request(url+'/v1/blocks/'+plan['id'],'PUT',block['cipher'],
                            context=self.client.ctx,headers={'Authorization':'Bearer '+plan['token']},timeout=20)
                    self.pending[block['index']]={'plain':block['plain'] if keep_plain else None,'plan':plan}
                    return
                except Error as exc:
                    if exc.status not in [503,404]:raise
                    last=exc
            raise last or Error(503,'ningún nodo primario alcanzable')
        if pool:list(pool.map(send,zip(encrypted,plans)))
        else:
            for pair in zip(encrypted,plans):send(pair)

    def write(self, offset, value):
        if self.mode=='r':raise Error(403,'handle de solo lectura')
        if self.mode=='a':offset=self.size
        if offset<0 or offset>self.size:raise Error(400,'no se admiten huecos dispersos')
        if not value:return 0
        end=offset+len(value);first=offset//self.block_size;last=(end-1)//self.block_size
        entries=[]
        for index in range(first,last+1):
            start=index*self.block_size
            old=self.fetch(index) if start<self.size else b''
            left=max(offset,start);right=min(end,start+self.block_size)
            relative=left-start
            content=old[:relative]+value[left-offset:right-offset]+old[right-start:]
            entries.append((index,content))
        self.upload(entries);self.size=max(self.size,end);self.dirty=True
        return len(value)

    def lock(self, start=0, end=2**40, ttl=30, kind='exclusive'):
        self.lock_info=self.client.call('/v1/lock','POST',{'handle':self.id,'start':start,'end':end,'ttl':ttl,'type':kind})
        return self.lock_info

    def unlock(self):
        if self.lock_info:
            self.client.call('/v1/unlock','POST',{'handle':self.id,'lock_id':self.lock_info['id']})
            self.lock_info=None

    def renew(self, ttl=30):
        if not self.lock_info:raise Error(410,'no tiene lock activo')
        self.lock_info=self.client.call('/v1/lock/renew','POST',
            {'handle':self.id,'lock_id':self.lock_info['id'],
             'fencing_token':self.lock_info['fencing_token'],'ttl':ttl})
        return self.lock_info

    def close(self):
        if self.closed:return {'closed':True}
        if self.dirty or self.mode in ['create','w']:
            lock={'id':self.lock_info['id'],'fencing_token':self.lock_info['fencing_token']} if self.lock_info else None
            result=self.client.call('/v1/commit','POST',{'handle':self.id,'size':self.size,
                                'blocks':[b['plan']['id'] for _,b in sorted(self.pending.items())],'lock':lock})
        else:result=self.client.call('/v1/close','POST',{'handle':self.id})
        self.closed=True;return result

    def abort(self):
        if not self.closed:
            try:self.client.call('/v1/close','POST',{'handle':self.id})
            except Error:pass
            self.closed=True
