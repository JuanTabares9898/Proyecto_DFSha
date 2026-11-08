"""Despliegue aislado. Nunca adopta ni detiene recursos ajenos al registro privado."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import shlex
import sys
import tarfile
import time
import urllib.request

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.aws_inventory import session
from scripts.preparar import generate

B = Path(__file__).resolve().parents[1]
PRIVATE = B / '.private'
STATE = PRIVATE / 'aws_recursos.json'
PROJECT = 'DFSha-AJ-20261108'
ACCOUNT = '413371389717'
REMOTE = '/opt/dfsha-aj'


def save(state):
    STATE.write_text(json.dumps(state, indent=2, default=str)); STATE.chmod(0o600)


def clients():
    s = session()
    assert s.client('sts').get_caller_identity()['Account'] == ACCOUNT, 'Cuenta incorrecta: se cancela.'
    return s.client('ec2')


def owned(ec2, state):
    ids = state.get('instances', [])
    if not ids:
        return []
    nodes = [i for r in ec2.describe_instances(InstanceIds=ids)['Reservations'] for i in r['Instances']]
    assert len(nodes) == len(ids)
    for n in nodes:
        tags = {t['Key']: t['Value'] for t in n.get('Tags', [])}
        assert tags.get('Proyecto') == PROJECT, 'Recurso no autorizado: se cancela.'
    return sorted(nodes, key=lambda n: next(t['Value'] for t in n['Tags'] if t['Key'] == 'Name'))


def launch(ec2):
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get('instances'):
            if len(state['instances'])!=4:raise RuntimeError('Despliegue parcial: revisar el registro antes de continuar.')
            print('Se conserva el despliegue existente; no se duplican máquinas.', flush=True)
            return state
    else:
        state = {'account': ACCOUNT, 'project': PROJECT, 'instances': [], 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        save(state)
    inv = json.loads((PRIVATE / 'inventario_inicial.json').read_text())
    assert inv['identity']['Account'] == ACCOUNT
    vpc = next(v for v in inv['vpcs'] if v['IsDefault'])
    subnet = sorted((s for s in inv['subnets'] if s['VpcId'] == vpc['VpcId'] and s['MapPublicIpOnLaunch']), key=lambda s: s['AvailabilityZone'])[0]
    ip = urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10).read().decode().strip()
    import ipaddress
    ipaddress.IPv4Address(ip)
    tags = [{'Key': 'Proyecto', 'Value': PROJECT}, {'Key': 'Equipo', 'Value': 'Andres-Juan'}]
    if not state.get('sg'):
        state['sg'] = ec2.create_security_group(GroupName=PROJECT, Description='Solo DFSha Andres Juan; no recursos anteriores', VpcId=vpc['VpcId'], TagSpecifications=[{'ResourceType':'security-group','Tags':tags}])['GroupId']
        save(state)
        ec2.authorize_security_group_ingress(GroupId=state['sg'], IpPermissions=[
            {'IpProtocol':'tcp','FromPort':22,'ToPort':22,'IpRanges':[{'CidrIp':ip+'/32','Description':'Mac de trabajo'}]},
            *[{'IpProtocol':'tcp','FromPort':port,'ToPort':port,'UserIdGroupPairs':[{'GroupId':state['sg'],'Description':'Solo nodos del proyecto'}]} for port in (22,2379,2380,8443,8444,9443,9444)]])
    keypath = PRIVATE / 'ssh_aws.pem'
    if not state.get('key_name'):
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())); keypath.chmod(0o600)
        ec2.import_key_pair(KeyName=PROJECT, PublicKeyMaterial=key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH), TagSpecifications=[{'ResourceType':'key-pair','Tags':tags}])
        state['key_name'] = PROJECT; save(state)
    images = ec2.describe_images(Owners=['099720109477'], Filters=[{'Name':'name','Values':['ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*']},{'Name':'state','Values':['available']},{'Name':'architecture','Values':['x86_64']}])['Images']
    image = max(images, key=lambda im: im['CreationDate'])
    state.update({'image':image['ImageId'], 'subnet':subnet['SubnetId'], 'ssh_ip':ip}); save(state)
    userdata = '''#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv curl
mkdir -p /opt/dfsha-aj
chown ubuntu:ubuntu /opt/dfsha-aj
systemd-run --unit=dfsha-aj-autostop --on-active=3h /sbin/shutdown -h now
touch /opt/dfsha-aj/bootstrap.ok
'''
    for i in range(1,5):
        response = ec2.run_instances(ImageId=image['ImageId'], InstanceType='t3.small', MinCount=1, MaxCount=1,
            KeyName=state['key_name'], NetworkInterfaces=[{'DeviceIndex':0,'SubnetId':subnet['SubnetId'],'Groups':[state['sg']],'AssociatePublicIpAddress':True}],
            BlockDeviceMappings=[{'DeviceName':image['RootDeviceName'],'Ebs':{'VolumeSize':12,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':False}}],
            CreditSpecification={'CpuCredits':'standard'}, MetadataOptions={'HttpTokens':'required','HttpEndpoint':'enabled'},
            InstanceInitiatedShutdownBehavior='stop', UserData=userdata,
            ClientToken=PROJECT+'-vm'+str(i), TagSpecifications=[{'ResourceType':kind,'Tags':tags+[{'Key':'Name','Value':PROJECT+'-VM-'+str(i)}]} for kind in ('instance','volume')])
        ident = response['Instances'][0]['InstanceId']; state['instances'].append(ident); save(state)
        print('Creada VM', i, ident, flush=True)
    return state


def connect(node):
    # TOFU en registro exclusivo del proyecto; las conexiones posteriores exigen la misma clave.
    known = PRIVATE / 'ssh_known_hosts'
    client = paramiko.SSHClient()
    if known.exists(): client.load_host_keys(str(known))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    options=dict(username='ubuntu', key_filename=str(PRIVATE/'ssh_aws.pem'), timeout=8, banner_timeout=15, auth_timeout=15, look_for_keys=False, allow_agent=False)
    try:
        client.connect(node['PublicIpAddress'], **options)
    except (TimeoutError,OSError):
        state=json.loads(STATE.read_text())
        first=owned(clients(),state)[0]
        if node['InstanceId']==first['InstanceId']:raise
        bastion=connect(first)
        channel=bastion.get_transport().open_channel('direct-tcpip',(node['PrivateIpAddress'],22),('127.0.0.1',0),timeout=10)
        client.connect(node['PrivateIpAddress'],sock=channel,**options)
        client._dfsha_bastion=bastion
    client.save_host_keys(str(known)); known.chmod(0o600)
    return client


def run(client, command, timeout=600):
    _, out, err = client.exec_command(command, timeout=timeout)
    output = out.read().decode(errors='replace'); errors = err.read().decode(errors='replace')
    status = out.channel.recv_exit_status()
    if status: raise RuntimeError('Comando remoto falló: '+command[:80]+'\n'+output[-2000:]+errors[-2000:])
    return output


def wait_ready(node):
    for attempt in range(90):
        try:
            c = connect(node)
            run(c, 'test -f /opt/dfsha-aj/bootstrap.ok', timeout=10)
            c.close(); print('VM lista:', node['InstanceId'], flush=True); return
        except Exception:
            if attempt == 89: raise
            time.sleep(5)


def bundle():
    output = PRIVATE/'fuentes.tar.gz'
    with tarfile.open(output, 'w:gz') as tar:
        for folder in ('dfsha','scripts','tests'):
            for path in sorted((B/folder).rglob('*.py')): tar.add(path, arcname=str(path.relative_to(B)))
        tar.add(B/'requirements.txt', arcname='requirements.txt')
    return output


def stage_configs(nodes):
    staging = PRIVATE/'aws_stage'; staging.mkdir(exist_ok=True, mode=0o700)
    if not (staging/'.private/client.json').exists(): generate(staging, [n['PrivateIpAddress'] for n in nodes])
    return staging


def install_one(pair, nodes, staging, archive):
    idx, node = pair; c = connect(node); sftp = c.open_sftp()
    run(c, 'mkdir -p /opt/dfsha-aj/.private/certs /opt/dfsha-aj/runtime /opt/dfsha-aj/evidencias; chmod 700 /opt/dfsha-aj/.private')
    sftp.put(str(archive), REMOTE+'/fuentes.tar.gz')
    run(c, 'cd /opt/dfsha-aj && tar xzf fuentes.tar.gz && python3 -m venv .venv && .venv/bin/pip -q install -r requirements.txt')
    private = staging/'.private'; names = ['datos'+str(idx+1)]
    if idx < 3: names += ['control'+str(idx+1), 'etcd'+str(idx+1)]
    sftp.put(str(private/'certs/ca.pem'), REMOTE+'/.private/certs/ca.pem')
    for name in names:
        for ext in ('pem','key'):
            remote = REMOTE+'/.private/certs/'+name+'.'+ext
            sftp.put(str(private/'certs'/(name+'.'+ext)), remote); sftp.chmod(remote,0o600)
        if not name.startswith('etcd'):
            config=json.loads((private/(name+'.json')).read_text())
            for field in ('ca','node_ca','service_cert','service_key','storage'):
                config[field]=config[field].replace(str(staging),REMOTE)
            with sftp.open(REMOTE+'/.private/'+name+'.json','w') as f: f.write(json.dumps(config,indent=2))
            sftp.chmod(REMOTE+'/.private/'+name+'.json',0o600)
    if idx == 0:
        for file in ('usuarios.json','client.json'):
            value=(private/file).read_text().replace(str(staging),REMOTE)
            with sftp.open(REMOTE+'/.private/'+file,'w') as f: f.write(value)
            sftp.chmod(REMOTE+'/.private/'+file,0o600)
    if idx < 3:
        run(c, 'cd /tmp && curl -fsSL --retry 3 -o dfsha-etcd.tar.gz https://github.com/etcd-io/etcd/releases/download/v3.6.15/etcd-v3.6.15-linux-amd64.tar.gz && tar xzf dfsha-etcd.tar.gz && sudo install etcd-v3.6.15-linux-amd64/etcd /usr/local/bin/dfsha-etcd')
        name='etcd'+str(idx+1); ip=node['PrivateIpAddress']; cert=REMOTE+'/.private/certs/'
        initial=','.join('etcd'+str(i+1)+'=https://'+n['PrivateIpAddress']+':2380' for i,n in enumerate(nodes[:3]))
        command=['/usr/local/bin/dfsha-etcd','--name',name,'--data-dir',REMOTE+'/runtime/'+name,
                 '--initial-cluster-token',PROJECT,'--initial-cluster',initial,
                 '--listen-client-urls','https://0.0.0.0:2379','--advertise-client-urls','https://'+ip+':2379',
                 '--listen-peer-urls','https://0.0.0.0:2380','--initial-advertise-peer-urls','https://'+ip+':2380',
                 '--cert-file',cert+name+'.pem','--key-file',cert+name+'.key','--trusted-ca-file',cert+'ca.pem','--client-cert-auth',
                 '--peer-cert-file',cert+name+'.pem','--peer-key-file',cert+name+'.key','--peer-trusted-ca-file',cert+'ca.pem','--peer-client-cert-auth','--tls-min-version','TLS1.3']
        unit(c,sftp,'etcd',shlex.join(command))
    for name in names:
        if name.startswith('etcd'): continue
        unit(c,sftp,name,'/usr/bin/env DFSHA_CONFIG='+REMOTE+'/.private/'+name+'.json '+REMOTE+'/.venv/bin/python -m dfsha.main')
    sftp.close(); c.close(); print('Servicios instalados en VM',idx+1,flush=True)


def unit(c,sftp,name,command):
    text='''[Unit]
Description=DFSha Andres Juan NAME
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/dfsha-aj
ExecStart=COMMAND
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/dfsha-aj/runtime /opt/dfsha-aj/evidencias
[Install]
WantedBy=multi-user.target
'''.replace('NAME',name).replace('COMMAND',command)
    target=REMOTE+'/dfsha-aj-'+name+'.service'
    with sftp.open(target,'w') as f:f.write(text)
    run(c,'sudo install -m 644 '+target+' /etc/systemd/system/dfsha-aj-'+name+'.service && sudo systemctl daemon-reload && sudo systemctl enable --now dfsha-aj-'+name+'.service')


def deploy(ec2,state):
    ec2.get_waiter('instance_running').wait(InstanceIds=state['instances'],WaiterConfig={'Delay':5,'MaxAttempts':60})
    nodes=owned(ec2,state)
    with ThreadPoolExecutor(4) as pool: list(pool.map(wait_ready,nodes))
    staging=stage_configs(nodes); archive=bundle()
    with ThreadPoolExecutor(4) as pool: list(pool.map(lambda pair:install_one(pair,nodes,staging,archive),enumerate(nodes)))
    c=connect(nodes[0])
    for attempt in range(30):
        try:
            print(run(c,'cd /opt/dfsha-aj && .venv/bin/python scripts/usuarios.py'),flush=True);break
        except Exception:
            if attempt==29:raise
            time.sleep(3)
    c.close()
    state['nodes']=[{'id':n['InstanceId'],'private_ip':n['PrivateIpAddress'],'public_ip':n.get('PublicIpAddress'),'vm':i+1} for i,n in enumerate(nodes)]
    state['deployed_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());save(state)
    print('Despliegue real completado en cuatro VMs nuevas.',flush=True)


def stop(ec2,state):
    nodes=owned(ec2,state)
    ids=[n['InstanceId'] for n in nodes if n['State']['Name'] in ('running','pending')]
    if ids: ec2.stop_instances(InstanceIds=ids)
    print('Deteniendo exclusivamente máquinas registradas:',ids,flush=True)
    ec2.get_waiter('instance_stopped').wait(InstanceIds=state['instances'],WaiterConfig={'Delay':5,'MaxAttempts':60})
    state['stopped_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());save(state)
    print('Las cuatro VMs del proyecto están detenidas; discos conservados.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['deploy','stop','status']);args=parser.parse_args()
    ec2=clients()
    if args.action=='deploy':
        try:
            state=launch(ec2)
            deploy(ec2,state)
        except BaseException:
            if STATE.exists():
                partial=json.loads(STATE.read_text())
                if partial.get('instances'):stop(ec2,partial)
            raise
    else:
        state=json.loads(STATE.read_text())
        if args.action=='stop':stop(ec2,state)
        else:
            for node in owned(ec2,state): print(node['InstanceId'],node['State']['Name'],node.get('PrivateIpAddress'))
