"""Solo inicia y detiene los siete procesos de este proyecto."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

B=Path(__file__).resolve().parents[1]
runtime=B/'runtime';runtime.mkdir(exist_ok=True)
state=runtime/'procesos.json'
if sys.argv[1]=='start':
    if state.exists():raise SystemExit('Ya existe registro de procesos. Use stop antes de reiniciar.')
    pids={}
    for name in ['control1','control2','control3','datos1','datos2','datos3','datos4']:
        env={**os.environ,'DFSHA_CONFIG':str(B/'.private'/(name+'.json'))}
        log=(runtime/(name+'.log')).open('ab')
        process=subprocess.Popen([sys.executable,'-m','dfsha.main'],cwd=B,env=env,stdout=log,stderr=log,start_new_session=True)
        pids[name]=process.pid
    state.write_text(json.dumps(pids));print('Procesos independientes iniciados:',', '.join(pids))
elif sys.argv[1]=='stop':
    if not state.exists():raise SystemExit('No hay procesos registrados de este proyecto.')
    for name,pid in json.loads(state.read_text()).items():
        # Comprueba la identidad antes de enviar la señal, por si el PID fue reutilizado.
        command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
        if 'dfsha.main' in command and str(B/'.venv') in command:
            os.kill(pid,signal.SIGTERM);print(name,'detenido')
    state.unlink()
