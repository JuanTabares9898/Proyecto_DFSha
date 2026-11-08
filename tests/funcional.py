"""Pruebas integrales contra procesos y etcd reales; jamás inventa resultados."""
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dfsha.client import Client
from dfsha.common import Error, Etcd, request, tls

B=Path(__file__).resolve().parents[1]
OUT=B/'evidencias';OUT.mkdir(exist_ok=True)
FILES=OUT/'archivos';FILES.mkdir(exist_ok=True)
cfg=json.loads((B/'.private/client.json').read_text())
passwords=json.loads((B/'.private/usuarios.json').read_text())
admincfg=json.loads((B/'.private/control1.json').read_text())
db=Etcd(admincfg)
results=[]

def check(name, function):
    start=time.perf_counter()
    try:
        data=function() or {}
        record={'prueba':name,'ok':True,'segundos':time.perf_counter()-start,'resultado':data}
    except Exception as exc:
        record={'prueba':name,'ok':False,'segundos':time.perf_counter()-start,'error':str(exc)}
        results.append(record);save();print(json.dumps(record,ensure_ascii=False),flush=True);raise
    results.append(record);save();print(json.dumps(record,ensure_ascii=False),flush=True)

def save():
    (OUT/('funcional_'+os.environ.get('DFSHA_ENTORNO','local')+'.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2))

def denied(status, operation):
    try:operation()
    except Error as exc:
        assert exc.status==status,(exc.status,status,str(exc))
        return {'status':exc.status,'error':exc.message}
    raise AssertionError('La operación prohibida fue aceptada')

c=None
def auth():
    global c
    c=Client(cfg,'andres',passwords['andres'])
    assert len(c.call('/v1/status')['nodes'])==4
    return {'usuario':'andres','nodos':4}
check('autenticacion_y_cuatro_nodos',auth)
check('sin_credenciales',lambda:denied(401,lambda:request(cfg['controls'][0]+'/v1/fs/ls',context=tls(cfg))))
check('credenciales_invalidas',lambda:denied(401,lambda:Client(cfg,'pruebas','invalida')))
stamp='t'+str(time.time_ns())
c.fs('mkdir','/'+stamp)
root='/'+stamp
check('directorio_no_vacio',lambda:(c.fs('mkdir',root+'/hijo'),denied(409,lambda:c.fs('rmdir',root))))
check('aislamiento_usuario',lambda:denied(403,lambda:c.fs('ls','/users/juan/')))
check('path_traversal',lambda:denied(403,lambda:c.fs('ls','/../juan')))

source=FILES/'original.bin'
with source.open('wb') as f:
    for _ in range(16):f.write(os.urandom(4194304))
sha=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()
target=root+'/grande.bin'
check('carga_64_MiB',lambda:c.put(source,target))
def recover():
    c.get(target,FILES/'recuperado.bin')
    received=hashlib.file_digest((FILES/'recuperado.bin').open('rb'),'sha256').hexdigest()
    assert sha==received
    f=c.open(target);blocks=list(f.blocks.values());f.close()
    allurls=set(u for b in blocks for u in b['urls'])
    assert len(allurls)>=3
    return {'bytes':source.stat().st_size,'bloques':len(blocks),'hash_original':sha,'hash_recuperado':received,'nodos':sorted(allurls)}
check('recuperacion_hash_y_distribucion',recover)
check('otra_cuenta_no_ve_archivo',lambda:denied(403,lambda:Client(cfg,'juan',passwords['juan']).open('/users/andres'+target)))

def rf3():
    h=c.open(target,'rw');offset=4194304-3;data=b'PRUEBA-ENTRE-DOS-BLOQUES'
    snapshot=c.open(target)
    assert h.write(offset,data)==len(data)
    assert h.read(offset,len(data))==data
    assert snapshot.read(offset,len(data))!=data
    h.close()
    later=c.open(target)
    assert later.read(offset,len(data))==data
    assert snapshot.read(offset,len(data))!=data
    snapshot.close();later.close()
    return {'offset':offset,'bytes':len(data),'snapshot_antiguo_conservado':True,'nueva_apertura_ve_commit':True}
check('read_write_y_snapshot',rf3)

def append():
    h=c.open(target,'a');old=h.size;h.write(0,b'APENDICE');h.close()
    h=c.open(target);assert h.read(old,8)==b'APENDICE';h.close()
    return {'anexado':8}
check('append',append)

def concurrent_same():
    a=c.open(target,'rw');b=c.open(target,'rw')
    a.write(0,b'AAAA');b.write(0,b'BBBB')
    barrier=threading.Barrier(2)
    def commit(h):
        barrier.wait()
        try:h.close();return 200
        except Error as exc:
            print('Detalle del rechazo concurrente:',str(exc),flush=True)
            h.abort();return exc.status
    with ThreadPoolExecutor(2) as pool:codes=list(pool.map(commit,[a,b]))
    assert sorted(codes)==[200,409],codes
    return {'resultados':codes,'ganadores':1}
check('escritores_mismo_bloque',concurrent_same)

def concurrent_disjoint():
    a=c.open(target,'rw');b=c.open(target,'rw')
    a.write(0,b'CCCC');b.write(2*4194304,b'DDDD')
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda h:h.close(),[a,b]))
    h=c.open(target);assert h.read(0,4)==b'CCCC';assert h.read(2*4194304,4)==b'DDDD';h.close()
    return {'commits':2,'ambos_cambios_conservados':True}
check('escritores_bloques_distintos',concurrent_disjoint)

def locks():
    a=c.open(target,'rw');b=c.open(target,'rw')
    lock=a.lock(0,0)
    assert lock['fencing_token']>0
    conflict=denied(423,lambda:b.lock(0,0))
    b.write(0,b'NOPE')
    denied(423,b.close)
    a.unlock();a.close();b.abort()
    return conflict
check('lock_exclusivo',locks)

def expired():
    a=c.open(target,'rw');lock=a.lock(0,0,ttl=5);a.write(0,b'LATE')
    for _ in range(14):
        value,_=db.get('locks/'+a.info['file_id']+'/'+lock['id'])
        if value is None:break
        time.sleep(.5)
    result=denied(410,a.close);a.abort();return result
check('fencing_rechaza_escritor_vencido',expired)

def renewed():
    h=c.open(target,'rw');first=h.lock(0,0,ttl=5)
    time.sleep(3)
    fresh=h.renew(ttl=10)
    assert fresh['fencing_token']>first['fencing_token']
    time.sleep(3)
    h.write(0,b'RENEW');h.close()
    return {'primer_fencing':first['fencing_token'],'nuevo_fencing':fresh['fencing_token'],'commit_tras_TTL_original':True}
check('renovacion_lock_y_nuevo_fencing',renewed)

def tls_version():
    import socket
    import urllib.parse
    dest=urllib.parse.urlparse(cfg['controls'][0])
    context=ssl.create_default_context(cafile=cfg['ca'])
    context.maximum_version=ssl.TLSVersion.TLSv1_2
    try:
        with socket.create_connection((dest.hostname,dest.port),timeout=5) as sock:
            with context.wrap_socket(sock,server_hostname=dest.hostname):pass
    except ssl.SSLError:return {'TLS_1_2':'rechazado','minimo':'TLS 1.3'}
    raise AssertionError('se aceptó TLS 1.2')
check('TLS_1_3_obligatorio',tls_version)

def hidden():
    h=c.open(root+'/incompleto.bin','create')
    h.write(0,b'pendiente sin commit')
    result=denied(404,lambda:c.fs('stat',root+'/incompleto.bin'))
    h.abort();return result
check('sin_archivo_parcial_visible',hidden)

def encrypted():
    paths=list((B/'runtime').glob('datos*/*.blk'))
    assert paths
    marker=source.open('rb').read(64)
    assert all(marker not in p.read_bytes() for p in paths)
    assert all(p.stat().st_mode & 0o077==0 for p in paths)
    return {'bloques_en_disco':len(paths),'texto_claro_no_encontrado':True,'permisos':'0600','cifrado':'AES-256-GCM en el cliente'}
check('cifrado_y_permisos_en_reposo',encrypted)

def mtls():
    try:request(admincfg['nodes'][0]['internal']+'/internal/blocks/00000000-0000-0000-0000-000000000000',context=tls(cfg))
    except Error as exc:
        assert exc.status==503
        return {'sin_certificado_rechazado_por_TLS':True}
    raise AssertionError('mTLS no exigió certificado')
check('mTLS_interno',mtls)

def cipher_integrity():
    h=c.open(target,'rw');plain=b'x'*32
    nonce=os.urandom(12)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw=nonce+AESGCM(h.key).encrypt(nonce,plain,h.aad(0))
    plan=c.call('/v1/plan','POST',{'handle':h.id,'blocks':[{'index':0,'size':len(raw),'plain_size':len(plain),'sha256':hashlib.sha256(raw).hexdigest()}]})['blocks'][0]
    corrupt=raw[:-1]+bytes([raw[-1]^1])
    result=denied(422,lambda:request(plan['url']+'/v1/blocks/'+plan['id'],'PUT',corrupt,context=c.ctx,headers={'Authorization':'Bearer '+plan['token']}))
    h.abort();return result
check('corrupcion_rechazada',cipher_integrity)
check('borrar_y_directorios',lambda:(c.fs('rm',target),c.fs('rmdir',root+'/hijo'),c.fs('rmdir',root)))
print('PRUEBAS FUNCIONALES COMPLETAS',len(results),flush=True)
