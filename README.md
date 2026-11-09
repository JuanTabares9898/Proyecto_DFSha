# DFSha

**Un sistema de archivos distribuido, cifrado y tolerante a fallos, construido desde cero en Python.**

DFSha guarda tus archivos repartidos en varias máquinas. Cada archivo se divide en bloques, se cifra en el cliente, se replica en tres nodos distintos y se publica de forma transaccional. Si una máquina completa se cae, los archivos siguen disponibles y el sistema se repara solo.

Es un prototipo académico de la Fase II del Proyecto 1 de Sistemas Distribuidos (2026-2), construido por:

- Andrés Arroyave Cardona (000499730)
- Juan Jerónimo Tabares Cataño (000214226)

---

## Tabla de contenido

1. [Qué problema resuelve](#qué-problema-resuelve)
2. [Características principales](#características-principales)
3. [Arquitectura](#arquitectura)
4. [Cómo funciona por dentro](#cómo-funciona-por-dentro)
5. [Inicio rápido (local)](#inicio-rápido-local)
6. [Usar el cliente](#usar-el-cliente)
7. [Pruebas](#pruebas)
8. [Despliegue en AWS](#despliegue-en-aws)
9. [Estructura del repositorio](#estructura-del-repositorio)
10. [Decisiones de diseño](#decisiones-de-diseño)
11. [Límites conocidos](#límites-conocidos)
12. [Estado del proyecto](#estado-del-proyecto)

---

## Qué problema resuelve

Un solo servidor tiene un límite de espacio y es un punto único de fallo. DFSha reparte los datos entre varios servidores y responde tres preguntas:

- **¿Dónde se guarda un archivo grande?** Se parte en bloques de 4 MiB distribuidos entre varios nodos de datos.
- **¿Qué pasa si un servidor muere?** Cada bloque tiene tres copias en tres dominios de fallo distintos, y el sistema reconstruye las que se pierdan.
- **¿Qué pasa si dos personas editan el mismo archivo a la vez?** Hay control de concurrencia con versiones, locks por rango y detección de conflictos.

---

## Características principales

| Área | Qué ofrece |
|---|---|
| **Operaciones** | Directorios, carga y descarga, lectura por offset, escritura por offset, append, locks por rango, renovación y cierre. Todo desde un CLI y un SDK en Python. |
| **Almacenamiento** | Bloques inmutables de 4 MiB (configurable), replicados en 3 dominios distintos. |
| **Durabilidad** | Una escritura se confirma cuando hay **2 copias durables** (W2). La tercera se completa después. |
| **Consistencia** | Los metadatos viven en un clúster **etcd con Raft**. Los cambios se publican en una sola transacción. |
| **Concurrencia** | Snapshots por apertura, CAS por bloque o por archivo, locks con lease real y token de *fencing*. |
| **Seguridad** | Cifrado **AES-256-GCM en el cliente**, TLS 1.3 público, mTLS interno, contraseñas con Argon2id y tokens de corta vida. |
| **Tolerancia a fallos** | Se probó la caída de una VM completa, la pérdida de mayoría en etcd y la reparación automática de réplicas. |

---

## Arquitectura

El sistema tiene tres piezas:

- **Controles (3):** son la puerta de entrada. Autentican usuarios, planifican en qué nodos van los bloques, validan recibos y publican las nuevas versiones. No guardan metadatos en disco local.
- **etcd (3 miembros, Raft):** guarda el namespace, las versiones, las ubicaciones de los bloques y los locks. Tolera la caída de un miembro.
- **Nodos de datos (4):** guardan únicamente bloques **cifrados**. Nunca ven el contenido en claro.

En AWS se despliegan en **cuatro VMs reales**, cada una con su propio dominio de fallo:

```text
                          ┌──────────────┐
                          │   Cliente    │
                          │  CLI / SDK   │
                          │  (cifra AES) │
                          └──────┬───────┘
                 HTTPS (TLS 1.3) │ metadatos        bloques cifrados
              ┌──────────────────┼──────────────────────────┐
              ▼                  ▼                          ▼
   ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
   │      VM1       │ │      VM2       │ │      VM3       │ │      VM4       │
   │ Control 1      │ │ Control 2      │ │ Control 3      │ │                │
   │ etcd 1         │ │ etcd 2         │ │ etcd 3         │ │                │
   │ Nodo datos 1   │ │ Nodo datos 2   │ │ Nodo datos 3   │ │ Nodo datos 4   │
   └────────────────┘ └────────────────┘ └────────────────┘ └────────────────┘
        ▲  mTLS interno: heartbeats, recibos, réplicas y reparación  ▲
```

El diagrama completo está en [`evidencias/arquitectura.png`](evidencias/arquitectura.png).

---

## Cómo funciona por dentro

### Escribir un archivo

1. El cliente abre el archivo (`open`) y el control le entrega un **snapshot** y un **handle**.
2. El cliente divide los datos en bloques de 4 MiB y cifra cada uno con AES-256-GCM.
3. El control reserva destinos para los bloques (`plan`), eligiendo tres dominios por bloque con *rendezvous hashing*.
4. El cliente sube cada bloque cifrado a los nodos de datos. Cada nodo lo guarda con archivo temporal, `fsync` y enlace atómico, y devuelve un **recibo**.
5. Al cerrar (`close`), el control verifica que haya **al menos dos recibos correctos** por bloque y publica en **una sola transacción de etcd** el namespace, la versión, el handle y las ubicaciones.

El punto de confirmación es la transacción de publicación. Subir bloques sueltos no publica nada.

### Modificar parte de un archivo

La escritura por offset usa **copy-on-write**: solo se reescriben los bloques tocados y los demás se reutilizan. Las aperturas anteriores siguen viendo su versión hasta que se cierran o vencen.

### Concurrencia

- Dos escrituras sobre el **mismo bloque**: una gana y la otra recibe **409**.
- Escrituras sobre **bloques distintos**: pueden fusionarse, siempre que no cambien el tamaño de forma incompatible.
- **Locks por rango** (shared o exclusive), expresados en índices de bloque inclusivos. Cada lock tiene lease de etcd y token de fencing, y renovarlo cambia el fencing. Un cliente con el lease vencido no puede reactivar su lock.

### Fallos y reparación

- Los nodos de datos envían *heartbeats* con lease, y así el control sabe cuáles están vivos.
- Un trabajador elegido por lease revisa las réplicas y reconstruye las copias faltantes desde una réplica sana.
- Sin mayoría en etcd, las operaciones nuevas se rechazan con **503** en lugar de arriesgar inconsistencias.

### Seguridad

- Cada bloque se cifra en el cliente con nonce aleatorio y datos asociados (propietario, archivo e índice).
- La clave de cada archivo se guarda **envuelta** con una clave compartida por los controles.
- Las capacidades de acceso a bloques están limitadas a un bloque y una operación, y duran cinco minutos.
- Los nodos internos se autentican con certificados (mTLS). La CA privada nunca se despliega en las VMs.

---

## Inicio rápido (local)

**Requisitos:** Python 3.12, Docker con Compose y puertos locales libres. Funciona en Mac o Linux.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/preparar.py
docker compose -f compose.local.json up -d
.venv/bin/python scripts/usuarios.py
.venv/bin/python scripts/servicios_locales.py start
.venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json status
```

Notas:

- `preparar.py` se ejecuta **una sola vez**. Si ya existe configuración, la conserva y no sobrescribe secretos.
- Espera unos segundos a que etcd forme el clúster antes de registrar usuarios. Si `usuarios.py` falla de forma temporal, repite solo ese paso.
- Las contraseñas aleatorias quedan en `.private/usuarios.json`, con permisos restrictivos. **Nunca publiques esa carpeta.** También puedes omitir `--credentials` y el cliente pedirá la contraseña sin mostrarla.

**Para apagar el entorno sin borrar datos:**

```bash
.venv/bin/python scripts/servicios_locales.py stop
docker compose -f compose.local.json stop
```

No uses borrado de volúmenes para apagarlo. Reiniciar conserva archivos y metadatos.

---

## Usar el cliente

Abre la consola interactiva:

```bash
.venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json shell
```

### Ejemplo básico

```text
mkdir /prueba
cd /prueba
put /ruta/local/archivo.txt datos.txt
ls
get datos.txt /ruta/local/recuperado.txt
open datos.txt rw
```

`open` devuelve un identificador de handle. Úsalo en lugar de `HANDLE`:

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

### Comandos

| Comando | Qué hace |
|---|---|
| `mkdir`, `rmdir`, `cd`, `ls` | Gestionan directorios. |
| `put LOCAL REMOTO` | Sube un archivo y lo reemplaza completo, solo al confirmar. |
| `get REMOTO LOCAL` | Descarga a un temporal y reemplaza el destino al terminar bien. |
| `open RUTA MODO` | Abre un handle en modo `r`, `rw`, `w`, `create` o `a` (append). |
| `read HANDLE OFFSET LONGITUD` | Lee bytes del snapshot, incluyendo tus cambios pendientes. |
| `write HANDLE OFFSET DATOS` | Escribe con copy-on-write. |
| `lock HANDLE INICIO FIN` | Bloquea un rango de **bloques** (índices inclusivos, no bytes). |
| `renew HANDLE TTL` | Renueva un lock vigente. |
| `close HANDLE` | Publica los cambios si los hay. |
| `abort HANDLE` | Descarta los cambios del handle. |
| `rm` | Quita el nombre de un archivo. |

Algunas reglas que conviene tener presentes:

- `exit` descarta los handles que sigan abiertos. Cierra o haz `abort` antes de salir.
- No se pueden escribir offsets mayores al tamaño actual, porque no hay agujeros dispersos.
- En modo `a`, los datos siempre se agregan al final, aunque indiques otro offset.
- Un **409** significa que otra persona publicó antes. Reabre el archivo y rehaz el cambio sobre la nueva versión.

La referencia completa de rutas, errores y semántica está en [`docs/API.md`](docs/API.md).

---

## Pruebas

```bash
.venv/bin/python tests/funcional.py
.venv/bin/python tests/fallos_locales.py
DFSHA_MIB=128 .venv/bin/python tests/rendimiento.py
```

| Prueba | Qué verifica |
|---|---|
| `funcional.py` | Operaciones de archivos, handles, locks y concurrencia de dos clientes. |
| `fallos_locales.py` | Caída y recuperación de procesos locales y de contenedores etcd. |
| `rendimiento.py` | Subida y descarga de archivos grandes, con comprobación de hash. |

Los archivos grandes no se incluyen en el repositorio: las pruebas los generan.

Las pruebas de fallos solo detienen procesos listados en `runtime/procesos.json` y los contenedores `dfsha-aj-etcd1`, `dfsha-aj-etcd2` y `dfsha-aj-etcd3`. **No las ejecutes sobre el montaje de otro proyecto.**

Los JSON de [`evidencias/`](evidencias/) se actualizan en cada corrida. Guarda una copia si quieres conservar la referencia anterior.

### Resultados en AWS

En las pruebas reales con cuatro VMs:

- Con una VM completa apagada, el archivo se recuperó con el mismo SHA-256 y se confirmó una carga nueva.
- Los bloques se re-replicaron automáticamente entre las VMs restantes.
- Sin mayoría de etcd, las escrituras se rechazaron con 503.
- Con archivos de 128 MiB, la subida rondó los 22-24 MiB/s con un hilo y cerca de 40 MiB/s con paralelismo 4. La bajada estuvo entre 46 y 68 MiB/s.

Estas cifras son mediciones de una corrida concreta y no garantías. Los datos completos están en `evidencias/rendimiento_aws.json` y `evidencias/fallos_aws.json`.

---

## Despliegue en AWS

El despliegue realizado usa una sesión temporal de AWS Academy, región `us-east-1`, con la etiqueta exclusiva `Proyecto=DFSha-AJ-20261108`. Los recursos previos no se adoptan, modifican ni eliminan.

```bash
.venv/bin/pip install -r requirements-admin.txt
.venv/bin/python scripts/aws_inventory.py
.venv/bin/python scripts/desplegar_aws.py deploy
.venv/bin/python scripts/pruebas_aws.py
.venv/bin/python scripts/desplegar_aws.py stop
```

**Antes de desplegar:**

- Necesitas una sesión temporal válida en `.private/aws.ini`, con formato de perfil `[default]` y los campos `aws_access_key_id`, `aws_secret_access_key` y `aws_session_token`. Ese archivo no se incluye en la entrega.
- No copies credenciales a los nodos.
- `desplegar_aws.py` comprueba expresamente la cuenta de Andrés. Adaptarlo a otra cuenta exige revisar también presupuesto y red.
- Crear recursos tiene costo y requiere autorización de quien administra la cuenta.

**Operación:**

- `.private/aws_recursos.json` identifica las únicas instancias que el administrador puede detener.
- `deploy` conserva las instancias existentes y no sustituye certificados, pero **no reanuda** un despliegue detenido. Para reanudar, inicia solo las cuatro IDs registradas desde la consola de EC2 y consulta de nuevo sus IP públicas. La IP privada y los discos se conservan mientras existan las instancias.
- Cada VM tiene un temporizador de seguridad que la detiene a las tres horas. No es un límite de facturación y no se rearma al reiniciar. **Apaga las VMs manualmente al terminar.**
- Los volúmenes conservados siguen generando costo de almacenamiento aunque las VMs estén detenidas.

**Dentro de las VMs:**

- El proyecto está en `/opt/dfsha-aj`.
- Los servicios de systemd son `dfsha-aj-etcd`, `dfsha-aj-controlN` y `dfsha-aj-datosN`, según la topología.
- El grupo de seguridad permite los puertos de aplicación solo entre sus miembros. SSH se restringe al equipo administrador y a las VMs del grupo. No hay web pública.

Para usar el cliente desde VM1:

```bash
cd /opt/dfsha-aj
.venv/bin/python -m dfsha.cli --user andres --credentials .private/usuarios.json shell
```

---

## Estructura del repositorio

```text
DFSha_Andres_Juan/
├── dfsha/                  Código principal
│   ├── cli.py              Consola interactiva y comandos
│   ├── client.py           SDK del cliente (cifrado, bloques, handles)
│   ├── control.py          Servicio de control (metadatos, locks, commit)
│   ├── data.py             Nodo de datos (almacenamiento y reparación)
│   ├── httpserver.py       Servidor HTTP
│   ├── common.py           TLS, tokens y acceso a etcd
│   └── main.py             Punto de entrada de los servicios
├── scripts/                Preparación, servicios locales, despliegue y cierre en AWS,
│                           generación del informe y empaquetado
├── tests/                  Pruebas funcionales, de fallos y de rendimiento
├── docs/                   API, decisiones, aportes, uso de IA, guion de demo e informe
├── evidencias/             Resultados reales: JSON, logs, capturas y diagrama
├── compose.local.json      etcd local con Docker Compose
└── MANIFIESTO_SHA256.json  Huellas de los archivos entregados
```

---

## Decisiones de diseño

El resumen de las principales decisiones. El detalle y la comparación entre los dos diseños originales están en [`docs/DECISIONES.md`](docs/DECISIONES.md).

| Decisión | Elección | Motivo |
|---|---|---|
| Tamaño de bloque | 4 MiB, configurable | Menos memoria por operación y particionamiento visible con archivos de 64 y 128 MiB. |
| Protocolo | HTTPS con JSON para control y binario para bloques | Un solo protocolo para los tres planos, más simple de depurar y de revisar en las evidencias. |
| Publicación | Transacción única en etcd tras validar recibos | Un archivo nunca queda publicado a medias. |
| Cifrado | En el cliente, con AES-256-GCM | Los nodos de datos nunca ven contenido en claro. |
| Replicación | R3 con confirmación W2 | Equilibrio entre durabilidad y disponibilidad de escritura. |
| Placement | *Rendezvous hashing* sobre dominios distintos | Reparto uniforme y cambios mínimos cuando entran o salen nodos. |
| Topología | 4 VMs: 3 con control, etcd y datos, y 1 solo de datos | Dominios de fallo reales con costo bajo. |

---

## Límites conocidos

DFSha es un prototipo y no un servicio de producción.

**Funcionalidad que no incluye:**

- FUSE ni compatibilidad POSIX completa.
- Directorios compartidos entre usuarios.
- Agujeros dispersos (*sparse files*).
- Tolerancia bizantina. Los nodos autorizados y los administradores se consideran confiables.

**Limitaciones de operación:**

- **No hay recolección de basura.** Los snapshots y los bloques antiguos se conservan, y borrar un archivo solo retira su nombre.
- El tamaño del namespace y el número de versiones por archivo están acotados por el tamaño máximo de una transacción de etcd.
- Con W2, una escritura reciente puede perderse si fallan dos nodos a la vez antes de completarse la tercera réplica.
- Los conexiones HTTPS no usan un pool persistente.

**Vencimientos:**

| Elemento | Duración |
|---|---|
| Token de usuario | 15 minutos |
| Handle | 1 hora |
| Lock | TTL de 5 a 300 segundos, renovable |
| Capacidad de lectura | 5 minutos (el SDK refresca el manifiesto) |
| Certificado de nodo | 30 días |

---

## Estado del proyecto

- El código, las pruebas y las evidencias de ejecución real están en este repositorio.
- Pendiente por parte del equipo: publicar el repositorio y revisar los aportes de cada integrante (ver [`docs/APORTES.md`](docs/APORTES.md)), y grabar el video de demostración (hay un guion en [`docs/GUIA_VIDEO.md`](docs/GUIA_VIDEO.md)).
- Las decisiones de alcance están documentadas en [`docs/DECISIONES.md`](docs/DECISIONES.md). Las exclusiones o simplificaciones que requieran aprobación docente deben confirmarse con el profesor y no darse por aprobadas.
- El uso de herramientas de IA está declarado en [`docs/USO_IA.md`](docs/USO_IA.md).

## Documentación adicional

- [`docs/API.md`](docs/API.md): rutas, semántica, errores y consistencia.
- [`docs/DECISIONES.md`](docs/DECISIONES.md): arquitectura y decisiones.
- [`docs/GUIA_VIDEO.md`](docs/GUIA_VIDEO.md): guion de la demostración.
- [`docs/APORTES.md`](docs/APORTES.md) y [`docs/USO_IA.md`](docs/USO_IA.md): contribuciones y declaración de IA.
