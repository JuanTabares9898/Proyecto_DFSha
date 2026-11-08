# API y semántica del prototipo

Todas las rutas públicas requieren TLS 1.3. Excepto login, llevan `Authorization: Bearer TOKEN`. Cada apertura devuelve un handle privado y un snapshot de bloques; nunca una lista fija de DN codificada en el cliente.

| Método | Ruta | Función |
|---|---|---|
| POST | /v1/auth/login | username/password; token de usuario de 15 minutos |
| GET | /v1/status | Nodos vivos descubiertos en metadatos |
| GET | /v1/fs/ls y /v1/fs/stat | Consulta de ruta en namespace del usuario |
| POST | /v1/fs/mkdir | Crear directorio |
| DELETE | /v1/fs/rmdir y /v1/fs/rm | Quitar directorio vacío o nombre de archivo |
| POST | /v1/open | Abrir path en r, rw, w, create o a |
| POST | /v1/plan | Reservar identificadores y destinos para bloques cifrados |
| GET | /v1/manifest | Refrescar ubicaciones del mismo snapshot del handle |
| POST | /v1/commit | Validar recibos y publicar una nueva versión atómicamente |
| POST | /v1/close | Cerrar handle sin publicar cambios pendientes |
| POST | /v1/lock | Lock shared/exclusive por rango inclusivo de índices |
| POST | /v1/lock/renew | Renovar lock vigente con fencing nuevo |
| POST | /v1/unlock | Liberar lock del handle y propietario |
| PUT y GET | /v1/blocks/UUID | DN; capacidad firmada para un bloque y operación |

El cuerpo de cada solicitud de control es JSON. `mkdir` recibe `path`; `open`, `path` y `mode`; `lock`, `handle`, `start`, `end`, `ttl` y `type`. `commit` recibe `handle`, tamaño final, IDs de los bloques planificados y el lock/fencing si existe. La especificación ejecutable está en `dfsha/control.py` y el SDK en `dfsha/client.py`.

## Operaciones del SDK

`Client.fs()` implementa RF1, mientras `cd` mantiene el directorio actual del CLI tras verificar que existe. `Client.put(local, remoto)` reemplaza el archivo completo solo al confirmar; `get(remoto, local)` reconstruye a un temporal y reemplaza el destino tras finalizar correctamente.

`open(path, mode)` acepta `r` (solo lectura), `rw` (modificar snapshot existente), `w` (crear o reemplazar), `create` (solo creación) y `a` (append). `Handle.read(offset, length)` lee bytes del snapshot y sus propios cambios pendientes. `write(offset, bytes)` aplica COW a los bloques tocados; un offset mayor que el tamaño actual se rechaza, pues no se crean agujeros dispersos. En modo append el dato se agrega al final del snapshot; el offset proporcionado no selecciona otra posición.

`Handle.close()` publica si hay cambios; de lo contrario solo cierra. Un 409 no convierte una escritura fallida en confirmada. Se debe reabrir y rehacer el cambio sobre la nueva versión; `abort()` descarta el handle. No se reintentan silenciosamente cambios semánticos que puedan sobrescribir a otro usuario.

## Visibilidad y consistencia

Una apertura previa mantiene su versión hasta cierre o expiración. Una apertura posterior al commit ve la nueva versión. No se ofrece POSIX ni lectura siempre actual de handles antiguos. Los snapshots se conservan en metadatos y sus bloques no se recolectan automáticamente.

Una carga al DN no publica un archivo. El control exige recibos W2 correctos y CAS en etcd. Sin mayoría de metadatos se rechazan las nuevas operaciones que la requieren con 503. No se anuncia disponibilidad de login o de nuevas aperturas sin quórum.

La escritura concurrente en un mismo bloque produce un ganador y un conflicto 409; en bloques distintos puede fusionar cambios. Una modificación concurrente del tamaño también puede generar conflicto. Los locks no son una mera sugerencia: el commit comprueba otros locks solapados, lease vigente y cobertura exclusiva del rango si el cliente presenta lock. Un lock shared permite coexistir con otros shared, pero no autoriza publicación exclusiva.

## Plano interno

El control recibe `/internal/heartbeat` mediante mTLS y valida que CN y `node_id` coincidan con la configuración de servidor. DN expone recibos y escritura de réplica bajo `/internal/blocks/UUID`, lectura de ciphertext bajo `/internal/raw/UUID` y reparación mediante `/internal/repair`, en un puerto mTLS separado. Los certificados privados y las capacidades no se incluyen en los registros públicos ni en la entrega.

## Errores principales

400 entrada inválida o bloques incompletos; 401 credenciales/capacidad inválidas; 403 namespace ajeno o acción prohibida; 404 recurso no publicado; 409 conflicto de versión/directorio no vacío; 410 handle/lock vencido; 422 checksum incorrecto; 423 rango bloqueado; 429 demasiados intentos; 503 servicio o quórum insuficiente.
