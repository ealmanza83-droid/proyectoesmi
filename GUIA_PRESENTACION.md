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
3. **No miente**: nunca anuncia una llegada que no verificó; nunca
   da una instrucción medida desde una orientación que no conoce.
4. **Cuida la seguridad**: avisa obstáculos cercanos (pitidos que se
   aceleran) y detecta caídas (alarma continua + "di silencia").

**Dato clave para la presentación**: NO se entrenó ningún modelo de
inteligencia artificial nuevo. Se usa YOLO11n genérico (entrenado con
la base COCO de 80 clases) y cada zona de la casa se identifica con
fotos de animales que el modelo ya reconoce.

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
    caida.py                Detección de caídas por el acelerómetro
  vision/                   El "ojo"
    detector.py             YOLO + confirmación temporal de señales
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
  test_flujo.py             15 pruebas automatizadas sin hardware
  prueba_ultrasonido.py     Verificación directa del HC-SR04 en la Pi
  caracterizar_mpu6050.py   Offset y ruido del MPU6050 en reposo
mi_deteccion/               Modelos YOLO (yolo11n COCO activo)
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
| `ENCARAR_SENAL` | Pide poner la señal de la zona enfrente | Ve esa señal por su cara interior |
| `NAVEGANDO` | Ejecuta la ruta: giros integrados + pasos acreditados | Se agota el plan |
| `CONFIRMANDO_LLEGADA` | Pide girar despacio hasta ver la señal del destino | Ve la señal del destino |
| `DETENIDO` | Salida limpia | — |

**Historia completa de un recorrido** (el guion de la demo, sección 10):

1. Arranque: *"Gira 90 grados a la derecha suavemente para buscar la
   señal orientadora"* → avanza a mitad → *"Ya giraste 90 grados.
   Puedes parar."*
2. Búsqueda: *"Buscando la señal durante un minuto"* → la cámara ve la
   vaca (cara interior de Inicio) → *"Estás en Inicio. ¿A dónde
   quieres ir?"*
3. Destino: *"Raspi, ve a la sala"* → *"Entendido. Vamos a Sala."*
4. **Encarada**: como las rutas se midieron mirando la señal, se pide
   ponerla enfrente: *"Para saber hacia dónde miras, primero encárate
   con la señal de esta zona. Gira despacio a la derecha hasta que
   te diga que la tienes frente a ti."* → al verla: `RUMBO ANCLADO`
   (rumbo = 0°) → *"La señal está frente a ti."* El lado en el que
   gira sale del sentido de la ruta: a la derecha en el sentido
   normal y a la izquierda al volver, porque las imágenes cambian de
   lado.
5. Navegación: *"Gira a la izquierda 90 grados"* → *"Vas por la
   mitad"* → *"Giro completo. Puedes parar."* → *"Camina 13 pasos"*
   (hasta el Cuarto) → cuenta solo pasos rectos → *"Pasos
   completados. Puedes parar."* → **encarada intermedia**: como al
   terminar el tramo la persona queda de espaldas a la señal de esa
   zona, pide *"Gira despacio a la derecha hasta encontrar la señal
   de esta zona"* → al verla (*"La señal está frente a ti."*) sigue
   el segundo tramo: otro giro a la izquierda y *"Camina 10 pasos"*
   hasta la Sala.
6. Llegada verificada: *"Para confirmar la llegada, gira despacio a
   la derecha hasta encontrar la señal de Sala"* → al verla:
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
eso el `MedidorGiro` aprende el sesgo cuando la persona está en reposo
(constante de tiempo de 5 s) y el `Rumbo` se **re-ancla en cada señal
vista** (la señal es una referencia física conocida).

### 5.2 Rumbo: orientación relativa, no absoluta

El MPU6050 no tiene magnetómetro: **no sabe dónde está el norte**, ni
hace falta. Navegar basta con saber *hacia dónde mira la persona
respecto de la última señal que vio* (en ese instante la tiene enfrente,
rumbo = 0°). Entre señales, la rotación se acumula con el giroscopio.
Si la persona se gira y luego pide una ruta, el sistema lo sabe: por eso
exige la **encarada** antes de arrancar.

### 5.3 Pasos: picos de aceleración

Al caminar, el acelerómetro muestra picos con cada pisada. El
`ContadorPasos` detecta picos sobre la línea base dinámica (que se
adapta con constante de tiempo de 2 s), con separación mínima de 0.35 s
para no contar dos veces el mismo pisón. La línea base adaptativa es lo
que evita los **pasos fantasma** con el sensor sin calibrar.

### 5.4 Línea recta: solo cuentan los pasos rectos

Los pasos del mapa se midieron en línea recta. Un paso dado mientras la
persona va curvando **no la acerca al sitio medido** (la curva alarga el
recorrido), así que no se acredita: el rumbo compara contra el rumbo del
primer paso (`_rumbo_al_caminar`). Si la desviación supera 25°, los
pasos se congelan hasta enderezar.

### 5.5 Corrección guiada del desvío (lo más reciente)

Cuando el sistema detecta que vas curvando, **no manda un número fijo
de grados** ("gira 30°") porque ese número se queda viejo mientras la
voz habla y tú sigues caminando. En su lugar guía el giro **en vivo**:

1. *"Alto un momento. Te estás desviando de la línea recta, unos 30
   grados. Gira despacio a la izquierda hasta que te diga que ya
   enderezaste."*
2. Avances al 50% y 75% del giro de corrección: *"Llevas la mitad del
   giro para enderezar"*, *"Ya casi enderezas"*.
3. Al volver a la recta: *"Ya enderezaste. Los pasos vuelven a
   contar."*
4. Respaldo: si a los 15 s de terminar de sonar el aviso sigues fuera
   de la recta (o giraste al revés), se recuerda con los grados
   actualizados.

Los pasos de la corrección **tampoco se acreditan** hasta enderezar.

### 5.6 Visión: YOLO + confirmación temporal

`DetectorVisual` corre YOLO11n (NCNN sobre la Raspberry) y filtra las
detecciones por el catálogo (`dominio/catalogo.py`):

* **Señales orientadoras** (una foto por zona por cara de puerta).
* **Obstáculos** que se anuncian en voz alta con su nombre: silla,
  mesa (*"dining table"*, el nombre real en COCO), maceta. Si el
  objeto cae en la franja central del frame (0.30–0.70 del ancho) se
  considera **en medio del camino** y se avisa en una sola frase
  (*"Alto, en medio del camino hay un obstáculo: silla"*, o
  *"…hay obstáculos: silla y mesa"* si hay varias); los de los lados
  se nombra con su lado (*"Alto, hay un obstáculo a tu izquierda:
  maceta"*). La posición la calcula `posicion_en_frame` en
  `vision/detector.py`.

La cámara parpadea (una señal se ve 2 de cada 5 frames), así que una
señal solo se acepta tras **3 detecciones seguidas** (tolerando 2
pérdidas) — `ConfirmadorSenal`. Es confirmación, no promedio: cambiar de
señal reinicia la cuenta.

### 5.7 Señales de las dos caras de la puerta (idea original)

Cada puerta tiene **dos fotos**: la cara *interior* (se ve estando
dentro de la zona) y la cara del *pasillo* (se ve desde el otro lado).
Con el mismo modelo genérico, ocho clases de COCO:

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

Los pasos son una estimación: la deriva puede dejar a la persona a un
lado de la puerta. Por eso, tras *"Pasos completados"*, el sistema **no**
anuncia la llegada: entra en `CONFIRMANDO_LLEGADA` y pide girar despacio
hasta ver la señal del destino. Si ve la señal de **otra** zona, avisa
*"Parece que pasaste de largo. Estás en Sala."* y replanifica. Es el
principio rector: **mejor callar que dar una indicación errada**.

### 5.9 Re-localización: cada señal es un GPS a pie

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

### 5.10 Seguridad

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

## 7. Las 12 pruebas automatizadas

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

Si en la presentación preguntan "¿cómo saben que funciona?": la respuesta
es esta suite (más la prueba física en la casa).

---

## 8. Guion sugerido para la demo en vivo

1. Encender y arrancar `python main.py` — mencionar los hilos y que el
   banner muestra el commit en ejecución.
2. Hacer el giro inicial en voz alta: mostrar avances *"Vas por la
   mitad"* y *"Ya giraste 90 grados. Puedes parar."*
3. Dejar que encuentre la señal: *"Estás en Inicio. ¿A dónde quieres
   ir?"*
4. Pedir *"Raspi, ve a la sala"* → encarar la señal → ruta.
5. Mostrar el giro de la ruta con avances y *"Giro completo. Puedes
   parar."*
6. **Demostración del desvío**: caminar curvando a propósito → aviso,
   corrección guiada con avances, *"Ya enderezaste. Los pasos vuelven a
   contar."*
7. Llegar: *"Pasos completados"* → girar hasta ver la señal → *"Has
   llegado a Sala."*
8. (Opcional) Simular una caída (golpe suave al sensor) y silenciar
   diciendo solo *"silencia"*.
9. Cerrar con el principio: *"el sistema prefiere callar antes que
   dar una indicación errada"* (llegada verificada, no arrancar sin
   rumbo conocido, no navegar desde el lado del pasillo).

---

## 9. Preguntas típicas y posibles respuestas

**¿Hay que entrenar un modelo?** No. YOLO11n genérico COCO ya
reconoce las ocho fotos usadas; el "entrenamiento" fue imprimir las
fotos correctas en las caras correctas de cada puerta.

**¿Cómo sabe en qué zona está?** Viendo la cara interior de la señal
de esa zona (3 detecciones seguidas). La cara del pasillo solo dice
"estás fuera de esa puerta".

**¿Y si se mueve la cámara o el sensor?** El giro usa la proyección
sobre la vertical (montaje libre) y el rumbo se re-ancla en cada señal.

**¿Qué pasa con la deriva del giroscopio?** Se aprende el sesgo en
reposo y se re-ancla el rumbo en cada señal; la llegada además se
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
| Enfriamiento del aviso de desvío | `enfriamiento_desvio_s = 12` |
| Recordatorio durante la corrección | `RECORDATORIO_CORRECCION_S = 15` (en `aplicacion.py`) |
| Tiempo de búsqueda de señal | `tiempo_busqueda_s = 60` |
| Umbral de caída | `umbral_caida_g = 2.5` |
| Distancia de alerta | `distancia_alerta_cm = 200` |
| Pasos del tramo de re-búsqueda | `pasos_objetivo = 5` |

La casa (mapa) se define en `guia/dominio/rutas.py` (`TRAMOS_DEMO`):
cada tramo solo se mide en un sentido; el inverso se calcula con los
mismos pasos y los giros espejados. Ese mismo sentido decide el giro
de encarada (`PlanificadorRutas.lado_encarada`): las imágenes están
del lado derecho en el sentido normal, así que se encara a la
derecha, y al volver la indicación se invierte (izquierda).
