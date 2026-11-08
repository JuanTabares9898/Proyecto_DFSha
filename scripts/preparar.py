#!/usr/bin/env python3
"""Genera configuración nueva y secretos exclusivos. No lee otros proyectos."""
import argparse
import datetime
import ipaddress
import json
import os
from pathlib import Path
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def secure_write(path, value):
    path.write_text(value)
    path.chmod(0o600)


def generate(base, hosts=None):
    private=base/'.private';private.mkdir(mode=0o700,exist_ok=True)
    if (private/'client.json').exists():
        raise SystemExit('La configuración ya existe. Se conserva: no se sobrescriben claves ni datos.')
    certs=private/'certs';certs.mkdir(mode=0o700,exist_ok=True)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    ca_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'DFSha AJ CA 20261108')])
    now=datetime.datetime.now(datetime.timezone.utc)
    ca=(x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
        .not_valid_after(now+datetime.timedelta(days=90)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .sign(ca_key,hashes.SHA256()))
    secure_write(certs/'ca.pem',ca.public_bytes(serialization.Encoding.PEM).decode())
    issuer=private/'issuer';issuer.mkdir(mode=0o700,exist_ok=True)
    secure_write(issuer/'ca.key',ca_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode())
    remote=hosts is not None
    addresses=hosts or ['127.0.0.1']*4
    control_ids=['control1','control2','control3']
    node_ids=['datos1','datos2','datos3','datos4']
    controls=[f'https://{addresses[i]}:{8443 if remote else 18443+i}' for i in range(3)]
    internal=[f'https://{addresses[i]}:{8444 if remote else 18543+i}' for i in range(3)]
    etcd=[f'https://{addresses[i]}:{2379 if remote else 23791+i}' for i in range(3)]
    nodes=[{'node_id':node_ids[i],'domain':'vm'+str(i+1),
            'url':f'https://{addresses[i]}:{9443 if remote else 19443+i}',
            'internal':f'https://{addresses[i]}:{9444 if remote else 19543+i}'} for i in range(4)]
    secrets_cfg={'auth_secret':secrets.token_urlsafe(48),'cap_secret':secrets.token_urlsafe(48),
                 'kek':__import__('base64').b64encode(os.urandom(32)).decode()}
    users={uid:secrets.token_urlsafe(18) for uid in ['andres','juan','pruebas']}
    secure_write(private/'usuarios.json',json.dumps(users,indent=2))
    def issue(name, extra=[]):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
        sans=[x509.DNSName('localhost'),x509.DNSName(name),x509.DNSName('dfsha-aj.internal')]
        sans += [x509.IPAddress(ipaddress.ip_address(a)) for a in set(addresses+['127.0.0.1'])]
        sans += [x509.DNSName(a) for a in extra]
        cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(ca_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
            .not_valid_after(now+datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName(sans),critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,ExtendedKeyUsageOID.CLIENT_AUTH]),critical=False)
            .sign(ca_key,hashes.SHA256()))
        secure_write(certs/(name+'.pem'),cert.public_bytes(serialization.Encoding.PEM).decode())
        secure_write(certs/(name+'.key'),key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode())
    for name in control_ids+node_ids+['etcd1','etcd2','etcd3']:issue(name)
    for i,nid in enumerate(control_ids+node_ids):
        is_control=nid in control_ids
        number=(control_ids if is_control else node_ids).index(nid)
        cfg={'role':'control' if is_control else 'data','node_id':nid,'block_size':4194304,
             'write_quorum':2,'replication':3,'nodes':nodes,'controls':controls,'controls_internal':internal,
             'control_ids':control_ids,'etcd':etcd,'ca':str(certs/'ca.pem'),'node_ca':str(certs/'ca.pem'),
             'service_cert':str(certs/(nid+'.pem')),'service_key':str(certs/(nid+'.key')),
             'cap_secret':secrets_cfg['cap_secret'],
             'port':(8443 if is_control else 9443) if remote else ((18443 if is_control else 19443)+number),
             'internal_port':(8444 if is_control else 9444) if remote else ((18543 if is_control else 19543)+number),
             'storage':str(base/'runtime'/nid)}
        if is_control:cfg.update({k:secrets_cfg[k] for k in ['auth_secret','kek']})
        secure_write(private/(nid+'.json'),json.dumps(cfg,indent=2))
    secure_write(private/'client.json',json.dumps({'ca':str(certs/'ca.pem'),'controls':controls},indent=2))
    compose={'name':'dfsha-aj','services':{},'networks':{'etcd':{}},'volumes':{}}
    initial=','.join(f'etcd{i}=https://etcd{i}:2380' for i in range(1,4))
    for i in range(1,4):
        name=f'etcd{i}'
        compose['services'][name]={
            'image':'gcr.io/etcd-development/etcd:v3.6.15','container_name':'dfsha-aj-'+name,
            'labels':{'Proyecto':'DFSha-AJ'},'networks':['etcd'],
            'ports':[f'127.0.0.1:{23790+i}:2379'],
            'volumes':[f'./.private/certs/ca.pem:/certs/ca.pem:ro',
                       f'./.private/certs/{name}.pem:/certs/{name}.pem:ro',
                       f'./.private/certs/{name}.key:/certs/{name}.key:ro',f'{name}:/etcd-data'],
            'command':['/usr/local/bin/etcd','--name',name,'--data-dir','/etcd-data',
                '--initial-cluster-token','dfsha-aj-20261108','--initial-cluster',initial,
                '--listen-client-urls','https://0.0.0.0:2379','--advertise-client-urls',f'https://{name}:2379',
                '--listen-peer-urls','https://0.0.0.0:2380','--initial-advertise-peer-urls',f'https://{name}:2380',
                '--cert-file',f'/certs/{name}.pem','--key-file',f'/certs/{name}.key',
                '--trusted-ca-file','/certs/ca.pem','--client-cert-auth',
                '--peer-cert-file',f'/certs/{name}.pem','--peer-key-file',f'/certs/{name}.key',
                '--peer-trusted-ca-file','/certs/ca.pem','--peer-client-cert-auth','--tls-min-version','TLS1.3']}
        compose['volumes'][name]={'labels':{'Proyecto':'DFSha-AJ'}}
    (base/'compose.local.json').write_text(json.dumps(compose,indent=2))
    print('Configuración y certificados nuevos generados. Contraseñas en .private/usuarios.json, no se imprimen.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--hosts',nargs=4)
    args=parser.parse_args()
    generate(Path(__file__).resolve().parents[1],args.hosts)
