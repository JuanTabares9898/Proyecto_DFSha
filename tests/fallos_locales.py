"""Fallos reales de procesos de este proyecto, reparación y quórum etcd."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dfsha.client import Client
from dfsha.common import Etcd, Error, tls, request

B=Path(__file__).resolve().parents[1]
config=json.loads((B/'.private/client.json').read_text())
accounts=json.loads((B/'.private/usuarios.json').read_text())
c=Client(config,'andres',accounts['andres'])
db=Etcd(json.loads((B/'.private/control1.json').read_text()))
pids=json.loads((B/'runtime/procesos.json').read_text())
results=[]
def record(name,data):
    results.append({'prueba':name,'resultado':data,'ok':True,'utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()})
    (B/'evidencias/fallos_locales.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(name,json.dumps(data,ensure_ascii=False),flush=True)
def stop(name):
    cmd=subprocess.run(['ps','-p',str(pids[name]),'-o','command='],capture_output=True,text=True).stdout
    assert str(B/'.venv') in cmd and 'dfsha.main' in cmd
    os.kill(pids[name],signal.SIGTERM)
def start(name):
    env={**os.environ,'DFSHA_CONFIG':str(B/'.private'/(name+'.json'))}
    with (B/'runtime'/(name+'.log')).open('ab') as log:
        proc=subprocess.Popen([sys.executable,'-m','dfsha.main'],cwd=B,env=env,stdout=log,stderr=log,start_new_session=True)
    pids[name]=proc.pid;(B/'runtime/procesos.json').write_text(json.dumps(pids))
root='/fallos'+str(time.time_ns());c.fs('mkdir',root)
source=B/'evidencias/archivos/fallos_original.bin';source.write_bytes(os.urandom(5*4194304))
sha=hashlib.sha256(source.read_bytes()).hexdigest()
path=root+'/datos.bin';c.put(source,path)
h=c.open(path);fid=h.info['file_id'];bids=[b['id'] for b in h.blocks.values()]
snap_hash=hashlib.sha256(h.read()).hexdigest();assert snap_hash==sha

stopped=[]
try:
    stop('datos1');stopped.append('datos1')
    c.get(path,B/'evidencias/archivos/fallos_recuperado.bin')
    actual=hashlib.sha256((B/'evidencias/archivos/fallos_recuperado.bin').read_bytes()).hexdigest()
    assert sha==actual
    record('caida_nodo_y_lectura',{'nodo':'datos1','hash_original':sha,'hash_recuperado':actual})
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        good=True
        for bid in bids:
            block,_=db.get('blocks/'+bid)
            if len(block['replicas'])!=3 or 'datos1' in block['replicas']:good=False
        if good:break
        time.sleep(2)
    assert good,'la reparación no completó tres réplicas en nodos vivos'
    record('re_replicacion_a_nodo_suplente',{'bloques':len(bids),'replicas_por_bloque':3,'destinos':['datos2','datos3','datos4']})
    stop('control1');stopped.append('control1')
    subprocess.run(['docker','stop','dfsha-aj-etcd1'],check=True,capture_output=True)
    c.put(source,root+'/con_control_caido.bin')
    record('caida_control_y_etcd',{'control1':'apagado','miembros_etcd_vivos':2,'carga_con_quorum':True})
    subprocess.run(['docker','stop','dfsha-aj-etcd2'],check=True,capture_output=True)
    started=time.monotonic()
    try:c.fs('mkdir',root+'/no_debe_publicarse')
    except Error as exc:
        assert exc.status==503
        record('sin_mayoria_rechaza_metadatos',{'status':exc.status,'segundos':round(time.monotonic()-started,3)})
    else:raise AssertionError('se aceptó escritura sin mayoría')
finally:
    subprocess.run(['docker','start','dfsha-aj-etcd1','dfsha-aj-etcd2'],check=True,capture_output=True)
    for name in stopped:start(name)
    time.sleep(5)
    h.close()
record('restauracion',{'nodos':len(c.call('/v1/status')['nodes']),'etcd':'tres miembros reiniciados'})

# Réplica asignada que se desconecta después del plan, no antes.
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
new=c.open(root+'/replica_retrasada.bin','create');plain=os.urandom(131072);nonce=os.urandom(12)
raw=nonce+AESGCM(new.key).encrypt(nonce,plain,new.aad(0))
plan=c.call('/v1/plan','POST',{'handle':new.id,'blocks':[{'index':0,'size':len(raw),'plain_size':len(plain),'sha256':hashlib.sha256(raw).hexdigest()}]})['blocks'][0]
victim=plan['destinations'][-1]
try:
    stop(victim)
    upload=request(plan['url']+'/v1/blocks/'+plan['id'],'PUT',raw,context=c.ctx,
                   headers={'Authorization':'Bearer '+plan['token']},timeout=20)
    assert len(upload['stored_on'])==2
    new.pending[0]={'plain':plain,'plan':plan};new.size=len(plain);new.dirty=True;new.close()
    r=c.open(root+'/replica_retrasada.bin');assert r.read()==plain;r.close()
    record('replica_desconectada_durante_escritura',{'replica':victim,'confirmadas':2,'lectura_version_confirmada':True})
finally:start(victim)
print('FALLOS LOCALES COMPLETOS',flush=True)
