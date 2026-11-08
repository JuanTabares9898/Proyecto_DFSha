"""Ejecuta pruebas reales y fallos solo en las VMs nuevas autorizadas."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.desplegar_aws import B,PRIVATE,STATE,REMOTE,clients,owned,connect,run,bundle

ec2=clients();state=json.loads(STATE.read_text());nodes=owned(ec2,state)
assert all(n['State']['Name']=='running' for n in nodes)
archive=bundle()
def sync(node):
    c=connect(node);s=c.open_sftp();s.put(str(archive),REMOTE+'/fuentes.tar.gz');s.close()
    run(c,'cd /opt/dfsha-aj && tar xzf fuentes.tar.gz && sudo systemctl restart "dfsha-aj-control*.service" "dfsha-aj-datos*.service"')
    c.close()
with ThreadPoolExecutor(4) as pool:list(pool.map(sync,nodes))
time.sleep(6)
first=connect(nodes[0])
def remote(command,timeout=900):
    output=run(first,'cd /opt/dfsha-aj && '+command,timeout=timeout)
    print(output,flush=True);return output
def py(code):
    import shlex
    return remote('.venv/bin/python -c '+shlex.quote(code))
def pull(name):
    s=first.open_sftp();s.get(REMOTE+'/evidencias/'+name,str(B/'evidencias'/name));s.close()

try:
    remote('DFSHA_ENTORNO=aws .venv/bin/python tests/funcional.py')
    pull('funcional_aws.json')
    setup='''import json,os,hashlib,time
from pathlib import Path
from dfsha.client import Client
B=Path.cwd();cfg=json.loads((B/'.private/client.json').read_text());pw=json.loads((B/'.private/usuarios.json').read_text());c=Client(cfg,'andres',pw['andres'])
root='/fallosaws'+str(time.time_ns());c.fs('mkdir',root);f=B/'evidencias/archivos/aws_fallos.bin';f.write_bytes(os.urandom(8*4194304));sha=hashlib.sha256(f.read_bytes()).hexdigest();c.put(f,root+'/datos.bin');h=c.open(root+'/datos.bin');bids=[b['id'] for b in h.blocks.values()];h.close()
(B/'evidencias/estado_fallos.json').write_text(json.dumps({'root':root,'sha256':sha,'blocks':bids}));print('Archivo de 32 MiB distribuido para fallos AWS')'''
    py(setup)
    victim=nodes[1]['InstanceId'];stopped=False;third_stopped=False
    results=[]
    def record(name,data):
        results.append({'prueba':name,'ok':True,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'resultado':data})
        (B/'evidencias/fallos_aws.json').write_text(json.dumps(results,indent=2,ensure_ascii=False));print(name,data,flush=True)
    header='''import json,hashlib,time
from pathlib import Path
from dfsha.client import Client
from dfsha.common import Etcd,Error
B=Path.cwd();x=json.loads((B/'evidencias/estado_fallos.json').read_text());cfg=json.loads((B/'.private/client.json').read_text());pw=json.loads((B/'.private/usuarios.json').read_text());c=Client(cfg,'andres',pw['andres']);db=Etcd(json.loads((B/'.private/control1.json').read_text()))
'''
    try:
        ec2.stop_instances(InstanceIds=[victim]);stopped=True
        ec2.get_waiter('instance_stopped').wait(InstanceIds=[victim],WaiterConfig={'Delay':5,'MaxAttempts':60})
        time.sleep(13)
        output=py(header+'''c.get(x['root']+'/datos.bin',B/'evidencias/archivos/aws_fallos_recuperado.bin');sha=hashlib.sha256((B/'evidencias/archivos/aws_fallos_recuperado.bin').read_bytes()).hexdigest();assert sha==x['sha256'];print(json.dumps({'sha_original':x['sha256'],'sha_recuperado':sha,'vm2':'stopped','etcd_vivos':2}))
c.put(B/'evidencias/archivos/aws_fallos.bin',x['root']+'/con_vm2_caida.bin');print('Carga nueva confirmada con VM2 apagada')''')
        record('caida_VM2_completa_lectura_y_carga',{'vm':victim,'salida':output.strip()})
        output=py(header+'''deadline=time.monotonic()+120
while time.monotonic()<deadline:
    copies=[db.get('blocks/'+bid)[0]['replicas'] for bid in x['blocks']]
    if all(len(v)==3 and 'datos2' not in v for v in copies):break
    time.sleep(2)
assert all(len(v)==3 and 'datos2' not in v for v in copies),copies
print(json.dumps({'bloques':len(copies),'replicas':copies,'reparacion_automatica':True}))''')
        record('re_replicacion_entre_VMs',json.loads(output))
        third=connect(nodes[2]);run(third,'sudo systemctl stop dfsha-aj-etcd.service');third_stopped=True
        no_majority=header.replace("c=Client(cfg,'andres',pw['andres']);", "")
        output=py(no_majority+'''try:
    c=Client(cfg,'andres',pw['andres']);c.fs('mkdir',x['root']+'/sin_mayoria')
except Error as e:
    assert e.status==503;print(json.dumps({'status':e.status,'escritura_sin_mayoria':'rechazada'}))
else:raise AssertionError('Aceptó metadatos sin mayoría')''')
        record('perdida_mayoria_etcd',json.loads(output))
        run(third,'sudo systemctl start dfsha-aj-etcd.service');third_stopped=False;third.close()
    finally:
        if third_stopped:
            third=connect(nodes[2]);run(third,'sudo systemctl start dfsha-aj-etcd.service');third.close()
        if stopped:
            ec2.start_instances(InstanceIds=[victim])
            ec2.get_waiter('instance_running').wait(InstanceIds=[victim],WaiterConfig={'Delay':5,'MaxAttempts':60})
    for _ in range(50):
        try:
            output=py(header+"assert len(c.call('/v1/status')['nodes'])==4;print('Cuatro nodos recuperados')")
            break
        except Exception:time.sleep(3)
    else:raise RuntimeError('VM2 no recuperó sus servicios')
    record('restauracion_cuatro_VMs',{'nodos_activos':4,'datos_conservados':True})
    # Cortar solo la réplica elegida después del plan. No se alteran SG ni otras VMs.
    setup_delay=header+'''from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os
h=c.open(x['root']+'/replica_tardia.bin','create');plain=b'REPLICA RETRASADA EN AWS'*1000;nonce=os.urandom(12);raw=nonce+AESGCM(h.key).encrypt(nonce,plain,h.aad(0));plan=c.call('/v1/plan','POST',{'handle':h.id,'blocks':[{'index':0,'size':len(raw),'plain_size':len(plain),'sha256':hashlib.sha256(raw).hexdigest()}]})['blocks'][0]
import base64
(B/'evidencias/plan_tardio_privado.json').write_text(json.dumps({'handle_info':h.info,'plan':plan,'raw':base64.b64encode(raw).decode(),'plain':base64.b64encode(plain).decode(),'root':x['root']}));print(plan['destinations'][-1])'''
    victim_name=py(setup_delay).strip();index=int(victim_name[-1])-1;node_now=owned(ec2,state)[index]
    delayed=connect(node_now)
    try:
        run(delayed,'sudo systemctl stop dfsha-aj-'+victim_name+'.service')
        output=py(header+'''import base64
from dfsha.common import request
from dfsha.client import Handle
p=json.loads((B/'evidencias/plan_tardio_privado.json').read_text());plan=p['plan'];raw=base64.b64decode(p['raw']);plain=base64.b64decode(p['plain']);h=Handle(c,p['handle_info'],'create')
answer=request(plan['url']+'/v1/blocks/'+plan['id'],'PUT',raw,context=c.ctx,headers={'Authorization':'Bearer '+plan['token']},timeout=20);assert len(answer['stored_on'])==2,answer
h.pending[0]={'plain':plain,'plan':plan};h.size=len(plain);h.dirty=True;h.close();h=c.open(p['root']+'/replica_tardia.bin');assert h.read()==plain;h.close();print(json.dumps({'replicas_confirmadas':2,'lectura_correcta':True}))
(B/'evidencias/plan_tardio_privado.json').unlink()''')
        record('replica_desconectada_despues_plan',{'replica':victim_name,**json.loads(output)})
    finally:
        run(delayed,'sudo systemctl start dfsha-aj-'+victim_name+'.service');delayed.close()
    remote('DFSHA_ENTORNO=aws DFSHA_MIB=128 .venv/bin/python tests/rendimiento.py',timeout=1800)
    pull('rendimiento_aws.json')
    atrest=[]
    for index,node in enumerate(owned(ec2,state)):
        c=connect(node)
        command="cd /opt/dfsha-aj && .venv/bin/python -c 'from pathlib import Path;import json,stat;ps=list(Path(\"runtime/datos"+str(index+1)+"\").glob(\"*.blk\"));assert ps;assert all(p.stat().st_mode & 0o077==0 for p in ps);print(json.dumps({\"bloques\":len(ps),\"permisos\":\"0600\",\"bytes\":sum(p.stat().st_size for p in ps)}))'"
        value=json.loads(run(c,command));c.close();value['vm']=node['InstanceId'];atrest.append(value)
    (B/'evidencias/almacenamiento_aws.json').write_text(json.dumps(atrest,indent=2))
    print('Pruebas AWS completas.',flush=True)
finally:
    first.close()
