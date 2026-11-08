"""Entrega por lista permitida, comprobación de integridad y exclusión de secretos."""
import configparser
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

B=Path(__file__).resolve().parents[1]
DOWNLOADS=Path('/Users/lifenzito/Downloads')
name='DFSha_FaseII_Andres_Arroyave_Juan_Tabares_20261108'
files=[B/'.gitignore',B/'README.md',B/'compose.local.json',*sorted(B.glob('requirements*.txt'))]
for folder in ('dfsha','scripts','tests'):
    files.extend(p for p in sorted((B/folder).rglob('*.py')) if p.name!='recibir_sesion_local.py')
files.extend(p for p in sorted((B/'docs').iterdir()) if p.suffix in ('.md','.docx'))
files.extend(p for p in sorted((B/'evidencias').iterdir()) if p.is_file() and p.suffix in ('.json','.png','.txt','.log'))
assert all(p.is_file() and '.private' not in p.relative_to(B).parts for p in files)
secrets=[]
for root in (B/'.private',B/'.private/aws_stage/.private'):
    for cfg in root.glob('*.json'):
        value=json.loads(cfg.read_text())
        if cfg.name=='usuarios.json':secrets.extend(v.encode() for v in value.values())
        for key in ('auth_secret','cap_secret','kek'):
            if key in value:secrets.append(value[key].encode())
credentials=configparser.ConfigParser();credentials.read(B/'.private/aws.ini')
if credentials.has_section('default'):secrets.extend(v.encode() for _,v in credentials.items('default'))
def scan(payload,label):
    assert not re.search(rb'-----BEGIN (?:RSA )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]{40}',payload),label
    assert not any(secret in payload for secret in secrets if len(secret)>=12),'Secreto detectado: '+label
for file in files:
    payload=file.read_bytes();scan(payload,str(file.relative_to(B)))
    if file.suffix=='.docx':
        with zipfile.ZipFile(file) as z:
            assert z.testzip() is None
            for item in z.namelist():scan(z.read(item),'docx/'+item)
manifest={'algoritmo':'SHA-256','archivos':{str(p.relative_to(B)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
          'exclusiones':['.private y todas las credenciales','runtime y discos del servicio','.venv y caches','archivos grandes generados para las pruebas','helper temporal de sesión AWS'],
          'validacion':'22 pruebas funcionales AWS, 5 escenarios de fallos AWS y 10 pares de rendimiento; revisar JSON originales'}
manifest_path=B/'MANIFIESTO_SHA256.json';manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
files.append(manifest_path)
target=DOWNLOADS/(name+'.zip')
assert not target.exists(),'Ya existe el ZIP de entrega; no se sobrescribe automáticamente.'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
    for file in files:z.write(file,arcname='DFSha_Andres_Juan/'+str(file.relative_to(B)))
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
    assert len(z.namelist())==len(files)
    for item in z.infolist():
        assert not Path(item.filename).is_absolute() and '..' not in Path(item.filename).parts
        assert '.private' not in Path(item.filename).parts and '/runtime/' not in item.filename
        if item.filename.endswith('.py'):compile(z.read(item).decode(),item.filename,'exec')
    for path,expected in manifest['archivos'].items():
        assert hashlib.sha256(z.read('DFSha_Andres_Juan/'+path)).hexdigest()==expected
word_source=B/'docs/DFSha_FaseII_Andres_Arroyave_Juan_Tabares.docx'
word_target=DOWNLOADS/(name+'.docx')
assert not word_target.exists(),'Ya existe el Word de entrega; no se sobrescribe automáticamente.'
shutil.copy2(word_source,word_target)
print(json.dumps({'zip':str(target),'word':str(word_target),'archivos':len(files),'zip_bytes':target.stat().st_size,'integridad':'correcta','secretos':'no incluidos'},ensure_ascii=False,indent=2))
