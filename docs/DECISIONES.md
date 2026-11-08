# Decisiones de arquitectura

La comparación usa los PDF individuales suministrados por Andrés y Juan. La selección corresponde al prototipo construido; los integrantes deben revisarla y ratificarla antes de la entrega y de la defensa. No se atribuyen reuniones o aprobaciones que no estén registradas.

| Aspecto | Andrés | Juan | Prototipo |
|---|---|---|---|
| Arquitectura | Cliente servidor con S2S | Cliente servidor con S2S | Misma opción, procesos separados y red real |
| Control | 3 controles y 3 etcd | 3 controles y 3 etcd | 3 controles y 3 etcd Raft |
| Datos | 4 nodos, R3 W2 | 4 nodos, R3 W2 | 4 nodos, R3 W2, un dominio por VM |
| Bloque | 8 MiB configurable | 4 MiB | 4 MiB configurable |
| Protocolo | REST control y gRPC datos | REST en todos los planos | HTTPS JSON y binario en todos los planos |
| Escritura parcial | COW por offset | Reemplazo y append; exclusión de offset pendiente de aprobación | Offset por byte con COW, reemplazo y append |
| Concurrencia | Rangos y fencing, CAS por bloque | CAS por versión de archivo | Rangos y fencing; CAS por bloque o por archivo según modo |
| Cifrado | Servidor | Cliente y clave envuelta | Cliente AES-256-GCM; DEK envuelta con KEK del control |
| Despliegue | Servicios agrupados en VMs | 7 VMs | 4 VMs, con caída de VM completa probada |

## D1 Bloques pequeños configurables

4 MiB reduce memoria por operación y permite mostrar particionamiento con archivos de 64 y 128 MiB. La configuración se comparte entre controles y el cliente recibe el tamaño al abrir. No se cambia el tamaño en un clúster que ya contiene datos sin una migración.

## D2 Un protocolo de aplicación

Se utiliza HTTP sobre TLS 1.3, JSON para control y cuerpos binarios para bloques. Evita dos serializadores y facilita revisar las evidencias. El servidor usa la biblioteca estándar Python y procesos separados con systemd en AWS. Se acepta el costo de conexiones HTTPS sin un pool persistente y no se afirma endurecimiento de producción. La exigencia del enunciado es justificar la herramienta, no usar obligatoriamente FastAPI o gRPC.

## D3 Publicación transaccional e inmutabilidad

Los nodos guardan primero el ciphertext mediante archivo temporal, fsync y enlace atómico. El control comprueba dos recibos durables del mismo hash/tamaño antes de actualizar en una sola transacción el namespace, la versión, el handle y las ubicaciones. El punto de confirmación del usuario es la transacción de publicación, no una carga aislada de bloques.

## D4 Semántica de concurrencia

La apertura toma un snapshot. Dos escrituras sobre el mismo bloque base compiten mediante CAS y una obtiene 409. Las modificaciones en bloques distintos pueden fusionarse si no alteran incompatiblemente el tamaño. Reemplazo, creación y append usan CAS de la versión completa. Los locks de rango inclusivo son explícitos, tienen lease real de etcd y token de fencing. La renovación cambia el fencing; un cliente vencido no revive su lock.

## D5 Replicación y fallos

El control descubre nodos mediante heartbeats con lease y elige tres dominios mediante rendezvous hashing. Se exige W2 para confirmar; por tanto no se promete conservar una escritura reciente ante dos fallos simultáneos antes de completar R3. Un trabajador elegido con lease revisa recibos y reconstruye copias desde una réplica sana. Si la elección vence durante un recorrido largo puede haber más de un trabajador; bloques inmutables y CAS hacen idempotentes sus efectos, pero generan trabajo redundante.

## D6 Claves y confianza

El cliente cifra cada bloque con nonce aleatorio, AES-256-GCM y AAD propietario/archivo/índice. Los DN solo persisten ciphertext. La DEK de archivo se guarda envuelta con una KEK compartida por los tres controles. Contraseñas Argon2id, capacidades acotadas a bloque/operación, TLS público y mTLS interno. La CA emisora queda exclusivamente en el administrador. Los nodos y el administrador pertenecen al dominio de confianza; no se ofrece protección frente a un nodo autorizado malicioso ni frente al compromiso de un control.

## D7 Cuatro VMs económicas

VM1 a VM3 albergan un control, un etcd y un DN cada una; VM4 alberga DN4. Se evita confundir procesos con dominios de fallo: la prueba detiene VM2 completa y comprueba quórum y reparación sobre VM1, VM3 y VM4. No se crean buckets S3 ni recursos de prácticas previas.

## D8 Alcance y aprobación

Se implementan las operaciones RF3 nombradas en el enunciado, incluida escritura arbitraria dentro del archivo y extensión contigua. No se implementan todos los detalles de POSIX. Si el profesor considera que alguna restricción documentada constituye una simplificación de RF3 o de una garantía no funcional, requiere su aprobación; el equipo no debe afirmar que ya la tiene.
