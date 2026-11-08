"""CLI con comandos RF1 RF2 y consola por handles para RF3."""
import argparse
import getpass
import json
from pathlib import Path
import posixpath
import shlex

from .client import Client
from .common import Error


def main():
    parser=argparse.ArgumentParser(description='Cliente DFSha del equipo Andrés y Juan')
    parser.add_argument('--config',default='.private/client.json')
    parser.add_argument('--user',required=True)
    parser.add_argument('--credentials',help='Archivo privado de credenciales generado para el laboratorio')
    parser.add_argument('command',choices=['ls','cd','stat','mkdir','rmdir','rm','put','get','shell','status'])
    parser.add_argument('arguments',nargs='*')
    args=parser.parse_args()
    password=json.loads(Path(args.credentials).read_text())[args.user] if args.credentials else getpass.getpass('Contraseña: ')
    client=Client(json.loads(Path(args.config).read_text()),args.user,password)
    cwd='/'
    handles={}
    def execute(command,values):
        nonlocal cwd
        def route(value):return posixpath.normpath(posixpath.join(cwd,value))
        if command=='cd':
            new=route(values[0]);stat=client.fs('stat',new)
            if stat['type']!='dir':raise Error(400,'no es un directorio')
            cwd=new;return {'cwd':cwd}
        if command in ['ls','stat','mkdir','rmdir','rm']:
            return client.fs(command,route(values[0] if values else '.'))
        if command=='put':return client.put(values[0],route(values[1]))
        if command=='get':return client.get(route(values[0]),values[1])
        if command=='status':return client.call('/v1/status')
        if command=='open':
            h=client.open(route(values[0]),values[1] if len(values)>1 else 'r');handles[h.id]=h
            return {'handle':h.id,'version':h.info['version'],'size':h.size}
        if command=='read':return {'text':handles[values[0]].read(int(values[1]),int(values[2])).decode(errors='replace')}
        if command=='write':return {'bytes':handles[values[0]].write(int(values[1]),values[2].encode())}
        if command=='lock':return handles[values[0]].lock(int(values[1]),int(values[2]))
        if command=='unlock':handles[values[0]].unlock();return {'ok':True}
        if command=='renew':return handles[values[0]].renew(int(values[1]) if len(values)>1 else 30)
        if command=='close':
            result=handles[values[0]].close();handles.pop(values[0]);return result
        if command=='abort':handles.pop(values[0]).abort();return {'ok':True}
        raise Error(400,'comando desconocido')
    if args.command=='shell':
        print('ls cd mkdir rmdir rm put get open read write lock renew unlock close abort status exit')
        try:
            while True:
                try:parts=shlex.split(input('dfsha:'+cwd+'> '))
                except EOFError:break
                if not parts:continue
                if parts[0]=='exit':break
                try:print(json.dumps(execute(parts[0],parts[1:]),ensure_ascii=False,indent=2))
                except (Error,KeyError,IndexError,ValueError) as exc:print('Error:',exc)
        finally:
            for h in handles.values():h.abort()
    else:print(json.dumps(execute(args.command,args.arguments),ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Error as exc:raise SystemExit(str(exc))
