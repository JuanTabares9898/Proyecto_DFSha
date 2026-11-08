"""Registra las cuentas del equipo, almacenando únicamente hashes Argon2id."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from argon2 import PasswordHasher
from dfsha.common import Etcd

b=Path(__file__).resolve().parents[1]
config=json.loads((b/'.private/control1.json').read_text())
db=Etcd(config);hasher=PasswordHasher()
for user,password in json.loads((b/'.private/usuarios.json').read_text()).items():
    success=db.cas('users/'+user,0,{'hash':hasher.hash(password)})['succeeded']
    print(user,'creado' if success else 'conservado')
