"""Construye el informe con las métricas y evidencias existentes, sin inventarlas."""
from pathlib import Path
import hashlib
import json
import statistics
import sys

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

B=Path(__file__).resolve().parents[1];E=B/'evidencias';D=B/'docs'
func=json.loads((E/'funcional_aws.json').read_text())
fault=json.loads((E/'fallos_aws.json').read_text())
bench=json.loads((E/'rendimiento_aws.json').read_text())
inv=json.loads((E/'aws_inventario_final.json').read_text())
assert len(func)==22 and all(x['ok'] for x in func)
assert len(bench['muestras'])==10 and all(x['hash_correcto'] for x in bench['muestras'])
assert all(x['estado']=='stopped' for x in inv['instancias'])
get_result=next(x['resultado'] for x in func if x['prueba']=='recuperacion_hash_y_distribucion')

canvas=Image.new('RGB',(2000,800),'white');draw=ImageDraw.Draw(canvas)
font_path=next((p for p in [Path('/System/Library/Fonts/Supplemental/Arial.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')] if p.exists()),None)
font=lambda size:ImageFont.truetype(str(font_path),size=size) if font_path else ImageFont.load_default(size=size)
def center(x,y,text,size=26):draw.multiline_text((x,y),text,font=font(size),fill='#162536',anchor='mm',align='center',spacing=14)
center(1000,45,'Cliente CLI y SDK',38)
center(1000,110,'TLS 1.3  ·  cifrado en el cliente  ·  descubrimiento de DN',25)
for i in range(4):
    x=20+i*500;cx=x+230
    draw.rounded_rectangle((x,250,x+460,620),radius=18,fill='#F1F5F9',outline='#334155',width=3)
    center(cx,300,'VM '+str(i+1),34)
    text=('Control '+str(i+1)+'\netcd '+str(i+1)+'\nDatos '+str(i+1)) if i<3 else 'Datos 4\nDestino de reparación\nDominio independiente'
    center(cx,445,text,28)
    draw.line((1000,155,cx,235),fill='#475569',width=3)
    draw.polygon(((cx,245),(cx-9,226),(cx+9,226)),fill='#475569')
    draw.line((cx,620,cx,690),fill='#475569',width=3)
draw.line((250,690,1750,690),fill='#475569',width=3)
center(1000,755,'Red privada EC2  ·  S2S con mTLS  ·  Raft entre etcd 1 a 3',27)
canvas.save(E/'arquitectura.png')

doc=Document();sec=doc.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=sec.bottom_margin=Inches(.7);sec.left_margin=sec.right_margin=Inches(.75)
for name in ('Normal','Title','Subtitle','Heading 1','Heading 2','Caption'):
    s=doc.styles[name];s.font.name='Calibri';s.font.color.rgb=RGBColor(0,0,0)
    for border in list(s.element.xpath('./w:pPr/w:pBdr')):border.getparent().remove(border)
doc.styles['Normal'].font.size=Pt(11)
doc.styles['Normal'].paragraph_format.space_after=Pt(7)
doc.styles['Normal'].paragraph_format.line_spacing=1.06
doc.styles['Title'].font.size=Pt(26)
doc.styles['Heading 1'].font.size=Pt(18)
doc.styles['Heading 2'].font.size=Pt(12)
doc.styles['Heading 1'].paragraph_format.space_after=Pt(10)
doc.styles['Heading 2'].paragraph_format.space_before=Pt(9)
doc.core_properties.title='DFSha implementación grupal y pruebas en AWS'
doc.core_properties.author='Andrés Arroyave Cardona y Juan Jerónimo Tabares Cataño'
doc.core_properties.subject='Proyecto 1 Sistemas Distribuidos 2026 2 Fase II'
footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
r=footer.add_run();fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');r._r.addnext(fld)

def p(t,style=None):return doc.add_paragraph(t,style)
def h(t):doc.add_heading(t,level=2)
def page(t):doc.add_page_break();doc.add_heading(t,level=1)
def code(t):
    q=p(t);q.paragraph_format.space_after=Pt(7)
    for r in q.runs:r.font.name='Courier New';r.font.size=Pt(9)
def table(headers,rows,widths=None):
    t=doc.add_table(rows=1,cols=len(headers));t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
    if widths:
        for col,width in zip(t.columns,widths):col.width=Inches(width)
    for i,header in enumerate(headers):t.rows[0].cells[i].text=str(header)
    for row in rows:
        cells=t.add_row().cells
        for i,value in enumerate(row):cells[i].text=str(value)
    pr=t._tbl.tblPr;borders=OxmlElement('w:tblBorders')
    for edge in ('top','left','bottom','right','insideH','insideV'):
        el=OxmlElement('w:'+edge);el.set(qn('w:val'),'single');el.set(qn('w:sz'),'4');el.set(qn('w:color'),'D9D9D9');borders.append(el)
    pr.append(borders)
    for ri,row in enumerate(t.rows):
        trpr=row._tr.get_or_add_trPr();cant=OxmlElement('w:cantSplit');trpr.append(cant)
        if ri==0:rep=OxmlElement('w:tblHeader');trpr.append(rep)
        for ci,c in enumerate(row.cells):
            if widths:c.width=Inches(widths[ci])
            c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cp=c._tc.get_or_add_tcPr();margins=OxmlElement('w:tcMar')
            for side in ('top','bottom','left','right'):
                el=OxmlElement('w:'+side);el.set(qn('w:w'),'80');el.set(qn('w:type'),'dxa');margins.append(el)
            cp.append(margins)
            shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'334155' if ri==0 else ('F1F5F9' if ri%2==0 else 'FFFFFF'));cp.append(shade)
            for par in c.paragraphs:
                par.paragraph_format.space_after=Pt(2);par.paragraph_format.line_spacing=1.03
                for r in par.runs:
                    r.font.size=Pt(10);r.font.color.rgb=RGBColor(255,255,255) if ri==0 else RGBColor(0,0,0);r.bold=ri==0
    p('')
    return t

doc.add_paragraph('DFSha implementación grupal y pruebas en AWS',style='Title')
p('Proyecto 1 de Sistemas Distribuidos 2026 2',style='Subtitle')
p('Andrés Arroyave Cardona · SIGAA 000499730\nJuan Jerónimo Tabares Cataño · SIGAA 000214226\nFase II grupal · Ejecución de referencia del 8 de noviembre de 2026')
h('Resultado del prototipo')
p('El prototipo implementa un servicio de archivos con namespace por usuario, bloques cifrados y replicados, operaciones por handle y coordinación de escrituras. Se desplegó en cuatro máquinas virtuales nuevas de AWS Academy y pasó 22 pruebas funcionales, además de pruebas de fallos y diez pares de mediciones de carga y descarga.')
p('La caída de una VM completa no impidió recuperar el archivo de prueba ni confirmar una nueva carga. La pérdida de la mayoría de etcd rechazó operaciones con 503. Las cuatro VMs nuevas se dejaron detenidas, conservando discos. Los recursos existentes quedaron fuera del despliegue.')
table(['Requisito de Fase II','Evidencia disponible'],[
('RF1 y RF2','CLI y SDK; directorios, put/get y hash idéntico'),
('RF3','read/write por offset, append, lock, renovación y close'),
('Distribución','3 controles, 3 etcd y 4 DN en 4 VMs'),
('Concurrencia y fallos','CAS, fencing, caída de VM y reparación automática'),
('Seguridad','Autenticación, namespaces, TLS, mTLS y cifrado'),
('Rendimiento','5 pares por condición, paralelismo 1 y 4')],[2.0,5.0])
h('Cierre de la entrega')
p('El código, las instrucciones y las evidencias están preparados. El equipo debe ratificar las decisiones, revisar sus aportes reales, publicar en su repositorio y grabar el video de 10 a 15 minutos. Este informe no sustituye la defensa oral ni acredita una aprobación docente de restricciones que no haya sido obtenida.')

page('Comparación de propuestas y decisiones')
p('Ambos diseños individuales seleccionan cliente servidor con comunicación entre servidores, control replicado en etcd y cuatro nodos de datos. La siguiente selección integra sus diferencias sin reutilizar proyectos ajenos.')
table(['Aspecto','Andrés','Juan','Prototipo'],[
('Bloque','8 MiB configurable','4 MiB','4 MiB configurable'),
('Control','3 controles y 3 etcd','3 controles y 3 etcd','Se conserva'),
('Replicación','R3 W2','R3 W2','R3 W2'),
('Datos','gRPC','REST','HTTPS binario'),
('Control externo','REST','REST','HTTPS y JSON'),
('Escritura parcial','COW por offset','Restricción pendiente de aprobación','Offset por byte y COW'),
('Coordinación','Rangos y fencing','CAS de archivo','CAS por bloque y locks'),
('Cifrado de bloque','Servidor','Cliente','Cliente AES 256 GCM'),
('VMs','Servicios agrupados','7 VMs','4 VMs separadas')],[1.3,1.8,1.8,2.1])
h('Razones de la selección')
p('Se adopta el bloque de 4 MiB para limitar memoria por transferencia y observar particionamiento con archivos moderados. HTTPS unifica la implementación de control y datos; la biblioteca estándar de Python permite inspeccionar el transporte sin depender de dos frameworks. Esta elección no se presenta como un servidor endurecido de producción.')
p('La escritura por offset preserva RF3 y evita adoptar sin aprobación la exclusión propuesta en el diseño de Juan. Los snapshots y el CAS por bloque permiten dos cambios en bloques distintos; el reemplazo y append usan CAS de archivo para evitar tamaños incompatibles.')
p('El cifrado se realiza antes de enviar los bloques. Se agrupan control, etcd y DN en tres VMs, más un cuarto DN independiente, para reducir costo. La prueba de fallo apaga la VM entera: no se confunde separación de procesos con independencia de máquinas.')
p('El registro ampliado D1 a D8 está en docs/DECISIONES.md. Los integrantes deben revisarlo y ratificarlo; no se afirma que haya existido una reunión formal de consenso no documentada.')

page('Arquitectura y despliegue distribuido')
doc.add_picture(str(E/'arquitectura.png'),width=Inches(7))
p('Figura 1. Topología real del prototipo. Cada caja es una VM independiente de AWS.',style='Caption')
table(['VM','Servicios','Dirección privada'],[(str(i+1),'Control '+str(i+1)+' · etcd '+str(i+1)+' · DN '+str(i+1) if i<3 else 'DN 4',n['ip_privada']) for i,n in enumerate(inv['instancias'])],[.55,4.0,2.45])
p('Cada VM usa Ubuntu 24.04 y t3.small, con 2 vCPU y 2 GiB RAM, más un volumen gp3 cifrado de 12 GiB. El modo de créditos de CPU es standard para evitar cargos de CPU excedente. Todos los nodos están en us-east-1a; se prueba fallo de VM, no resiliencia ante caída de una zona completa.')
h('Metadatos y descubrimiento')
p('etcd mantiene usuarios con hashes, namespaces, versiones, planes, ubicaciones, handles y locks bajo /dfsha-aj/. Las lecturas utilizadas son linealizables. Cada DN registra un heartbeat con lease de 12 segundos y lo envía aproximadamente cada 3 segundos; un nodo vencido deja de ofrecerse como vivo.')
p('El cliente conoce únicamente tres controles iniciales. Al abrir o reservar bloques recibe ubicaciones actuales y capacidades firmadas. El control selecciona tres dominios VM mediante rendezvous hashing; la réplica y la reparación circulan directamente entre servidores mediante mTLS.')

page('Operaciones y garantías de consistencia')
h('RF1 y RF2')
p('Cada usuario tiene una raíz lógica aislada. ls, cd, mkdir, rmdir y rm operan sobre su namespace; rmdir exige que el directorio esté vacío. put reemplaza el archivo completo al confirmar. get reconstruye a un temporal y solo reemplaza el destino local al terminar correctamente.')
p('El control expone POST /v1/auth/login, /v1/open, /v1/plan, /v1/commit y /v1/close; RF1 se atiende bajo /v1/fs/. Los locks se adquieren en /v1/lock y se renuevan en /v1/lock/renew. DN recibe PUT y GET /v1/blocks/UUID con capacidad Bearer. La referencia completa de métodos, parámetros y errores está en docs/API.md.')
h('RF3 por handles')
p('open admite r, rw, w, create y a. read usa offset y longitud en bytes. write acepta reemplazo parcial, cruza bloques con COW y permite extensión contigua; no crea agujeros dispersos. En append agrega al final del snapshot. close publica una nueva versión si hay cambios; abort descarta la sesión.')
p('Una apertura conserva su snapshot; una nueva apertura posterior al commit ve la versión publicada. No se promete que un handle antiguo vea cambios de terceros ni compatibilidad POSIX completa. Los locks cubren índices de bloques inclusivos, no bytes, y duran entre 5 y 300 segundos. La renovación emite un fencing nuevo; un lease vencido no se renueva.')
h('Camino de confirmación')
p('1. El cliente cifra cada bloque con nonce aleatorio y AAD propietario, archivo e índice. 2. El control reserva IDs y tres destinos. 3. El primario guarda el bloque y replica S2S; cada copia usa fsync y publicación atómica en disco. 4. El control verifica al menos dos recibos durables con tamaño y SHA correctos. 5. Una transacción CAS publica la versión y ubicaciones. Sin este último paso, los bloques cargados no hacen visible un archivo parcial.')
h('Escritores concurrentes')
p('Dos cambios sobre el mismo bloque base producen un ganador y un 409; no se sobrescribe silenciosamente al otro. Dos cambios sobre bloques distintos pueden fusionarse conservando ambos. Creación, reemplazo y append validan la versión del archivo. El contador de lockguard y el CAS del lock serializan adquisición, renovación y commit; un fencing vencido produce 410 y un rango ajeno bloqueado produce 423.')
h('Disponibilidad declarada')
p('Tres miembros de etcd toleran perder uno, pero no dos. Un commit exige W2 de R3 y quórum de metadatos. Antes de completar la tercera copia, perder dos nodos puede perder una escritura reciente: no se promete tolerancia a ese escenario. La reparación copia ciphertext desde una réplica sana y valida hash; no mezcla versiones de bloques.')

page('Seguridad y dominio de confianza')
table(['Control','Implementación y prueba'],[
('Identidad de usuario','Contraseñas Argon2id; token firmado de 15 minutos; acceso sin token y clave inválida rechazados'),
('Aislamiento','Namespace y propietario validados; ruta de otro usuario y traversal reciben 403'),
('Canal externo','TLS 1.3 con CA validada y verificación de nombre; TLS 1.2 rechazado'),
('Canal interno','mTLS en puerto separado; sin certificado de servicio no hay transferencia interna'),
('Bloques','AES 256 GCM en cliente, nonce aleatorio y AAD; hash del ciphertext verificado'),
('Claves','DEK envuelta por KEK del control; DN sin KEK ni secreto de autenticación de usuarios'),
('Persistencia','Bloques 0600 y directorios 0700; cuatro volúmenes EBS cifrados'),
('Red','Puertos de aplicación solo dentro del SG nuevo; SSH de administración restringido')],[1.45,5.55])
p('Las capacidades de DN se limitan a bloque y operación; put también exige un destino del plan y el hash y tamaño esperados. Un ciphertext alterado se rechazó con 422 antes de publicarse. Los controles comparten KEK y secretos de firma; la CA emisora permanece solo en el equipo administrador y no se envía a las VMs ni al ZIP.')
h('Límites de seguridad')
p('El modelo confía en nodos autorizados y administradores. No se ofrece tolerancia bizantina, aislamiento de un DN autorizado malicioso ni seguridad si un control o su KEK es comprometido. mTLS autentica servicios pero no elimina esa confianza. No hay gestión empresarial de claves, revocación PKI en línea ni rotación automática de certificados.')
p('El servidor HTTP de biblioteca estándar tiene límites de tamaño y tiempo de lectura, pero no sustituye un gateway de producción con controles de carga. El contador de intentos de login es básico y no constituye una protección distribuida exhaustiva frente a abuso.')
p('Los certificados de servicio caducan a los 30 días, los handles a una hora y los tokens de usuario a 15 minutos. La recuperación futura requiere conservar de forma privada las claves y renovar la sesión de AWS; estos secretos deliberadamente no se incluyen como material de entrega pública.')

page('Corrección replicación y pruebas de fallo')
p('La suite funcional ejecutó 22 casos con resultado correcto en AWS, además de una corrida local separada. El archivo de 64 MiB se dividió en 16 bloques y sus ubicaciones abarcaron los cuatro DN.')
table(['Prueba','Resultado observado'],[
('Carga y recuperación','64 MiB, 16 bloques; SHA 256 original y recuperado iguales'),
('RF3 y snapshot','24 bytes escritos desde offset 4194301; cruza dos bloques y preserva snapshot anterior'),
('Append','8 bytes añadidos y recuperados al final'),
('Mismo bloque','Resultados 200 y 409; un solo ganador'),
('Bloques distintos','Dos commits y ambos cambios conservados'),
('Bloqueos','Conflicto 423, lease vencido 410; renovación con fencing mayor y commit válido')],[1.5,5.5])
p('SHA 256 del archivo funcional recuperado',style='Caption')
code(get_result['hash_original'][:32]+'\n'+get_result['hash_original'][32:])
table(['Escenario real en AWS','Resultado'],[
('VM2 completa apagada','Lectura idéntica y nueva carga confirmada con dos etcd vivos'),
('Reparación','8 bloques restaurados a R3 sobre datos1, datos3 y datos4'),
('Solo un etcd vivo','Operación dependiente de metadatos rechazada con 503'),
('Reinicio de VM2','Cuatro DN nuevamente registrados; discos conservados'),
('Réplica cortada después del plan','Dos recibos durables; commit y lectura de versión correcta')],[2.15,4.85])
p('Los archivos funcional_aws.json, fallos_aws.json y almacenamiento_aws.json contienen los registros reproducibles. Las pruebas de laboratorio local se identifican como locales y no sustituyen las de red en AWS. Los registros fallidos de desarrollo no se presentan como corridas exitosas.')

page('Mediciones de rendimiento en AWS')
p('Se generó un archivo aleatorio de 128 MiB y se midieron diez cargas y diez descargas completas con comprobación SHA 256, cinco pares por condición. El cliente se ejecutó en VM1; los datos circularon por red privada entre las cuatro VMs. La condición cambiada es el paralelismo del SDK, 1 frente a 4 transferencias.')
rows=[]
for r in bench['muestras']:rows.append((r['paralelismo'],r['repeticion'],f"{r['put_s']:.3f}",f"{r['get_s']:.3f}",f"{r['put_MiB_s']:.2f}",f"{r['get_MiB_s']:.2f}"))
table(['Paralelo','Par','put s','get s','put MiB/s','get MiB/s'],rows,[.75,.45,1,1,1.4,1.4])
summary=[]
for par in (1,4):
    rs=[r for r in bench['muestras'] if r['paralelismo']==par]
    summary.append((par,f"{statistics.median(r['put_s'] for r in rs):.3f}",f"{statistics.median(r['get_s'] for r in rs):.3f}",f"{statistics.median(r['put_MiB_s'] for r in rs):.2f}",f"{statistics.median(r['get_MiB_s'] for r in rs):.2f}"))
table(['Paralelo','Mediana put s','Mediana get s','Mediana put MiB/s','Mediana get MiB/s'],summary,[.75,1.25,1.25,1.7,1.7])
one,four=bench['resumen'];putchange=(1-four['put_mediana_s']/one['put_mediana_s'])*100;getchange=(1-four['get_mediana_s']/one['get_mediana_s'])*100
p(f'En esta corrida, el paralelismo 4 cambió la mediana de carga en {putchange:.1f}% de reducción y la de descarga en {getchange:.1f}% de reducción frente a 1. El resultado refleja este entorno y no es una garantía. El costo de cifrado, TLS, recibos y CAS forma parte de la duración; no es una medición aislada del disco.')
p('Las pruebas se ejecutaron después de las funcionales y de fallos; no se vaciaron cachés ni se aleatorizó el orden de condiciones. Son cinco muestras por condición, con VM1 compartiendo cliente y servicios. Por ello no se infiere significancia estadística, escalabilidad ilimitada ni capacidad de producción. El detalle está en rendimiento_aws.json.')

page('Reproducción y límites operativos')
h('Arranque local')
code('python3 -m venv .venv\n.venv/bin/pip install -r requirements.txt\n.venv/bin/python scripts/preparar.py\ndocker compose -f compose.local.json up -d\n.venv/bin/python scripts/usuarios.py\n.venv/bin/python scripts/servicios_locales.py start')
p('Se necesitan Python 3.12 y Docker Compose. El generador crea secretos nuevos solo si no existen, y se niega a sobrescribirlos. El CLI usa .private/client.json y puede pedir contraseña sin mostrarla. La carpeta .private no se publica ni se incluye en el ZIP.')
h('Pruebas y apagado seguro')
code('.venv/bin/python tests/funcional.py\n.venv/bin/python tests/fallos_locales.py\nDFSHA_MIB=128 .venv/bin/python tests/rendimiento.py\n.venv/bin/python scripts/servicios_locales.py stop\ndocker compose -f compose.local.json stop')
p('En AWS, desplegar_aws.py y pruebas_aws.py usan exclusivamente la cuenta verificada y las IDs del registro privado. Los servicios están bajo /opt/dfsha-aj y systemd. Se arrancan y detienen solo las cuatro VMs del proyecto; no se pulsa Reset del laboratorio.')
h('Restricciones que afectan uso y evaluación')
p('Se retienen versiones y bloques viejos sin recolección de basura. rm retira el nombre pero no es borrado físico inmediato. Un namespace por usuario y el historial completo por archivo residen en valores de etcd, por lo que el tamaño máximo de valor y transacción limita la escala. No se ofrece capacidad ilimitada ni compatibilidad POSIX completa.')
p('No se implementan agujeros dispersos, compartición entre usuarios ni un montaje FUSE. Handles antiguos leen snapshots; append puede entrar en conflicto y requiere reabrir. No hay renovación de handle ni de login automática. Un trabajador de reparación puede solaparse con otro si su lease vence durante un recorrido largo; sus cambios son idempotentes, pero producen trabajo redundante.')
p('Las restricciones se declaran para que el equipo las contraste con el profesor. El enunciado exige aprobación previa si se consideran simplificaciones de RF3 o de garantías no funcionales; este informe no afirma que esa aprobación ya exista.')

page('Evidencia del despliegue y cierre del equipo')
shot=E/'aws_instancias_detenidas.png'
if shot.exists():
    doc.add_picture(str(shot),width=Inches(7));p('Figura 2. Captura auténtica de la consola al finalizar. Las cuatro VMs nuevas están detenidas.',style='Caption')
h('Presupuesto y preservación')
p('El laboratorio mostraba US$22,50 usados de US$50 antes del despliegue. Se autorizó un máximo de US$5 para este trabajo. Se crearon cuatro VMs y 48 GiB de EBS cifrado; se apagaron al terminar. Las tarifas son orientativas y el saldo del laboratorio puede actualizarse con demora. Los discos conservados siguen facturando hasta que el titular decida eliminarlos; no se borraron datos para reducir costo.')
p('A la tarifa publicada de US$0,0209 por t3.small hora y US$0,005 por IPv4 hora, el conjunto encendido ronda US$0,104 por hora, más almacenamiento y transferencias aplicables. Como referencia, 48 GiB a US$0,08 por GB mes equivalen aproximadamente a US$3,84 mensuales; no es una factura ni un tope automático.')
h('Pendientes para la entrega completa')
p('Los integrantes deben ratificar la arquitectura, registrar sus revisiones y contribuciones reales, publicar en el repositorio que suministren, grabar el video de 10 a 15 minutos y preparar la defensa. El guion está en docs/GUIA_VIDEO.md. No se crearon commits que simulen aportes ni se modificó el repositorio de otro proyecto.')
h('Referencias')
for line in [
'Enunciado Proyecto1_DFSha-20262.pdf, secciones 5 y 7, y las dos propuestas individuales suministradas por el equipo.',
'etcd API y transacciones https://etcd.io/docs/v3.6/learning/api/',
'etcd seguridad https://etcd.io/docs/v3.6/op-guide/security/',
'AWS T3 https://aws.amazon.com/ec2/instance-types/t3/',
'AWS EBS https://aws.amazon.com/ebs/pricing/',
'AWS IPv4 https://aws.amazon.com/vpc/pricing/']:
    q=p(line);q.paragraph_format.space_after=Pt(4)
    for r in q.runs:r.font.size=Pt(9)

D.mkdir(exist_ok=True)
doc.save(D/'DFSha_FaseII_Andres_Arroyave_Juan_Tabares.docx')
print('Informe creado con métricas reales y estado de AWS verificado.')
