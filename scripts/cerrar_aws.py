"""Recoge registros públicos, verifica preservación y apaga solo el despliegue nuevo."""
from pathlib import Path
import json
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.desplegar_aws import B,PRIVATE,STATE,REMOTE,clients,owned,connect,run,stop

ec2=clients();state=json.loads(STATE.read_text());nodes=owned(ec2,state);E=B/'evidencias'
for i,node in enumerate(nodes):
    c=connect(node);s=c.open_sftp()
    s.put(str(B/'dfsha/cli.py'),REMOTE+'/dfsha/cli.py')
    s.close()
    logs=run(c,'sudo journalctl --no-pager -u "dfsha-aj-*.service" -n 120 -o short-iso',timeout=30)
    assert 'Bearer ' not in logs and 'aws_secret_access_key' not in logs
    (E/('aws_VM'+str(i+1)+'_servicios.log')).write_text(logs)
    fingerprints=run(c,'for f in /opt/dfsha-aj/.private/certs/*.pem; do openssl x509 -in "$f" -noout -subject -enddate -fingerprint -sha256; done',timeout=30)
    (E/('aws_VM'+str(i+1)+'_certificados.txt')).write_text(fingerprints)
    if i==0:
        cli=run(c,'cd /opt/dfsha-aj && .venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json status')
        assert len(json.loads(cli)['nodes'])==4
        (E/'aws_CLI_status.json').write_text(cli)
    c.close()
vols=ec2.describe_volumes(Filters=[{'Name':'tag:Proyecto','Values':[state['project']]}])['Volumes']
assert len(vols)==4 and all(v['Encrypted'] and v['Size']==12 for v in vols)
sg=ec2.describe_security_groups(GroupIds=[state['sg']])['SecurityGroups'][0]
for rule in sg['IpPermissions']:
    for ip in rule.get('IpRanges',[]):
        assert rule['FromPort']==22 and ip['CidrIp'].endswith('/32')
    for group in rule.get('UserIdGroupPairs',[]):assert group['GroupId']==state['sg']
original=json.loads((PRIVATE/'inventario_inicial.json').read_text())
ids=[i['InstanceId'] for i in original['instances']]
after={i['InstanceId']:i for r in ec2.describe_instances(InstanceIds=ids)['Reservations'] for i in r['Instances']}
fields=['ImageId','InstanceType','KeyName','SecurityGroups','SubnetId','VpcId','BlockDeviceMappings','PrivateIpAddress']
preserved=[]
for before in original['instances']:
    now=json.loads(json.dumps(after[before['InstanceId']],default=str))
    assert all(before.get(f)==now.get(f) for f in fields)
    assert now['State']['Name']==before['State']['Name']
    preserved.append({'id':before['InstanceId'],'nombre':next((t['Value'] for t in before.get('Tags',[]) if t['Key']=='Name'),''),'configuracion_preservada':True,'estado':now['State']['Name']})
oldvols={v['VolumeId']:v for v in original['volumes']}
for current in ec2.describe_volumes(VolumeIds=list(oldvols))['Volumes']:
    current=json.loads(json.dumps(current,default=str))
    old=oldvols[current['VolumeId']]
    assert all(old.get(f)==current.get(f) for f in ['Size','Encrypted','VolumeType','Iops','Throughput','Attachments'])
print('Instancias y discos previos: configuración y estado preservados.',flush=True)
stop(ec2,state)
final=owned(ec2,state)
output={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'proyecto':state['project'],
        'instancias':[{'vm':i+1,'id':n['InstanceId'],'estado':n['State']['Name'],'tipo':n['InstanceType'],'ip_privada':n['PrivateIpAddress'],'zona':n['Placement']['AvailabilityZone']} for i,n in enumerate(final)],
        'volumenes':[{'id':v['VolumeId'],'GiB':v['Size'],'cifrado':v['Encrypted'],'tipo':v['VolumeType'],'instancia':v['Attachments'][0]['InstanceId']} for v in vols],
        'grupo_seguridad':{'id':sg['GroupId'],'nombre':sg['GroupName'],'puertos_internos':sorted(set(r['FromPort'] for r in sg['IpPermissions'] if r.get('UserIdGroupPairs'))),'SSH_externo':'IP de administrador /32','aplicacion_publica':False},
        'recursos_previos':preserved,'discos_previos_preservados':len(oldvols),'ningun_recurso_previo_modificado':True}
(E/'aws_inventario_final.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
print('Evidencias guardadas y cuatro VMs detenidas.',flush=True)
