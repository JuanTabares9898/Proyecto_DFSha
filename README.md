# DFSha de Andrés Arroyave y Juan Jerónimo Tabares

Prototipo de sistema de archivos distribuido de la Fase II del Proyecto 1 de Sistemas Distribuidos 2026-2. Se desarrolla a partir de los dos diseños individuales suministrados, sin utilizar los archivos ni recursos de otros equipos.

Integrantes: Andrés Arroyave Cardona (000499730) y Juan Jerónimo Tabares Cataño (000214226).

## Qué contiene

- Cliente CLI y SDK Python: directorios, carga y descarga, handles, lectura, escritura por offset, append, bloqueos por rango de bloques, renovación y cierre.
- Tres controles sin metadatos locales, tres miembros etcd con Raft y cuatro nodos de datos. En AWS se distribuyen en cuatro VMs nuevas; no es una simulación monolítica.
- Bloques inmutables de 4 MiB, cifrados en el cliente con AES-256-GCM y replicados en tres dominios distintos. Una escritura exige dos copias durables y publicación transaccional de metadatos.
- Pruebas ejecutables y registros JSON de pruebas reales. Los archivos originales grandes no se empaquetan: las pruebas los generan.
- Informe, decisiones arquitectónicas, referencia de API y guion de demostración en `docs/`.

El repositorio del equipo, la revisión de aportes y la grabación del video se completan con los integrantes. No se crearon commits ni se publicó en otro repositorio.

## Ejecución local en Mac o Linux

Necesita Python 3.12, Docker con Compose y puertos locales libres. Ejecute desde esta carpeta:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/preparar.py
docker compose -f compose.local.json up -d
.venv/bin/python scripts/usuarios.py
.venv/bin/python scripts/servicios_locales.py start
.venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json status
.venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json shell
```

`preparar.py` se ejecuta una sola vez; si ya existe configuración la conserva y se niega a sobrescribir secretos. Espere unos segundos a la formación de etcd antes de registrar usuarios. Si ese paso falla temporalmente, repita únicamente `usuarios.py`.

Las contraseñas aleatorias quedan en `.private/usuarios.json`, con permisos restrictivos. Nunca publique esa carpeta. El cliente también permite omitir `--credentials` y solicita la contraseña sin mostrarla.

En la consola interactiva:

```text
mkdir /prueba
cd /prueba
put /ruta/local/archivo.txt datos.txt
ls
get datos.txt /ruta/local/recuperado.txt
open datos.txt rw
```

Copie el identificador devuelto por `open` en los siguientes comandos, sustituyendo HANDLE:

```text
read HANDLE 0 30
lock HANDLE 0 0
write HANDLE 0 "texto nuevo"
renew HANDLE 30
close HANDLE
rm datos.txt
cd /
rmdir /prueba
exit
```

El rango de `lock` se expresa en índices de bloque inclusivos, no en bytes. `close` confirma cambios; `abort` los descarta. `exit` descarta los handles aún abiertos, por lo que debe confirmar antes de salir. Consulte `docs/API.md` para la semántica exacta.

## Pruebas reproducibles

```bash
.venv/bin/python tests/funcional.py
.venv/bin/python tests/fallos_locales.py
DFSHA_MIB=128 .venv/bin/python tests/rendimiento.py
```

Las pruebas de fallos locales detienen y recuperan exclusivamente procesos identificados en `runtime/procesos.json` y los contenedores `dfsha-aj-etcd1`, `dfsha-aj-etcd2` y `dfsha-aj-etcd3`. No las ejecute contra un montaje de otro proyecto. Los JSON de `evidencias/` se actualizan con cada corrida; preserve una copia si desea conservar la referencia anterior.

Para detener únicamente este entorno sin borrar datos:

```bash
.venv/bin/python scripts/servicios_locales.py stop
docker compose -f compose.local.json stop
```

No use borrado de volúmenes para apagarlo. Reiniciar conserva archivos y metadatos.

## AWS Academy

El despliegue realizado usa una sesión temporal de la cuenta autorizada de Andrés, región us-east-1, y la etiqueta exclusiva `Proyecto=DFSha-AJ-20261108`. Los recursos previos no se adoptan, modifican ni eliminan. Consulte el inventario entregado en las evidencias antes de actuar.

Instale las herramientas de administración solo en el equipo desde el que se despliega:

```bash
.venv/bin/pip install -r requirements-admin.txt
```

Para reproducir un despliegue NUEVO se necesita una sesión temporal válida guardada localmente en `.private/aws.ini`, formato de perfil `[default]` con `aws_access_key_id`, `aws_secret_access_key` y `aws_session_token`. No se incluye en la entrega. No se deben copiar credenciales a los nodos. `scripts/desplegar_aws.py` comprueba expresamente la cuenta de Andrés; adaptarlo a otra cuenta exige revisar también presupuesto y red. Crear recursos nuevos tiene costo y requiere autorización del titular.

```bash
.venv/bin/python scripts/aws_inventory.py
.venv/bin/python scripts/desplegar_aws.py deploy
.venv/bin/python scripts/pruebas_aws.py
.venv/bin/python scripts/desplegar_aws.py stop
```

El registro privado `.private/aws_recursos.json` identifica las únicas instancias que el administrador puede detener. `deploy` conserva las existentes y no sustituye certificados, pero no arranca automáticamente un despliegue detenido. Para reanudar, inicie exclusivamente las cuatro IDs registradas en la consola de EC2 y consulte de nuevo sus IP públicas. La IP privada y los discos se conservan mientras existan las instancias; los certificados originales siguen asociados a esas IP privadas.

En cada VM el proyecto está en `/opt/dfsha-aj`. Los servicios de systemd son `dfsha-aj-etcd`, `dfsha-aj-controlN` y `dfsha-aj-datosN`, según la topología. El grupo de seguridad permite los puertos de aplicación solo entre sus propios miembros; SSH se restringe al equipo administrador y a las VMs del grupo. No hay una web pública ni se necesita para esta práctica. Desde VM1:

```bash
cd /opt/dfsha-aj
.venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json shell
```

El temporizador de seguridad creado en el arranque inicial detiene cada VM después de tres horas; no es un límite de facturación ni se rearma automáticamente con cada reinicio. Apáguelas manualmente al finalizar. Los volúmenes conservados siguen generando almacenamiento facturable aun con las VMs detenidas.

## Límites que deben conocer

Es un prototipo académico, no un servicio de producción. No se implementan FUSE, compatibilidad POSIX completa, directorios compartidos, agujeros dispersos ni tolerancia bizantina. Los nodos autorizados y administradores se consideran confiables. Los snapshots y bloques antiguos se conservan y aún no hay recolección de basura; borrar un archivo retira su nombre, no elimina inmediatamente todas sus versiones cifradas del disco. El espacio del namespace y las versiones por archivo están limitados por el tamaño máximo de una transacción/valor de etcd; no se promete capacidad ilimitada.

Los tokens de usuario vencen a los 15 minutos; se vuelve a iniciar sesión. Los handles vencen a una hora y los locks tienen TTL de 5 a 300 segundos; solo los locks se renuevan explícitamente. Las capacidades de lectura duran cinco minutos y el SDK actualiza el manifiesto cuando es necesario. Los certificados de nodos se generan para 30 días y deben renovarse antes de caducar. La CA privada nunca se despliega ni se entrega dentro del ZIP.

La semántica acordada debe ser revisada por los integrantes y explicada al profesor. No se presume una aprobación docente de exclusiones o simplificaciones que no haya sido obtenida realmente.
