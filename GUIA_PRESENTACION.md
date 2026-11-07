# Guía de presentación — Guía de Orientación

Esta guía explica el sistema completo, módulo por módulo, para poder
presentarlo y responder preguntas. El detalle matemático está en
`ECUACIONES.md` y el comportamiento general en `README.md`.

---

## 1. El problema y la solución

**Problema:** una persona con discapacidad visual se mueve dentro de
una casa (una universidad, un piso) y no sabe en qué zona está ni cómo
llegar a un sitio concreto sin ayuda constante de otra persona.

**Solución:** un dispositivo portátil (Raspberry Pi 5 + cámara + sensor
ultrasonico + MPU6050 + micrófono + altavoz) que:

1. **Se localiza solo**: reconoce señales impresas en las puertas con
   la cámara (YOLO) y dice *"Estás en Sala"*.
2. **Navega por voz**: la persona pide *"Raspi, ve al baño"* y el
   sistema da instrucciones habladas: *"Gira a la izquierda 90
   grados"*, *"Camina 6 pasos"*.
3. **No miente**: nunca anuncia una llegada que no verificó; exige la
   señal centrada, 70–80 cm estables por ultrasonido, camino frontal
   libre y una pausa tras avisar un obstáculo.
4. **Cuida la seguridad**: avisa obstáculos cercanos (pitidos que se
   aceleran) y detecta caídas (alarma continua + "di silencia").

**Dato clave para la presentación**: el código admite la clase
personalizada `door`, pero los modelos incluidos en el repositorio no la
contienen. El modelo configurado debe incluir tanto `door` como las
señales de animales; si falta `door`, el arranque lo advierte. Una puerta
ayuda a orientar la búsqueda, pero solo una señal interior determina la
zona.

---

## 2. Arquitectura física

| Componente | Modelo | Función |
|---|---|---|
| Computadora | Raspberry Pi 5 | Corre todo el programa |
| Cámara | USB (libcamera/UVC) | Ve las señales de las puertas (640×480) |
| Inercial | MPU6050 (I2C) | Giroscopio + acelerómetro: giros, pasos, caídas |
| Distancia | HC-SR04 (GPIO) | Obstáculos: pitidos que se aceleran |
| Audio | Micrófono USB + altavoz | Escucha (Vosk) y habla (Piper) |

---

## 3. Arquitectura de software

```
main.py                     Punto de entrada: arma el equipo y arranca
guia/
  configuracion.py          TODOS los umbrales y rutas (un solo lugar)
  aplicacion.py             Cerebro: máquina de estados, orquesta todo
  version.py                Commit en ejecución (por consola)
  dominio/                  El "qué" del sistema (sin hardware)
    estados.py              Los 9 estados del asistente
    catalogo.py             Qué puede ver la cámara (señales, obstáculos)
    comandos.py             Qué puede entender el oído (destinos, órdenes)
    rutas.py                Mapa de la casa y planificador de rutas
  navegacion/               El "movimiento"
    movimiento.py           Muestreador MPU (100 Hz), medidor de giro,
                            contador de pasos
    rumbo.py                Orientación relativa desde la última señal
    posicion.py             Estimador 1D de avance e incertidumbre
    odometro.py             Avance con ultrasonido opcional por tramo
    puertas.py              Seguimiento y conteo auxiliar de puertas
    caida.py                Detección de caídas por el acelerómetro
  vision/                   El "ojo"
    detector.py             YOLO: señales, puertas y obstáculos
    interfaz.py             Ventana de diagnóstico (para quien ve la pantalla)
  audio/                    La "voz y oído"
    locutor.py              Sintesis de voz (Piper + caché)
    microfono.py            Escucha continua (Vosk) con modos y colas
    gramaticas.py           Listas de palabras que Vosk intenta transcribir
    alarma.py               Beeps de proximidad y alarma de caída
  hardware/                 Adaptadores al equipo real
    mpu6050.py              Lectura del sensor (escala datasheet + DLPF 44/42 Hz)
    camara.py               Captura de frames
    ultrasonido.py          HC-SR04 (gpiozero) en hilo propio + alerta
pruebas/
  test_flujo.py             34 pruebas automatizadas sin hardware
  prueba_ultrasonido.py     Verificación directa del HC-SR04 en la Pi
  caracterizar_mpu6050.py   Offset y ruido del MPU6050 en reposo
  registrar_caminata.py     CSV de sensores con la aplicación cerrada
  evaluar_pasos.py          Reproducción offline y error del contador
  calibrar_focal.py         Calibración de distancia visual
mi_deteccion/               Modelos YOLO (incluye la clase personalizada door)
piper_voices/  vosk/        Modelos de voz (TTS y reconocimiento)
```

### Los 4 hilos (clave para preguntas)

| Hilo | Corre a | Por qué en hilo propio |
|---|---|---|
| Bucle principal | cámara + YOLO (~100–300 ms/frame) | Es el que decide; si se atrasa, nada se rompe |
| `muestreador-mpu` | 100 Hz fijos | Integrar giros a frecuencia fija: si se muestrea con el ritmo del bucle, un frame lento pierde grados de giro |
| `alerta-proximidad` | ~10 Hz | Los pitidos no dependen de la cámara |
| `microfono` | bloqueado en audio | Vosk escucha siempre; publica en una cola que el bucle consume |

El bucle principal, en cada iteración, hace **exactamente esto**
(`Aplicacion._bucle`):

```python
frame = camara.capturar()          # 1. mirar
resultado = detector.analizar(frame)  #    (YOLO)
procesar_voz()                     # 2. escuchar
vigilar_caida()                    # 3. seguridad
anunciar_obstaculos(resultado)     # 3. seguridad
manejadores[estado](resultado)     # 4. actuar (un solo punto de decisión)
mostrar(frame, ...)                # 5. diagnosticar
```

---

## 4. La máquina de estados

El sistema es una **máquina de estados** (`dominio/estados.py`): en cada
instante solo se ejecuta la lógica del estado actual. Sin esto, el
código se convierte en banderas globales imposibles de explicar.

| Estado | Qué pasa | Sale cuando... |
|---|---|---|
| `GIRO_INICIAL` | Pide girar 90° a la derecha e integra el giro | Lleva los 90° |
| `BUSCAR_SENAL` | Mira 1 minuto buscando una señal | Ve una señal / se acaba el tiempo |
| `MEDIA_VUELTA` | Media vuelta de 180° (1.er intento fallido) | Lleva los 180° |
| `CINCO_PASOS` | Camina 5 pasos y reintenta girar (2.º intento) | Dio los 5 pasos |
| `ESPERANDO_DESTINO` | Escucha *"Raspi, ve a la sala"* | Pide un destino |
| `ENCARAR_SENAL` | Pide poner la señal de la zona enfrente (se omite con la encarada automática: señal confirmada hace < 30 s y giro < 20°) | Ve esa señal por su cara interior |
| `NAVEGANDO` | Ejecuta la ruta: giros integrados + pasos acreditados | Se agota el plan |
| `CONFIRMANDO_LLEGADA` | Pide girar despacio hasta ver la señal del destino | Ve la señal del destino |
| `DETENIDO` | Salida limpia | — |

**Historia completa de un recorrido** (el guion de la demo, sección 10):

1. Arranque: *"Gira 90 grados a la derecha suavemente para buscar la
   señal orientadora"* → giro en silencio → *"Puedes parar. Ya giraste
   90 grados."*
2. Búsqueda: *"Buscando la señal durante un minuto"* → la cámara ve la
   vaca (cara interior de Inicio) → *"Estás en Inicio. ¿A dónde
   quieres ir?"*
3. Destino: *"Raspi, ve a la sala"* → *"Entendido. Vamos a Sala."*
4. **Encarada**: como las rutas se midieron mirando la señal, se pide
   ponerla enfrente: *"Para saber hacia dónde miras, primero encárate
   con la señal de esta zona. Gira despacio a la izquierda hasta que
   te diga que la tienes frente a ti."* → al verla: `RUMBO ANCLADO`
   (rumbo = 0°) → *"La señal está frente a ti."* El lado en el que
   gira sale del sentido de la ruta (`GIRO_SENAL_NORMAL` /
   `GIRO_SENAL_INVERSO`): a la **izquierda** en el sentido normal y a
   la derecha al volver, porque las imágenes cambian de lado.
   **Excepción (encarada automática)**: si la señal de la zona
   se acaba de confirmar en el paso 2 y no has girado más de 20°
   desde entonces, no se pide nada: la ruta arranca directo
   (`ENCARADA AUTOMATICA` en consola). En casa no podría encontrarse
   otra señal en el mismo sitio, así que pedirla otra vez dejaría a
   la persona girando sin salida.
5. Navegación: *"Gira a la izquierda 90 grados"* → *"Puedes parar.
   Giro completo."* → *"Camina 14 pasos"*
   (hasta el Cuarto) → cuenta solo pasos rectos → *"Pasos
   completados. Puedes parar."* → **encarada intermedia**: como al
   terminar el tramo la persona queda de espaldas a la señal de esa
   zona, pide *"Gira despacio a la izquierda hasta encontrar la señal
   de esta zona"* → al verla (*"La señal está frente a ti."*) sigue
   el segundo tramo: otro giro a la izquierda y *"Camina 10 pasos"*
   hasta la Sala.
6. Llegada verificada: *"Para confirmar la llegada, gira despacio a
   la izquierda hasta encontrar la señal de Sala"* → al verla:
   *"Has llegado a Sala. ¿A dónde quieres ir?"*

---

## 5. Los algoritmos clave (la parte que hay que saber explicar)

### 5.1 Giro: integrar la velocidad angular

El giroscopio **no mide ángulos, mide velocidad angular** (grados por
segundo). Para saber cuántos grados giró la persona:

```
ángulo acumulado = Σ (velocidad_angular × dt)     [Euler]
```

Eso exige muestrear a **frecuencia fija**: por eso el MPU6050 se lee en
su propio hilo a 100 Hz (`MuestreadorMPU`). Si se integrara con el
ritmo del bucle principal (que va al ritmo de la cámara), cada frame
lento haría perder grados.

**Proyección sobre la vertical** (el detalle elegante): el módulo del
MPU puede venir montado en cualquier orientación, así que no se usa un
eje fijo del sensor. Se proyecta el vector giroscopio sobre la dirección
de la gravedad medida por el acelerómetro:

```
ω_vertical = (a⃗ · ω⃗) / |a⃗|
```

La componente que cuenta es siempre la del eje hacia arriba, sea cual
sea el montaje. El signo (derecha/izquierda) lo da la regla de la mano
derecha.

**Deriva**: el giroscopio tiene sesgo y deriva con la temperatura; por
eso el `MedidorGiro` lo aprende **en marcha**, mirando los extremos de
una ventana de 1 s: si la señal está plana toma la velocidad tal cual,
y si oscila (la marcha) toma el punto medio `(máx+mín)/2`, que es
independiente de la fase del paso. Solo aprende con el rumbo cerrado
(velocidad sostenida < 5 °/s), y el `Rumbo` se **re-ancla en cada
señal vista** (la señal es una referencia física conocida).

### 5.2 Rumbo: orientación relativa, no absoluta

El MPU6050 no tiene magnetómetro: **no sabe dónde está el norte**, ni
hace falta. Navegar basta con saber *hacia dónde mira la persona
respecto de la última señal que vio* (en ese instante la tiene enfrente,
rumbo = 0°). Entre señales, la rotación se acumula con el giroscopio.
Si la persona se gira y luego pide una ruta, el sistema lo sabe: por eso
exige la **encarada** antes de arrancar.

### 5.3 Pasos: banda del paso y umbral adaptativo

Al caminar, el acelerómetro muestra oscilación con cada pisada, pero
también gravedad, postura que cambia y balanceo lento. El
`ContadorPasos` pasa la señal por un **pasa-banda de 0.5 a 5 Hz**
(biquad RBJ sin scipy): solo queda la frecuencia del paso y, como el
filtro no tiene entrada en continua, un cambio de postura de golpe no
genera ningún pico. Sobre esa señal se cruza un **umbral adaptativo**,
`máx(0.09 g; 0.45 × media de los últimos picos contados)`, con
histéresis (hay que bajar a la mitad para rearmar) y un tiempo
refractario de 0.18 s para no contar dos veces el mismo pisón. El
umbral se mantiene solo, a la mitad de lo que de verdad produce la
marcha de esa persona: si camina suave baja, si va vigoroso sube. Además
cada pico tiene que encajar en el **ritmo medido** (±40 % de la mediana
de los últimos intervalos, o el piso de 1.5 s sin marcha); tres
descartes seguidos re-baselan el ritmo para no arrastrar una cadencia
que ya no describe a la persona.

### 5.4 Línea recta: solo cuentan los pasos rectos

Los pasos del mapa se midieron en línea recta. Un paso dado mientras la
persona va curvando **no la acerca al sitio medido** (la curva alarga el
recorrido), así que no se acredita: el rumbo compara contra el rumbo del
primer paso (`_rumbo_al_caminar`). Si la desviación supera 25°, los
pasos se congelan hasta enderezar. El rumbo solo se mueve con **giro
sostenido** (EMA de 0.5 s, gate de 5 °/s), así que el balanceo al
caminar no lo deforma.

### 5.5 Corrección guiada del desvío (lo más reciente)

Cuando el sistema detecta que vas curvando, **no manda un número fijo
de grados** ("gira 30°") porque ese número se queda viejo mientras la
voz habla y tú sigues caminando. En su lugar guía el giro **en vivo**:

1. El aviso solo sale si el desvío pasa de 25° **y se mantiene un
   segundo** (`confirmacion_desvio_s`): un paso torcido o un balanceo
   puntual no lo disparan.
2. *"Alto un momento. Te estás desviando de la línea recta, unos 30
   grados. Gira despacio a la izquierda hasta que te diga que ya
   enderezaste."*
3. Al volver por debajo del 60% del umbral (histéresis): *"Ya
   enderezaste. Los pasos vuelven a contar."*
4. Respaldo: si a los 15 s de terminar de sonar el aviso sigues fuera
   de la recta (o giraste al revés), se recuerda con los grados
   actualizados.

No hay avisos al 50% ni al 75% del giro de corrección (antes decían
*"Llevas la mitad del giro para enderezar"* y *"Ya casi enderezas"*):
la voz es lenta y, cuando terminaba de decir la frase, la persona ya
había girado más de lo que tocaba y el giro se deshacía.

Los pasos de la corrección **tampoco se acreditan** hasta enderezar.

### 5.6 Visión: YOLO + confirmación temporal

`DetectorVisual` corre YOLO11n (NCNN sobre la Raspberry) y filtra las
detecciones por el catálogo (`dominio/catalogo.py`):

* **Señales orientadoras** (una foto por zona por cara de puerta).
* **Puertas** (`door`): geometría y rango para guiar la búsqueda y
  corregir posición; no identifican una zona.
* **Obstáculos** que se anuncian en voz alta con su nombre: silla,
  mesa (*"dining table"*, el nombre real en COCO), maceta. La imagen
  se divide en **3 fragmentos iguales** (izquierda, centro, derecha);
  el central va de 1/3 a 2/3 del ancho (`obstaculo_centro_min/max`).

  * **Solo en la navegación**: los obstáculos se anuncian mientras
    la persona camina la ruta; girando para buscar la señal (o
    encarando) la cámara no anuncia nada. La señal, en cambio, se
    lee siempre con los 3 fragmentos: no se recorta.
  * **Dando pasos** solo se tiene en cuenta el fragmento central:
    los de los otros dos se descartan por completo
    (`mirar_solo_centro_en_marcha`), porque avisar de lo que está a
    un lado solo genera confusión. Lo central se avisa en una sola
    frase (*"Alto, en medio del camino hay un obstáculo: silla"*, o
    *"…hay obstáculos: silla y mesa"* si hay varias).
  * Solo se anuncia lo que está en la dirección que se recorre:
    `obstaculos_en_voz` deja fuera a *"persona"* y `anunciar_lados`
    (`False`) deja mudos los lados.

  **La voz de la guía manda** (`silencio_para_obstaculos_s` = 4 s):
  un aviso de objeto espera a que la instrucción quede dicha; nunca
  al revés. La posición la calcula `posicion_en_frame` en
  `vision/detector.py`.

  **Un anuncio por encuentro**: mientras el objeto siga a la vista no
  se repite (antes se repetía cada `enfriamiento_objeto_s` y la frase,
  que bloquea el bucle, tapaba las indicaciones de la ruta). El aviso
  se re-arma solo cuando el frame queda limpio ese tiempo; un objeto
  **nuevo** sí se anuncia enseguida, y un parpadeo de la cámara no lo
  dispara otra vez. El detector ya no aplica enfriamiento: reporta lo
  que se ve en el frame y de repetir se encarga `Aplicacion`.

La cámara parpadea (una señal se ve 2 de cada 5 frames), así que una
señal solo se acepta tras **3 detecciones seguidas** (tolerando 2
pérdidas) — `ConfirmadorSenal`. Es confirmación, no promedio: cambiar de
señal reinicia la cuenta.

La caja de cada señal incluye su alto y ancho en píxeles. Si se conoce
la altura de la imagen impresa y la focal calibrada, se estima la
distancia con `d = focal_px * altura_senal_m / alto_px`. La mediana de
los últimos `distancia_mediana_frames` suaviza el parpadeo; el historial
se descarta tras perder la clase. Hasta calibrar `focal_px` (por defecto
0), no se usa una distancia visual.

Si el modelo configurado contiene `door`, se devuelve aparte de las
señales, con caja, centro, alto, ancho y distancia suavizada con la mediana. En una búsqueda, si
hay una puerta y todavía no se confirma una señal, se anuncia una vez
su ubicación izquierda/centro/derecha y se guía hacia ella. El animal
detectado dentro de la caja de puerta tiene prioridad sobre animales en
otras zonas del frame. El ultrasonido filtra tres lecturas nuevas para
guiar la aproximación al rango configurable de 70–80 cm; una señal solo
confirma llegada si está centrada y no hay un obstáculo frontal. La
puerta nunca identifica la zona: sigue siendo necesaria la señal interior.

### 5.7 Señales de las dos caras de la puerta (idea original)

Cada puerta tiene **dos fotos**: la cara *interior* (se ve estando
dentro de la zona) y la cara del *pasillo* (se ve desde el otro lado).
Las fotos de animales usan clases reconocidas por el modelo base. Para
que la clase personalizada `door` localice el marco, el modelo cargado
debe haber sido entrenado/exportado con esa clase además de las señales:

| Zona | Cara interior (dentro) | Cara pasillo (fuera) |
|---|---|---|
| Inicio | vaca (`cow`) | elefante (`elephant`) |
| Sala | cebra (`zebra`) | oso (`bear`) |
| Baño | jirafa (`giraffe`) | caballo (`horse`) |
| Cuarto | oveja (`sheep`) | perro (`dog`) |

Regla que destruye la ambigüedad: **solo la cara interior confirma
"estás en la zona"** y solo desde ella arrancan las rutas (los tramos
se midieron mirando esa cara). Si ves la cara del pasillo, estás *fuera*
de la puerta: *"Frente a ti está la puerta de Baño, pero estás del lado
del pasillo."*

### 5.8 Llegada verificada (nunca mentir)

Los pasos son una estimación: la zancada cambia y la deriva puede
dejar a la persona a un lado de la puerta. Por eso el tramo **no** se
da por terminado en "N pasos" exactos, sino en una **ventana**:

* al **70 %** de los pasos esperados → *"Reduce el paso, ya casi
  llegas."*
* entre el 70 % y el **130 %** → quien cierra el tramo es **la
  cámara**, si ve la señal interior de la zona de cierre (la
  siguiente del camino): *"Ya veo la señal. Puedes parar."*
* pasado el 130 % sin verla → *"Pasos completados. Puedes parar."* +
  *"Llevas más pasos de los esperados y no veo la señal."*, y el
  flujo sigue como siempre en tramos intermedios. En el tramo final,
  con ultrasonido estable y aún por encima de 80 cm, se mantiene la
  aproximación lenta; entre 70 y 80 cm se detiene para volver a buscar
  la señal.

En ningún caso los pasos por sí solos anuncian la llegada: tras el
tramo entra en `CONFIRMANDO_LLEGADA` y pide girar despacio hasta ver
la señal del destino. Si ve la señal de **otra** zona, avisa *"Parece
que pasaste de largo. Estás en Sala."* y replanifica. Es el principio
rector: **mejor callar que dar una indicación errada**.

Detalle fino: ver la señal de la zona de cierre **dos veces** (la
puerta enfrente mientras se camina) no se lee como desviación — antes
provocaba un *"Te saliste de la ruta"* falso.

### Estimador de posición del tramo

El estado del filtro es la distancia recorrida y su varianza. Predice
con los pasos rectos y la zancada actual, y corrige con distancia visual
a la señal interior esperada y con el HC-SR04 estable si el tramo lo
habilita. La aplicación expone `posicion_m` e `incertidumbre_m`; puede
usar el límite superior de confianza para empezar antes el aviso de
búsqueda. El filtro **no sustituye la señal**: sin la señal correcta la
llegada no se confirma.

### 5.9 Zancada adaptativa: el paso real

Cada tramo del mapa lleva **dos** medidas: los `pasos` que se contaron
andándolo y la `distancia_m` con cinta métrica. Mientras no haya nada
medido, el plan anuncia los pasos del mapa (es el conteo con el que se
midio la casa).

En cuanto la cámara cierra un tramo dentro de la ventana (5.8), ese
tramo sirve para **medir la zancada real** de la persona:

```
medida  = distancia / pasos_acreditados
zancada = 0,30 · medida + 0,70 · zancada anterior   (EMA, recortada a 0,30 … 0,90 m)
pasos   = round(distancia / zancada)
```

A partir de ahí el plan recalcula los pasos con esa zancada, así el
número anunciado se acerca al paso real. La media móvil es
deliberada: un tramo raro (contado al revés) no descuadra el plan
siguiente. Y **solo mide el cierre verificado**: si el tramo se cerró
por pasos de más (130 % sin señal), no se sabe dónde acabó y no se
mide nada.

### 5.10 Re-localización: cada señal es un GPS a pie

Durante la navegación, cada señal interior confirmada corrige la
ubicación real:

* Es el destino → llegada verificada.
* Es la siguiente zona del camino → avanza el índice de zona.
* Está detrás o fuera del plan (te devolviste o tomaste otro camino) →
  *"Te saliste de la ruta hacia Baño. Estás en Sala. Vamos de nuevo."*
  y se recalcula la ruta (**replanificación**).

Además la voz permite recuperarse: *"Raspi, devolverme"* (vuelve a la
zona anterior de la ruta) y *"Raspi, otra ruta"* (recalcula al mismo
destino).

### 5.11 Seguridad

* **Caída**: el `DetectorCaida` exige **2 muestras consecutivas** con
  magnitud > 2.5 g y cambio brusco respecto a hace 0.3 s (mata glitches
  del bus I2C: un solo dato raro no dispara nada). Al dispararse: alarma
  **continua** de 60 s + *"Caída detectada. Di silencia cuando te
  hayas levantado."* y el micrófono entra en modo `SILENCIO` con
  gramática mínima (`["silencia", "silenciar", "apaga", ...]`): con el
  tono de 1200 Hz sonando, la gramática corta se reconoce rápido y ya
  no hace falta la palabra "Raspi". Hay 8 s de gracia al arrancar (la
  manipulación del equipo no cuenta como caída).
* **Proximidad**: el HC-SR04 se lee en hilo propio (`distancia_cm` nunca
  bloquea el bucle) y los pitidos se aceleran continuamente de 1.2 s a
  0.1 s según la distancia. Mientras la voz habla, los pitidos se
  callan solos (evento `locutor.hablando` compartido).

---

## 6. Voz: oír y hablar

* **Hablar**: Piper (modelo `es_MX-claude-high`) con **caché** por
  frase: la primera vez sintetiza a wav, después reproduce el archivo.
* **Escuchar**: Vosk (modelo pequeño español) en su propio hilo. El
  micrófono tiene **modos**, y cada modo define qué gramática usa y qué
  se publica:

| Modo | Gramática | Cuándo |
|---|---|---|
| `DESPERTADOR` | palabra clave "ras pi" + frases | Siempre; solo se publica lo que trae "Raspi" |
| `DESTINO` | nombres de lugares | Tras *"¿A dónde quieres ir?"* |
| `COMANDO` | órdenes sueltas | 8 s tras decir solo "Raspi" |
| `SILENCIO` | solo apagar la alarma | Mientras suena la alarma de caída |

Las gramáticas pequeñas = reconocimiento más rápido y más preciso en la
Raspberry. Detalle curioso para la presentación: "Raspi" no está en el
vocabulario del modelo, se pide como **"ras pi"** (que sí está) y lo
reconoce igual cuando la persona dice "Raspi".

---

## 7. Las pruebas automatizadas

`python pruebas/test_flujo.py` — corren sin hardware (con stubs del
sensor), y verifican el comportamiento completo. Ejemplos:

1. Comandos de voz y versión en ejecución.
2. Planificación de rutas (incluye rutas inversas espejadas).
3. Flujo completo: giro → señal → destino → navegación → llegada.
4. Destino pedido sin conocer la ubicación (no navega a ciegas).
5. Integración del giro (derecha/izquierda/reposo/giro lento).
6. Caídas (un glitch no dispara; un golpe real sí).
7. Proximidad (el beep se acelera).
8. Confirmación de señales tolerando parpadeos de la cámara.
9. Pasos sin fantasma con acelerómetro sin calibrar.
10. Encarada y **corrección guiada del desvío**.
11. Las dos caras de la puerta (interior vs pasillo).
12. Re-localización, llegada verificada y órdenes de voz de
    recuperación.
13. El giro de encarada según el sentido de la ruta (12
    combinaciones).
14. Obstáculos: en medio del camino y a los lados —los lados se
    nombra **sin "Alto"** y en una sola frase—.
15. Encarada intermedia al terminar un tramo.
16. Cámara y ultrasonido hablando del mismo obstáculo.
17. Pasos con una subida de postura de golpe (el pasa-banda la
    bloquea y la cuenta sigue).
18. El balanceo al caminar no desvía el rumbo.
19. Sesgo del giroscopio aprendido en marcha.
20. Confirmación temporal del desvío e histéresis de liberación.
21. Encarada automática al confirmar la zona.
22. **Un solo aviso por encuentro** de obstáculo: no repite mientras
    siga a la vista, se re-arma con el frame limpio y un objeto
    nuevo sí se anuncia enseguida.
23. **El paso sin ritmo de marcha se descarta** (no adelanta el fin
    del tramo) y el primer paso de un tramo nuevo siempre cuenta.
24. **Prioridad de la voz**: con la guía recién hablada el obstáculo
    espera su turno; pasado el silencio anuncia una sola vez y solo
    lo que está en la dirección que se recorre (sin *"persona"* ni
    lados).
25. **Obstáculos solo en la navegación**: dando pasos solo cuenta el
    fragmento central; girando para buscar la señal la cámara no
    anuncia nada.
26. **Giro sin desfase**: el corte se adelanta `grados_compensacion_giro`
    y *"Puedes parar"* sale primero en la frase.
27. **Llegada por ventana**: al 70 % avisa *"Reduce el paso, ya casi
    llegas"*; entre 70 % y 130 % **la cámara** cierra el tramo viendo
    la señal de cierre; pasado el 130 % sin señal avisa y el flujo
    sigue como siempre (sin anunciar llegadas falsas).
28. **Zancada adaptativa**: el plan usa los pasos del mapa mientras no
    haya medida; la EMA se actualiza solo con el cierre verificado por
    la cámara (el de 130 % no mide nada) y el plan siguiente calcula
    `distancia / zancada`.
29. **Banda del paso y ritmo re-baselado**: marcha lenta (1 s/paso),
    rápida (0,25 s) y suave (0,12 g) cuentan sin tocar la
    configuración; el temblor de 20 Hz (fuera de banda) no cuenta; y
    tres cruces seguidos fuera de cadencia olvidan el ritmo para
    arrancar otro nuevo.
30. **Evaluación offline**: un CSV registrado con la aplicación cerrada
    se reproduce con el mismo `ContadorPasos`; la prueba verifica que
    cuenta y calcula el error porcentual frente al conteo manual.
31. **Distancia visual**: el tamaño de caja produce alto/ancho en
    píxeles y `d=f·H/h`; una mediana de cinco frames reduce saltos y
    una focal sin calibrar no produce distancia.
32. **Odómetro opcional**: un tramo puede declarar la distancia frontal
    conocida al final; lecturas HC-SR04 nuevas y estables estiman
    `d_inicio - d_actual`, pero la señal de cámara sigue siendo la única
    confirmación de llegada.
33. **Estimador de posición**: pruebas sintéticas verifican predicción
    por zancada, corrección visual, propagación/reducción de
    incertidumbre y que esa incertidumbre inicia la búsqueda, pero no
    cierra un tramo sin señal interior.
34. **Puertas**: detecciones simuladas centradas/laterales verifican
    caja y distancia; varias apariciones de la misma puerta no duplican
    el conteo ni confirman zona/llegada.

Si en la presentación preguntan "¿cómo saben que funciona?": la respuesta
es esta suite (más la prueba física en la casa).

### Registro de una caminata real

En la Raspberry Pi, cierra `main.py` y ejecuta
`python pruebas/registrar_caminata.py --salida pruebas/caminata.csv`.
Camina contando los pasos manualmente y pulsa `Ctrl+C` al terminar. El
archivo conserva `t, ax, ay, az, gx, gy, gz` y la distancia del
ultrasonido (vacía cuando no hay eco). Reproduce el registro en la Pi o
en un PC con
`python pruebas/evaluar_pasos.py pruebas/caminata.csv --pasos-reales 42`.
El porcentaje es el error absoluto del conteo; úsalo para ajustar banda,
piso y factor del umbral en `guia/configuracion.py`. Los registros son
locales y pueden revelar patrones de movimiento: no los subas al repo
sin revisar su contenido.

Para estimar la distancia de la señal, mide su altura real y el alto de
su caja en un frame tomado a distancia conocida. Ejecuta
`python pruebas/calibrar_focal.py --distancia-m 1.5 --altura-real-m 0.20 --alto-px 96`
y copia la salida en `Configuracion.focal_px`; reemplaza las medidas del
ejemplo por las reales de la casa.

El HC-SR04 también puede estimar el avance en un pasillo con una pared
frontal conocida al final. Configura
`TramoRuta(distancia_frontal_fin_cm=...)` con esa distancia final,
ajusta `muestras_ultrasonido_odometro` y
`salto_maximo_ultrasonido_cm`, y valida las lecturas con el sensor. No
uses una puerta lateral como referencia frontal: el sensor debe mirar
hacia la pared o puerta de fin de tramo.

---

## 8. Guion sugerido para la demo en vivo

1. Encender y arrancar `python main.py` — mencionar los hilos y que el
   banner muestra el commit en ejecución.
2. Hacer el giro inicial en voz alta: el giro transcurre en silencio
   (antes se anunciaba *"Vas por la mitad"*) y al final *"Puedes
   parar. Ya giraste 90 grados."* (el corte se
   adelanta 8°: la orden sale primero mientras sigues girando)
3. Dejar que encuentre la señal: *"Estás en Inicio. ¿A dónde quieres
   ir?"*
4. Pedir *"Raspi, ve a la sala"* → encarar la señal → ruta.
5. Mostrar el giro de la ruta (en silencio) hasta *"Puedes parar. Giro
   completo."*
6. **Demostración del desvío**: caminar curvando a propósito → aviso,
   corrección guiada en vivo, *"Ya enderezaste. Los pasos vuelven a
   contar."*
7. Llegar: acercarse despacio hasta que el ultrasonido indique el rango
   configurado de 70–80 cm; después, la cámara debe confirmar la señal
   interior para anunciar *"Has llegado a Sala."*
8. (Opcional) Simular una caída (golpe suave al sensor) y silenciar
   diciendo solo *"silencia"*.
9. Cerrar con el principio: *"el sistema prefiere callar antes que
   dar una indicación errada"* (llegada verificada, no arrancar sin
   rumbo conocido, no navegar desde el lado del pasillo).

---

## 9. Preguntas típicas y posibles respuestas

**¿Hay que entrenar un modelo?** Las señales de animales están en el
modelo COCO. Para detectar marcos, hace falta además un modelo con la
clase `door`; los modelos incluidos ahora mismo no la contienen y el
programa lo advierte. La puerta no cambia la regla de identidad: solo
una señal interior confirma la zona.

**¿Cómo sabe en qué zona está?** Viendo la cara interior de la señal
de esa zona (3 detecciones seguidas). La cara del pasillo solo dice
"estás fuera de esa puerta".

**¿Y si se mueve la cámara o el sensor?** El giro usa la proyección
sobre la vertical (montaje libre) y el rumbo se re-ancla en cada señal.

**¿Qué pasa con la deriva del giroscopio?** Se aprende el sesgo en
marcha (ventana de extremos de 1 s, con gate de rumbo cerrado) y se
re-ancla el rumbo en cada señal; la llegada además se
verifica con la cámara (no depende solo de la integración).

**¿Por qué tantos hilos?** Para que la cámara (lenta, ~100-300 ms por
frame) nunca congele el muestreo del giroscopio (que debe ser a 100 Hz
fijos para no perder grados), ni la escucha del micrófono.

**¿Funciona en la oscuridad?** Sí: la navegación depende del
giroscopio y los pasos; la cámara solo se usa para localizarse y
confirmar.

**¿Cuánto cuesta / consume?** Raspberry Pi 5 + periféricos
comerciales; el software es todo de código abierto (YOLO, Vosk, Piper,
gpiozero).

**¿Por qué "Raspi"?** Es el despertador: sin palabra clave, el
reconocedor publicaría transcripciones falsas (ruido) las 24 horas.

---

## 10. Dónde ajustar cosas (si preguntan)

Todo umbral vive en `guia/configuracion.py`:

| Ajuste | Parámetro |
|---|---|
| Cuánto giro cuenta como desvío | `desvio_maximo_grados = 25` |
| Confirmación que debe durar el desvío antes de avisar | `confirmacion_desvio_s = 1.0` |
| Histéresis de liberación del aviso | `liberacion_desvio_factor = 0.6` |
| Delta de aceleración que vale como paso | `umbral_paso_min_g = 0.09` (piso) / `factor_umbral_paso = 0.45` (adaptativo) |
| Banda del paso (pasa-banda) | `frecuencia_paso_min_hz = 0.5` / `frecuencia_paso_max_hz = 5.0` |
| Jitter permitido antes de rediseñar el filtro | `variacion_recalculo_filtro_paso_pct = 0.05` (5 %, evita reinicios por variaciones de I²C) |
| Cadencia: tolerancia y mínimos para juzgar | `cadencia_tolerancia = 0.40` / `cadencia_intervalos_minimos = 3` |
| Re-basado del ritmo tras descartes seguidos | `rechazos_rebasar = 3` |
| Enfriamiento del aviso de desvío | `enfriamiento_desvio_s = 12` |
| Recordatorio durante la corrección | `RECORDATORIO_CORRECCION_S = 15` (en `aplicacion.py`) |
| Tiempo de búsqueda de señal | `tiempo_busqueda_s = 60` |
| Umbral de caída | `umbral_caida_g = 2.5` |
| Distancia de alerta | `distancia_alerta_cm = 300` |
| Adelanto del corte de giro | `grados_compensacion_giro = 8` |
| Pasos del tramo de re-búsqueda | `pasos_objetivo = 5` |
| Ventana de llegada del tramo | `ventana_llegada_inicio = 0.70` / `ventana_llegada_fin = 1.30` |
| Zancada: peso de la última medida (EMA) | `zancada_ema_alfa = 0.30` |
| Zancada: recorte de la medida | `zancada_min_m = 0.30` / `zancada_max_m = 0.90` |
| Mediana de distancia visual | `distancia_mediana_frames = 5` / `distancia_mediana_perdidas = 3` |
| Tamaño físico de señal/puerta | `altura_senal_m`, `altura_puerta_m`, `ancho_puerta_m` |
| Seguimiento horizontal de puerta | `salto_maximo_puerta_px = 160` / `frames_puerta_perdida = 4` |
| Ruido de las mediciones de posición | `varianza_medicion_senal_m2`, `varianza_medicion_ultrasonido_m2`, `varianza_medicion_puerta_m2` |
| Margen de incertidumbre de búsqueda | `sigmas_inicio_busqueda = 1.0` |

La casa (mapa) se define en `guia/dominio/rutas.py` (`TRAMOS_DEMO`):
cada tramo solo se mide en un sentido; el inverso se calcula con los
mismos pasos y los giros espejados. Ese mismo sentido decide el giro
de encarada (`PlanificadorRutas.lado_encarada`): las imágenes están
del lado **izquierdo** en el sentido normal (`GIRO_SENAL_NORMAL =
"izquierda"`), así que se encara a la izquierda, y al volver la
indicación se invierte (derecha, `GIRO_SENAL_INVERSO`). El lado no
está hardcodeado: sale de esas dos constantes, así que si algún día
se mueven las fotos de lado basta con cambiarlas ahí.
