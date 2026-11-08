"""Inventario de solo lectura usando exclusivamente la sesión temporal de Andrés."""
import configparser
import json
from pathlib import Path
import boto3

B=Path(__file__).resolve().parents[1]
def session():
    auth=configparser.ConfigParser();auth.read(B/'.private/aws.ini')
    return boto3.Session(aws_access_key_id=auth['default']['aws_access_key_id'],
                         aws_secret_access_key=auth['default']['aws_secret_access_key'],
                         aws_session_token=auth['default']['aws_session_token'],region_name='us-east-1')

if __name__=='__main__':
    s=session();identity=s.client('sts').get_caller_identity()
    ec2=s.client('ec2')
    instances=[i for r in ec2.describe_instances()['Reservations'] for i in r['Instances']]
    data={'identity':identity,'instances':instances,'volumes':ec2.describe_volumes()['Volumes'],
          'vpcs':ec2.describe_vpcs()['Vpcs'],'subnets':ec2.describe_subnets()['Subnets']}
    (B/'.private/inventario_inicial.json').write_text(json.dumps(data,default=str,indent=2))
    print('Cuenta verificada:',identity['Account'])
    for i in instances:
        name=next((t['Value'] for t in i.get('Tags',[]) if t['Key']=='Name'),'(sin nombre)')
        print(i['InstanceId'],name,i['State']['Name'],i['InstanceType'])
    print('Discos existentes:',len(data['volumes']))
    print('No se realizó ninguna modificación.')
