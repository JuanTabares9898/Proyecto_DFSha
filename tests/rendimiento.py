"""Cinco pares de carga/descarga por concurrencia con verificación SHA-256."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dfsha.client import Client

B=Path(__file__).resolve().parents[1]
cfg=json.loads((B/'.private/client.json').read_text());pw=json.loads((B/'.private/usuarios.json').read_text())
c=Client(cfg,'andres',pw['andres'])
out=B/'evidencias';files=out/'archivos';files.mkdir(exist_ok=True,parents=True)
size_mib=int(os.environ.get('DFSHA_MIB','128'));source=files/'benchmark.bin'
with source.open('wb') as f:
    for _ in range(size_mib):f.write(os.urandom(1048576))
sha=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()
root='/rendimiento'+str(time.time_ns());c.fs('mkdir',root)
rows=[]
for parallel in (1,4):
    for rep in range(1,6):
        target=root+'/p'+str(parallel)+'r'+str(rep)+'.bin'
        start=time.perf_counter();c.put(source,target,parallel=parallel);upload=time.perf_counter()-start
        start=time.perf_counter();c.get(target,files/'benchmark_recuperado.bin',parallel=parallel);download=time.perf_counter()-start
        received=hashlib.file_digest((files/'benchmark_recuperado.bin').open('rb'),'sha256').hexdigest();assert received==sha
        row={'paralelismo':parallel,'repeticion':rep,'bytes':source.stat().st_size,'put_s':upload,'get_s':download,
             'put_MiB_s':size_mib/upload,'get_MiB_s':size_mib/download,'sha256':sha,'hash_correcto':True}
        rows.append(row);print(json.dumps(row),flush=True)
        c.fs('rm',target)
        result={'entorno':os.environ.get('DFSHA_ENTORNO','local'),'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'muestras':rows,
                'resumen':[{ 'paralelismo':p, 'put_mediana_s':statistics.median(r['put_s'] for r in rows if r['paralelismo']==p),
                 'get_mediana_s':statistics.median(r['get_s'] for r in rows if r['paralelismo']==p)} for p in (1,4) if any(r['paralelismo']==p for r in rows)]}
        (out/('rendimiento_'+os.environ.get('DFSHA_ENTORNO','local')+'.json')).write_text(json.dumps(result,indent=2))
c.fs('rmdir',root)
print('MEDICIONES COMPLETAS',flush=True)
