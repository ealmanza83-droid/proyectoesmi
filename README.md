# Guía de Orientación — Asistente para personas invidentes

Sistema que ayuda a una persona invidente a moverse dentro de la casa:
gira el dispositivo, busca una **señal orientadora** con la cámara, identifica
en qué zona está, escucha por voz **a dónde quiere ir** y le indica por el
parlante los giros y pasos hasta llegar. Mientras camina, los **pitidos del
ultrasonido** le avisan la distancia a los obstáculos.

Proyecto pensado para **Raspberry Pi 5**.

---

## Cómo funciona (flujo)

```
        ┌──────────────────────────────────────────────┐
        ▼                                              │
 1. Gira 90° a la derecha                              │
        ▼                                              │
 2. Busca señal orientadora (1 minuto)                 │
        ├── la encuentra ──► "Estás en <zona>"         │
        │                     "¿A dónde quieres ir?"   │
        │                          ▼                   │
        │               3. Calcula la ruta             │
        │                  gira / camina paso a paso   │
        │                  "Has llegado a <zona>" ─────┤
        │                                              │
        └── no encuentra ──► 4. Media vuelta (180°)    │
                                  │                    │
                            busca otra vez             │
                                  │                    │
                            no encuentra ──► 5. Camina │
                                              5 pasos ─┘
```

---

## Estructura del proyecto

```
proyectoesmi/
├── main.py                  ← punto de entrada
├── guia/                    ← código del sistema (POO)
│   ├── configuracion.py     parámetros: rutas, umbrales, pines
│   ├── aplicacion.py        orquestador + máquina de estados
│   ├── dominio/
│   │   ├── estados.py       fases del recorrido (Enum)
│   │   ├── catalogo.py      señales, obstáculos y destinos
│   │   ├── comandos.py      interpretación de lo que dice la persona
│   │   └── rutas.py         mapa de la casa + planificador (BFS)
│   ├── hardware/
│   │   ├── camara.py        Picamera2
│   │   ├── mpu6050.py       giroscopio + acelerómetro (I2C)
│   │   └── ultrasonido.py   HC-SR04 (gpiozero) + hilo de pitidos
│   ├── audio/
│   │   ├── locutor.py       Piper (voz) con caché de audios
│   │   ├── microfono.py     Vosk (escucha) en hilo propio
│   │   ├── gramaticas.py    gramáticas reducidas por modo
│   │   └── alarma.py        beep de proximidad + alarma continua de caída
│   ├── vision/
│   │   ├── detector.py      YOLO: señales, puertas + obstáculos (1 inferencia)
│   │   └── interfaz.py      ventana de diagnóstico
│   └── navegacion/
│       ├── movimiento.py    MuestreadorMPU (100 Hz) + MedidorGiro + ContadorPasos
│       ├── rumbo.py         Rumbo (orientación relativa anclada a la señal)
│       ├── posicion.py      Estimador 1D por pasos y mediciones de sensores
│       ├── odometro.py      Filtrado de avance opcional por ultrasonido
│       ├── puertas.py       Seguimiento horizontal y conteo opcional de puertas
│       └── caida.py         DetectorCaida (acelerómetro)
├── pruebas/test_flujo.py    prueba completa sin hardware
├── pruebas/prueba_ultrasonido.py  verificación del HC-SR04 en la Pi
├── pruebas/caracterizar_mpu6050.py  offset y ruido del MPU6050
├── pruebas/registrar_caminata.py  registro de sensores a CSV (app cerrada)
├── pruebas/evaluar_pasos.py       reproducción offline del contador de pasos
├── pruebas/calibrar_focal.py      calibración de la cámara por tamaño conocido
├── mi_deteccion/            modelos YOLO (best.pt, yolo11n_ncnn_model…)
├── piper_voices/            voces Piper (es_MX-claude-high…)
├── vosk/                    modelo Vosk en español
└── antiguo/                 scripts antiguos (ver antiguo/README.md)
```

### Documentación

* `README.md` (este) — comportamiento general del sistema.
* `GUIA_PRESENTACION.md` — explicación completa módulo por módulo:
  arquitectura, máquina de estados, algoritmos, guion de demo y
  preguntas típicas.
* `ECUACIONES.md` — inventario de las ecuaciones y modelos
  matemáticos (integración del giroscopio, proyección sobre la
  vertical, contadores de pasos, etc.).

---

## Requisitos e instalación (Raspberry Pi 5)

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-gpiozero \
                    python3-lgpio python3-smbus2 alsa-utils

pip install -r requirements.txt     # ultralytics, piper-tts, vosk, sounddevice...
```

> `picamera2` y `lgpio` es más seguro instalarlos con `apt` (vienen
> preparados para el sistema de cámaras de la Raspberry Pi).

---

## Ejecución

### Desde la consola (recomendado en la Raspberry Pi)

El `venv` **se crea en la Pi y dentro del proyecto**. Si copiaste la
carpeta desde Windows, borra el `venv` anterior: los entornos de
Windows tienen `Scripts/` en vez de `bin/` y no funcionan en Linux.

```bash
cd ~/proyectoesmi                # la carpeta del proyecto

rm -rf venv                      # solo si el venv vino de Windows
python3 -m venv --system-site-packages venv
source venv/bin/activate         # activa el entorno (aparece (venv) en la consola)

pip install -r requirements.txt

python main.py
```

> `--system-site-packages` es importante: hace que el entorno vea
> `picamera2` y `opencv` instalados con `apt` (si no, fallará con
> `ModuleNotFoundError: No module named 'picamera2'`).

Si al activar te dice `bash: venv/bin/activate: No such file or directory`
o `bad interpreter`, ese `venv` es de Windows: ejecuta `rm -rf venv` y
vuelve a crearlo con el comando de arriba.

Para salir del entorno: `deactivate`.

* `Escape` cierra el programa (también funciona `Ctrl+C`).
* La ventana *"Sistema de Orientacion"* muestra distancia, estado,
  ubicación y destino (solo diagnóstico: el usuario trabaja por audio).

### Desde Thonny

Thonny puede usar el mismo entorno: **Herramientas → Opciones →
Intérprete**, y en "Intérprete alternativo de Python" indica la ruta
del Python del entorno, por ejemplo:

```
/home/pi/proyectoesmi/venv/bin/python
```

### Prueba sin hardware

```bash
python pruebas/test_flujo.py
```

Comprueba comandos de voz, planificación de rutas, giro por
integración, caídas, proximidad y el flujo completo
(giro → señal → destino → navegación → llegada) con sensores simulados.

### Registrar y evaluar una caminata

Para grabar mientras sigues las instrucciones de navegación, inicia la
aplicación con el registro opcional. Se usan las lecturas del MPU6050 y
la última distancia del HC-SR04 que ya utiliza la aplicación; no se
abren conexiones adicionales a los sensores:

```bash
python main.py --registrar-caminata pruebas/caminata.csv
```

Realiza el recorrido normal y pulsa `Ctrl+C` al terminar. El CSV conserva
las pausas y cambios de rumbo, por lo que podrás contar después los pasos
reales que diste durante ese recorrido.

Como alternativa, con la aplicación principal **cerrada**, registra el
MPU6050 y el HC-SR04 directamente en la Pi. La captura termina con
`Ctrl+C`; también se puede limitar con `--duracion-s`:

```bash
python pruebas/registrar_caminata.py --salida pruebas/caminata.csv
python pruebas/registrar_caminata.py --salida pruebas/caminata.csv --duracion-s 30
```

Cuenta manualmente los pasos de la caminata y reproduce el archivo en la
Pi o en un PC para comparar el detector sin hardware:

```bash
python pruebas/evaluar_pasos.py pruebas/caminata.csv --pasos-reales 42
```

El CSV incluye `t, ax, ay, az, gx, gy, gz, distancia_ultrasonido`;
la distancia queda vacía si no hay eco. El evaluador informa los pasos
detectados y el error porcentual absoluto. Los CSV de caminatas pueden
contener datos personales y no se guardan automáticamente en el repositorio.

### Calibrar distancia visual

Mide la altura real de la señal (y configura `altura_senal_m` con ese
valor), colócala de frente a una distancia medida y anota el alto de su
caja en píxeles. Calcula la focal con:

```bash
python pruebas/calibrar_focal.py --distancia-m 1.5 --altura-real-m 0.20 --alto-px 96
```

Copia `focal_px` al `Configuracion`. El detector informa alto/ancho de
caja y distancia suavizada con la mediana de `distancia_mediana_frames`;
sin focal calibrada no publica una distancia.

---

## Configuración importante

Todo vive en **`guia/configuracion.py`** (dataclass `Configuracion`):

| Qué | Parámetro | Valor por defecto |
|-----|-----------|-------------------|
| Confianza mínima de YOLO | `confianza_minima` | `0.60` |
| Detecciones seguidas para confirmar una señal | `detecciones_senal_requeridas` | `3` |
| Frames perdidos tolerados antes de reiniciar la confirmación | `perdidas_senal_toleradas` | `2` |
| Tiempo de búsqueda de señal | `tiempo_busqueda_s` | `60` |
| Límite izquierdo del fragmento central (1/3 del ancho) | `obstaculo_centro_min` | `1/3` (0.333) |
| Límite derecho del fragmento central (2/3 del ancho) | `obstaculo_centro_max` | `2/3` (0.667) |
| Obstáculos solo en la navegación y en el fragmento central | `mirar_solo_centro_en_marcha` | `True` (fuera de la navegación la cámara no anuncia nada) |
| Silencio de la voz antes de que hable un obstáculo | `silencio_para_obstaculos_s` | `4.0` s |
| Objetos que sí se anuncian por voz | `obstaculos_en_voz` | `("silla", "mesa", "maceta")` |
| Anunciar también los fragmentos laterales | `anunciar_lados` | `False` |
| Frecuencia de muestreo del MPU6050 | `intervalo_muestreo_mpu_s` | `0.01` (100 Hz) |
| Velocidad mínima para contar giro (zona muerta y gate de rumbo) | `velocidad_minima_giro_dps` | `5.0` |
| Suavizado del giro sostenido que alimenta el rumbo | `tau_sostenida_giro_s` | `0.5` s |
| Confirmación que debe durar un desvío antes de avisar | `confirmacion_desvio_s` | `1.0` s |
| Fracción del umbral a la que se libera el aviso de desvío | `liberacion_desvio_factor` | `0.6` |
| Validez de la señal recién confirmada para arrancar sin encarar | `encarada_validez_s` | `30` s |
| Giro máximo desde el anclaje para dar la encarada por hecha | `encarada_max_giro_grados` | `20` |
| Adelanto del corte de giro (absorbe el giro de la frase) | `grados_compensacion_giro` | `12` grados |
| Banda de frecuencia del paso (pasa-banda de `ContadorPasos`) | `frecuencia_paso_min_hz` / `frecuencia_paso_max_hz` | `0.5` / `5.0` Hz (más abajo está el balanceo y la postura, más arriba el temblor) |
| Variación mínima de muestreo para rediseñar el filtro de pasos | `variacion_recalculo_filtro_paso_pct` | `0.05` (5 %; tolera el jitter normal de las lecturas I²C sin reiniciar el filtro) |
| Piso del umbral de paso (sobre la señal YA filtrada) | `umbral_paso_min_g` | `0.09` g |
| Umbral adaptativo | `factor_umbral_paso` | `0.45` (el umbral es `máx(0.09 g; 0.45 × media de los últimos picos contados)`) |
| Intervalo mínimo entre picos de paso | `intervalo_paso_s` | `0.18` s (un paso cada ~0.25 s a 1.85 m/s) |
| Ventana máxima para confirmar el primer paso tras una pausa | `intervalo_paso_max_s` | `1.5` s; el primer pico queda pendiente y se acredita con el siguiente (en `0` se desactiva) |
| Tolerancia de cadencia: solo descarta intervalos muy cortos | `cadencia_tolerancia` / `cadencia_intervalos_minimos` | `0.70` (descarta intervalos menores al 30 % de la mediana) / `3` intervalos antes de juzgar |
| Picos demasiado cercanos seguidos que re-baselan el ritmo | `rechazos_rebasar` | `3` (tras ellos se olvida la cadencia y el siguiente pico arranca un ritmo nuevo) |
| Zancada (metros que avanza con un paso) | `zancada_m` | `0.46` (semilla: hasta que no se mide la primera, el plan usa los pasos del mapa) |
| Peso de la última zancada medida (EMA) | `zancada_ema_alfa` | `0.30` |
| Recorte mínimo/máximo de la zancada medida | `zancada_min_m` / `zancada_max_m` | `0.30` / `0.90` m por paso |
| Velocidad de marcha | `velocidad_m_s` | `1.85` m/s |
| Pasos a dar si no hay señal | `pasos_objetivo` | `5` |
| Umbral mínimo para cerrar el tramo con señal de cámara | `ventana_llegada_inicio` | `0.70` (70 % de avance) |
| Fin de la ventana de llegada (límite para tramos intermedios) | `ventana_llegada_fin` | `1.30` (130 % de los pasos esperados) |
| Cadencia de paso si aún no hay suficientes muestras | `periodo_paso_llegada_s` | `0.6` s |
| Tiempo de reacción para estimar frenado | `tiempo_reaccion_llegada_s` | `0.8` s |
| Desaceleración usada para estimar frenado | `desaceleracion_llegada_m_s2` | `1.2` m/s² |
| Adelanto máximo adicional del aviso | `anticipacion_llegada_max_fraccion` | `0.15` (15 % del tramo) |
| Umbral de impacto para detectar caída | `umbral_caida_g` | `2.5` |
| Cambio brusco de aceleración que delata una caída | `umbral_cambio_caida_g` | `1.5` |
| Muestras seguidas que confirman una caída (mata glitches I2C) | `muestras_caida_requeridas` | `2` |
| Gracia al arrancar (ignora caidas mientras se manipula el equipo) | `gracia_caida_s` | `8` |
| Enfriamiento tras una caída (evita dobles alarmas) | `enfriamiento_caida_s` | `15` |
| Duración máxima del beep continuo de caída | `duracion_alarma_caida_s` | `60` |
| Pines HC-SR04 | `pin_echo` / `pin_trigger` | `23` / `18` (BCM) |
| Chip gpiozero del HC-SR04 (`/dev/gpiochipN`) | `chip_lgpio` | `15` |
| Rango de los pitidos (obstáculo **frontal**) | `distancia_alerta_cm` | `300` cm |
| Altura real de la señal impresa | `altura_senal_m` | `0.20` m (medir en la casa) |
| Altura/ancho real de puerta | `altura_puerta_m` / `ancho_puerta_m` | `2.0` / `0.9` m (medir en la casa) |
| Focal calibrada de la cámara | `focal_px` | `0` (desactivada hasta calibrar) |
| Ventana de mediana para distancia visual | `distancia_mediana_frames` | `5` frames |
| Frames perdidos que conservan la mediana visual | `distancia_mediana_perdidas` | `3` |
| Frames perdidos antes de rearmar el aviso de puerta | `frames_puerta_perdida` | `4` |
| Salto horizontal máximo al seguir una puerta | `salto_maximo_puerta_px` | `160` px |
| Proximidad insegura al confirmar señal | `distancia_puerta_min_cm` | `45` cm |
| Distancia para detener la aproximación sin señal visible | `distancia_puerta_max_cm` | `140` cm |
| Muestras ultrasónicas estables para aproximación | `muestras_ultrasonido_puerta` | `3` |
| Retención de pitido ante una pérdida breve de eco | `retencion_eco_ultrasonido_s` | `0.35` s (solo para el pitido; nunca para confirmar llegada) |
| Espera tras un aviso de obstáculo antes de confirmar llegada | `espera_llegada_tras_obstaculo_s` | `4` s |
| Muestras estables del odómetro ultrasónico | `muestras_ultrasonido_odometro` | `5` |
| Salto máximo entre lecturas estables | `salto_maximo_ultrasonido_cm` | `60` cm; cambios mayores se aceptan al repetirse consistentemente |
| Incertidumbre inicial de posición | `sigma_inicial_posicion_m` | `0.5` m |
| Ruido de predicción por paso | `varianza_proceso_paso_m2` | `0.0025` m² |
| Peso de mediciones visuales/ultrasónicas | `varianza_medicion_senal_m2` / `varianza_medicion_ultrasonido_m2` | `0.16` / `0.09` m² |
| Margen de incertidumbre para iniciar búsqueda | `sigmas_inicio_busqueda` | `1.0` σ |

Las rutas de los modelos se calculan desde la ubicación del proyecto:
funcionan igual en la Raspberry Pi que en el PC (sin rutas absolutas fijas).
El modelo configurado debe contener las clases de señales orientadoras y
`door` para detectar ambos. El modelo COCO incluido no tiene `door`, y el
`best_ncnn_model` incluido solo contiene `maceta`, `mesa` y `silla`; al
arrancar, el sistema avisa si faltan las clases de puerta. Entrena/exporta
un modelo con las clases necesarias y apunta `ruta_modelo` a ese modelo.

### Señales orientadoras (`guia/dominio/catalogo.py`)

```python
SENALES_ORIENTADORAS = {"cow": "Inicio", "zebra": "Sala",
                        "giraffe": "Baño", "sheep": "Cuarto"}
PUERTAS              = {"door": "puerta"}  # guía visual; no identifica zona
OBJETOS_PERMITIDOS   = {"chair": "silla", "table": "mesa",
                        "potted plant": "maceta"}
```

El detector devuelve las puertas en una lista aparte con caja,
centro horizontal, alto/ancho y rango. Durante la búsqueda, una puerta
visible sin señal confirmada se anuncia una vez por encuentro; el animal
detectado dentro de su caja tiene prioridad. El rango usa
`altura_puerta_m` (o `ancho_puerta_m` como respaldo) y la misma
`focal_px` calibrada. La puerta nunca cambia `ubicacion`.
Cuando la cámara ve centrada la señal interior esperada, esa señal
confirma el tramo si el camino está libre y no hay una lectura ultrasónica
estable que indique proximidad insegura (menos de `distancia_puerta_min_cm`).
No se exige una distancia exacta ni una lectura ultrasónica estable. Sin
señal visible, `distancia_puerta_max_cm` solo define cuándo detener la
aproximación final y volver a buscarla con la cámara.

### Mapa de la casa (`guia/dominio/rutas.py`)

La casa es una cadena: **Inicio → Cuarto → Sala → Baño**. Las señales
van impresas del lado **izquierdo** del pasillo, así que caminando
hacia adelante la persona gira a la **izquierda** para encarar la
imagen de cada puerta; al volver, las imágenes quedan a su derecha y
el sistema invierte el giro solo (derecha). Ese lado no está
hardcodeado: sale de `GIRO_SENAL_NORMAL` / `GIRO_SENAL_INVERSO` en
`guia/dominio/rutas.py`.

```python
TRAMOS_DEMO = {
    ("Inicio", "Cuarto"): TramoRuta(pasos=11, giros=("izquierda",), distancia_m=6.1),
    ("Cuarto", "Sala"):   TramoRuta(pasos=8,  giros=("izquierda",), distancia_m=4.4),
    ("Sala", "Baño"):     TramoRuta(pasos=31, giros=("izquierda",), distancia_m=19.7),
}
```

Cada tramo lleva **dos** medidas: `pasos` (el conteo de quien lo
anduvo) y `distancia_m` (cinta métrica). Son las dos caras de la
misma realidad y no hace falta que cuadren con una zancada fija:

| Tramo | Distancia | Pasos andados | Zancada implícita |
|-------|-----------|---------------|-------------------|
| Inicio → Cuarto | 6.1 m | 11 | 0.55 m |
| Cuarto → Sala | 10.5 - 6.1 = 4.4 m | 8 | 0.55 m |
| Sala → Baño | 30.2 - 10.5 = 19.7 m | 31 | 0.64 m |

(las distancias se midieron desde Inicio; el tramo intermedio es la
diferencia entre una medida y la siguiente).

Si un pasillo termina frente a una pared o puerta cuya distancia se
conoce, el tramo puede activar opcionalmente el odómetro con
`distancia_frontal_fin_cm`. El avance ultrasónico es la diferencia entre
la primera distancia estable y la actual; se corrige con la ventana de
llegada, pero **nunca confirma por sí solo** que se llegó.

El estimador por tramo predice posición con los pasos acreditados y la
zancada vigente, y corrige con la distancia a la señal esperada y, cuando
se configuró, con el HC-SR04 frontal. Expone posición e incertidumbre.
La incertidumbre puede iniciar antes el aviso de búsqueda, pero la ventana
solo se cierra al confirmar con cámara la señal interior esperada.

Un tramo también puede declarar `puertas_en_tramo` (cantidad de puertas
intermedias esperadas). El contador horizontal corrige débilmente la
posición al pasar cada puerta, una vez por detección; no la usa para
identificar la zona ni para anunciar llegada.

**Zancada adaptativa** (`guia/dominio/zancada.py`): mientras no haya
ninguna medida, el plan usa `pasos` (el conteo del mapa). En cuanto la
cámara cierra un tramo dentro de la ventana de llegada, se mide
`distancia_m / pasos` y se guarda como media móvil exponencial
(`zancada_ema_alfa`, recortada entre `zancada_min_m` y
`zancada_max_m`); a partir de ahí el plan recalcula
`round(distancia / zancada)`, así el número de pasos anunciado se
acerca al paso real de la persona. Solo sirve el cierre **verificado
con la cámara**: el cierre por pasos de más (130 % sin señal) no mide
nada, porque ahí no se sabe dónde acabó el tramo de verdad.

Cada tramo se pone **en un solo sentido**; el planificador calcula
solo el camino de vuelta con los mismos pasos y los giros espejados
(recorrer al reves un pasillo recto: lo que fue girar a la izquierda,
de vuelta es girar a la derecha).  Asi `Sala → Baño` implica
tambien `Baño → Sala`, y el planificador busca el camino mas corto
aunque la ruta cruce varios tramos (`Inicio → Cuarto → Sala → Baño`,
y de vuelta `Baño → Sala → Cuarto → Inicio`).

Para ajustar el mapa a tu casa, mide la distancia de cada tramo con
cinta métrica (`distancia_m`) y anota los pasos que diste andándolo
(`pasos`), más el giro inicial con el que se encara la señal de cada
puerta. La zancada exacta la calcula el sistema sola, al cerrar el
primer tramo con la cámara.

### Comandos de voz

Palabra clave: **"Raspi"** (también "ras pi", "ras py").

* *"Raspi"* → "Sí, dime." y abre 8 s para la orden.
* *"Raspi, ve al baño"* / *"quiero ir a la sala"* → inicia la ruta.
* *"Raspi, para"* / *"alto"* → pausa · *"reanuda"* / *"continuar"* → sigue.
* *"gira a la derecha"* / *"gira a la izquierda"* → indicación de giro.
* *"silencia"* / *"apaga"* / *"estoy bien"* → apaga la alarma de caída.

### Guía de giros y pasos por voz

Mientras la persona gira o camina, el sistema la guía en todo momento:

* **Durante el giro**: no se habla. Antes anunciaba *"Vas por la
  mitad"* (al 50 %) y *"Ya casi listo"* (al 75 %), pero la voz es
  lenta: mientras la persona gira de forma constante, cuando
  terminaba de decir la frase ya había girado más de lo pedido y el
  giro se deshacía. Ahora el giro transcurre en silencio y solo
  habla al final, con la orden de parar.
* **Al completar el giro** (el inicial de 90°, la media vuelta de 180°
  o el giro de una ruta): *"Puedes parar. Ya giraste 90 grados."* /
  *"Puedes parar. Giro completo."* — la **orden va primero** (es lo
  que hay que oir antes) y el corte se **adelanta** 12°
  (`grados_compensacion_giro`): mientras suena la frase y la
  instruccion siguiente la persona sigue girando, y sin ese adelanto
  el giro acababa ~10° pasado del objetivo (el desfase que se notaba
  al girar). Después pasa a la siguiente indicación (buscar señal,
  caminar, etc.).
* **Si te giras al lado equivocado**: esos grados **se descuentan** del
  giro pedido (el contador pasa a negativo y la etiqueta de la cámara
  lo marca con *"al lado contrario"*), así la instrucción no se da por
  completo hasta que la rotación **neta** llegue a los 90°/180° pedidos
  y, al parar, acabas mirando donde toca. Antes girar al lado contrario
  no se tenía en cuenta y el giro se completaba con la persona
  desviada.
* **Al acercarse al final de un tramo (ventana de llegada)**: los pasos
  son solo la estimación, así que el tramo ya **no** se da por
  terminado en "N pasos" exactos. El sistema calcula el aviso de
  reducción de paso según la cadencia reciente y una distancia de
  reacción/frenado, adelantándolo como máximo un 15 % del tramo; dice
  *"Reduce el paso; te indicaré dónde detenerte."* El aviso no cierra el
  tramo. Solo la cámara lo **cierra** si ve la señal interior de la zona
  siguiente entre el **70 %** y el **130 %**:
  *"Ya veo la señal. Puedes parar."* Si se pasa del 130 % sin verla,
  indica que se detenga y explica que aún no confirmó la señal; sigue
  el flujo de siempre (encarada intermedia o `CONFIRMANDO_LLEGADA`).
  En el tramo final, si no ve la señal, continúa despacio hasta la
  distancia máxima de búsqueda configurada y luego vuelve a buscarla.
  La llegada al destino **nunca** se anuncia sin haber visto su señal.
  Además ver
  la puerta de la zona de cierre mientras se camina no se lee como
  una desviación (antes provocaba un *"Te saliste de la ruta"* falso).

Así la persona nunca se queda sin saber cuándo detenerse ni en qué
punto del recorrido va.

### Rumbo: ¿sabe el sistema hacia dónde mira la persona?

El MPU6050 no tiene magnetómetro (no puede saber el norte), pero sí
puede mantener un **rumbo relativo**: el giroscopio integra toda la
rotación real (por encima del umbral de temblor) en una variable
`Rumbo`, que se **ancla en cero cada vez que la cámara confirma una
señal orientadora** — en ese instante la persona tiene la señal
enfrente, y esa es una referencia conocida.

Por eso, al pedir un destino el sistema **no arranca a ciegas**:

* Si la señal de la zona está a la vista → el rumbo se ancla y la
  ruta arranca de inmediato ("Entendido. Vamos a Baño." → "Gira a la
  izquierda 90 grados.").
* Si la señal **se acaba de confirmar** (la búsqueda la vio, o se
  mantuvo a la vista esperando el destino) y no has girado más de
  `encarada_max_giro_grados` (20°) en menos de `encarada_validez_s`
  (30 s) → también arranca directo, **sin volver a pedir encarar**:
  la encarada automática (`ENCARADA AUTOMATICA` en consola) da por
  hecho que sigues enfrente de tu señal. En casa las señales de las
  demás zonas están a pasos de distancia, así que pedirla otra vez
  dejaría a la persona girando sin salida. Si pasan más de 30 s o
  giras más de 20°, sí se pide encarar como siempre.
* Si **no** está a la vista → el nuevo estado *Encarar señal* pide
  girar despacio hasta encontrarla ("Para saber hacia dónde miras,
  primero encárate con la señal de esta zona..."); solo entonces se
  ancla el rumbo y se dan las instrucciones, que así se miden desde
  una orientación conocida. **El lado en el que hay que girar sale
  del sentido de la ruta** (`PlanificadorRutas.lado_encarada`): a la
  izquierda en el sentido normal y a la derecha al volver, porque las
  imágenes cambian de lado (ver *Mapa de la casa*). Lo mismo vale
  para confirmar la llegada a la puerta del destino.
* **Encarada intermedia**: al terminar los pasos de un tramo (quedan
  más tramos por delante) la persona normalmente queda mirando hacia
  donde vino, así que antes del giro del siguiente tramo se vuelve a
  pedir encarar la señal de la zona alcanzada; recién entonces se
  anuncia *"Gira a la izquierda 90 grados"* del tramo siguiente.

La deriva del giroscopio hace que el rumbo pierda precisión con los
minutos; por eso se vuelve a anclar en cada señal encontrada.

### Línea recta: ¿camina derecho mientras cuenta pasos?

Mientras la persona camina un tramo, el sistema vigila el rumbo: si
acumula más giro que `desvio_maximo_grados` (25° por defecto) respecto
del punto donde empezó a caminar, va curvando y los pasos no
llegarían al sitio medido. En ese caso la corrección se guía **en
vivo**: *"Alto un momento. Te estás desviando de la línea recta, unos
30 grados. Gira despacio a la izquierda hasta que te diga que ya
enderezaste"* — con el lado de la corrección calculado al revés del
desvío. Mientras gira, el sistema integra el giro de corrección, sin
avisos a mitad de giro (*"Llevas la mitad del giro para enderezar"*,
*"Ya casi enderezas"* se quitaron por el mismo motivo de desfase: la
voz tarda más que la persona en girar); si el desvio no baja vuelve a
avisar cada 15 s con el grados actualizado, y al entrar en la recta
anuncia *"Ya enderezaste.
Los pasos vuelven a contar"*. El número de grados del aviso es solo
contexto (cuánto te desviaste): el criterio para parar de girar es la
voz, no cumplir un número fijo que se quedaría viejo mientras la
persona sigue caminando.

Y no es solo un aviso: **los pasos dados mientras está desviado no se
acreditan**. El sensor sigue contando la marcha a ciegas, pero el
tramo solo avanza con pasos rectos — si no, el sistema daría por
"completos" los pasos del tramo caminando en curva y diría que has llegado a
un sitio que está en otro. Excepción basada en cámara: si YOLO confirma
la señal interior esperada del siguiente tramo y esta queda centrada,
esa referencia visual prevalece sobre un rumbo inercial discrepante;
los pasos hacia la señal cuentan y no se ordena girar alejándose de ella.
Un obstáculo frontal desactiva esta excepción. La señal centrada por sí
sola no confirma llegada: siguen siendo necesarios el rango ultrasónico
y las demás validaciones configuradas.

### Alertas de seguridad

* **Caída**: si el acelerómetro cambia de forma brusca (impacto o salto
  grande respecto a hace un instante), el sistema anuncia *"Caida
  detectada"* y enciende un **beep continuo** (no intermitente) durante
  60 s o hasta que la persona diga *"silencia"*. Para no alarmar por
  nada, el cambio debe durar 2 muestras seguidas (un glitch del bus I2C
  dura una sola lectura) y hay 8 s de gracia al arrancar, mientras se
  manipula el equipo.
* **Proximidad**: el ritmo de los pitidos del HC-SR04 **se acelera de
  forma continua** a medida que la persona se acerca al obstáculo
  (lineal entre 1.2 s de intervalo a 3 m —donde empiezan, a
  `distancia_alerta_cm`— y 0.1 s rozando el objeto).
  Si hay una alarma de caída en marcha, los pitidos se callan.
* **Obstáculos vistos con la cámara**: además de los pitidos, la
  cámara **nombra** lo que tiene delante. La imagen se divide en **3
  fragmentos iguales** (izquierda, centro, derecha); el central va de
  1/3 a 2/3 del ancho (`obstaculo_centro_min/max`) y es por donde se
  camina.

  * **Los obstáculos solo existen en la navegación**: mientras la
    persona camina la ruta. Girando sobre su propio eje para buscar
    la señal (o encarando, esperando) la cámara **no anuncia nada**;
    los pitidos del ultrasonido siguen por seguridad. Eso sí, la
    **señal orientadora nunca se recorta**: se lee siempre con los 3
    fragmentos de la imagen.
  * **Dando pasos** solo se tiene en cuenta el fragmento central:
    los objetos de los otros dos fragmentos se descartan por
    completo (`mirar_solo_centro_en_marcha`), porque avisar de lo que
    está a un lado solo genera confusión. Y *en medio* solo cuenta
    lo que **cabe entero** en ese fragmento (un objeto ancho que se
    sale de un lado es de ese lado, aunque su centro caiga dentro).
    Lo central se anuncia en
    una sola frase — *"Alto, en medio del camino hay un obstáculo:
    silla"* / *"…hay obstáculos: silla y mesa"*.
  * Solo se anuncia lo que puede estar en la **dirección que se
    recorre**: `obstaculos_en_voz` deja fuera a *"persona"* (se
    mueve, no bloquea el paso) y `anunciar_lados` = `False` deja
    mudos los lados (póntelo en `True` si quieres que se digan, en
    una sola frase y sin *"Alto"*).

  **La voz de la guía tiene prioridad** (`silencio_para_obstaculos_s`
  = 4 s): una instrucción (giro, pasos, llegada) jamás se atrasa ni
  se corta por un aviso de objeto; el aviso espera a que la voz
  quede callada ese tiempo.

  El anuncio es **una sola vez por encuentro**: mientras el objeto
  siga a la vista no se repite. Se re-arma cuando el frame queda
  limpio `enfriamiento_objeto_s` (4 s), de modo que un parpadeo de
  la cámara no lo dispara otra vez y un objeto **nuevo** sí se
  anuncia enseguida. El detector ya no aplica enfriamiento: reporta
  lo que se ve en el frame.

### Si algo falla con la posición en la imagen

Todo lo que depende de *dónde* cae un objeto o una señal en el frame
se ajusta en `guia/configuracion.py`, sin tocar código:

| Síntoma | Qué tocar |
|---------|-----------|
| Anuncia *"en medio del camino"* algo que claramente está a un lado | Estrecha `obstaculo_centro_min` / `obstaculo_centro_max` (p. ej. 0.38 / 0.62) |
| No anuncia algo que sí bloquea el paso | Amplía esos mismos dos valores (p. ej. 0.25 / 0.75) |
| Dice *"camina hacia ella"* con la señal todavía a un lado | Estrecha `guia_senal_centro_min` / `guia_senal_centro_max` (por defecto 0.40 / 0.60) |
| No ve la señal ni de cerca | Baja `confianza_minima` (0.60 → 0.50) o baja `detecciones_senal_requeridas` (3 → 2) |
| Ve la señal pero no la confirma (parpadea) | Sube `perdidas_senal_toleradas` (2 → 3) |

En la ventana de diagnóstico se dibujan **dos líneas verticales**
marcando el fragmento central (con la etiqueta *centro*): sirven para
ver de un vistazo si un objeto cae dentro o fuera. La posición se
decide con la **caja completa** del objeto: para ser *en medio del
camino* tiene que **caber entera** en el fragmento central; si se sale
de un solo lado es de ese lado (aunque su centro caiga dentro), y si
se sale de los dos lados —un frontal muy cerca que ocupa el central
entero— sí es del centro.

### Diagnóstico por consola

Mientras la persona gira, la consola imprime cada segundo una línea
como::

    GIRO derecha: 45.3/90 grados | vel +87.2 dps

La velocidad de giro es **positiva hacia la derecha** y se proyecta
sobre la vertical (funciona con el módulo plano, de canto o invertido).
Si al girar a la derecha la velocidad sale negativa, cambia
`signo_derecha` de `1` a `-1` en `guia/configuracion.py`.

Los pasos dejan su propia traza, útil para calibrar el umbral y para
ver si un tramo se cierra antes de tiempo::

    PASO: 12 (pico +0.33 g, umbral 0.09 g, cada 0.20 s)
    PASO PENDIENTE: reanudacion; se confirmara con el siguiente paso
    TRAMO: 45 pasos acreditados de 50 crudos

El `cada … s` es el intervalo desde el paso anterior: si sale muy
pegado al anterior hay doble conteo, y si un cruce llega con más del
límite (`intervalo_paso_max_s` = 1.5 s), el primer pico queda pendiente
hasta que otro pico confirme la reanudación. Un movimiento aislado
durante la pausa no cuenta; al reanudar se acreditan los dos primeros
pasos detectados. Al cerrar un tramo, `TRAMO` deja los pasos
**acreditados** (los que pide la ruta) junto a los **crudos** que sensó
el contador.

La medición del HC-SR04 se hace con **gpiozero** (`DistanceSensor` con
`LGPIOFactory`), exactamente igual que el script verificado en esta
Raspberry Pi 5 (`pruebas/prueba_ultrasonido.py`): chip lgpio 15, TRIG
en GPIO18 y ECHO en GPIO23. La primera lectura de `sensor.distance`
espera el primer eco (gpiozero tarda hasta ~0.5 s mientras calienta
su cola de muestras), por eso **solo se lee en el hilo propio** del
sensor: el bucle principal nunca se bloquea esperando el eco (antes
ese bloqueo congelaba la guía entera).

Si el sensor no devuelve eco, la consola avisa `AVISO HC-SR04: sin
eco (...)`. **La guia sigue funcionando** solo sin pitidos de
proximidad; al reconectarlo aparece
`HC-SR04: eco recuperado (NN cm)`.

El chip GPIO se elige por `chip_lgpio` (en esta Raspberry Pi funciona
el `15`; ese número va cambiando con las actualizaciones del kernel).
El número real de cada chip se ve con `ls /dev/gpiochip*`.

Si al arrancar aparece `AVISO HC-SR04: no se pudo usar el sensor`, la
consola indica el motivo exacto. Para verificar el sensor por separado
(con la aplicación principal **cerrada**, si no los dos pelean por los
pines) corre en la Pi:

```bash
cd ~/Proyectoesmi && source venv/bin/activate && git pull origin main
python pruebas/prueba_ultrasonido.py
```

Si la ventana muestra la distancia cambiando al poner la mano delante,
el sensor y su cableado están bien. Las causas habituales del fallo
son: gpiozero sin instalar (`sudo apt install python3-gpiozero` o
`venv/bin/pip install gpiozero`), **permisos** sobre `/dev/gpiochip*`
(comprueba `ls -l /dev/gpiochip*`, agrégate al grupo con
`sudo usermod -aG gpio $USER` y reinicia), o TRIG/ECHO ocupados o mal
conectados: VCC→5V, GND→GND y ECHO con **divisor de voltaje** (el ECHO
del HC-SR04 habla a 5V y el GPIO de la Raspberry Pi solo admite 3.3V).
El sensor inoperante **nunca detiene la guía**: solo se queda sin
pitidos.

Para caracterizar el MPU6050 —**offset y desviación estándar del
ruido en reposo**, los dos datos del giroscopio y el acelerómetro—
corre con la aplicación **cerrada** (los dos no deben leer el I2C a
la vez) y el equipo quieto y plano sobre la mesa:

```bash
python pruebas/caracterizar_mpu6050.py 10 --corridas 5   # recomendado
python pruebas/caracterizar_mpu6050.py                   # una sola corrida
```

Con **3 o más corridas** el script descarta automáticamente las que
salgan con sigma anómalo (> 3 veces la mediana de ese eje, p.ej. la
primera corrida mientras la mesa aún se asentaba) y al final imprime
el **resumen consolidado**: media, dispersión (mín..máx) y sigma por
eje, con los datos listos para copiar al reporte. Siempre imprime la
tasa real de muestreo y la comparación del ruido con los umbrales de
`configuracion.py` (`umbral_paso_min_g`, dead-band de giros).

El driver enciende el sensor con su **filtro digital (DLPF)**
activo: 44 Hz de ancho de banda en el acelerómetro y 42 Hz en el
giroscopio (la consola arranca con `MPU6050 OK (DLPF 44/42 Hz)`).
Con el valor por defecto (260/256 Hz) el sensor entregaba todo el
ruido; con el filtro la sigma en reposo baja aproximadamente a la
mitad sin tocar las señales de la persona (pasos de 2 a 6 Hz,
giros por debajo de 30 Hz). Los valores **sin filtro** quedan en la
caracterización anterior: tras el `git pull` vuelve a correr el
script y compara la sigma antes/después.

---

## Decisiones de diseño

* **POO por capas**: hardware, audio, visión, navegación y dominio están
  separados; cada clase tiene una única responsabilidad y se le pasa por
  constructor (facilita simularla en pruebas).
* **Máquina de estados**: `Aplicacion` despacha a un manejador por estado;
  no hay banderas globales ni `if` encadenados de 700 líneas.
* **Hilos acotados**: cuatro (micrófono, alerta de proximidad, muestreador
  del MPU6050 a 100 Hz y alarma de caída), todos con parada ordenada
  (`detener()` + `join`). El giroscopio mide **velocidad angular**, no
  ángulos: el muestreador integra esa velocidad a frecuencia fija para
  no perder grados aunque la cámara o la voz relentice el bucle.
* **Sin bloqueos innecesarios**: la voz se atiende desde la cola de
  eventos en cada vuelta del bucle; la cámara sigue trabajando.
* **Una sola inferencia YOLO por frame**, con confirmación de 3 detecciones
  seguidas; los obstáculos se anuncian **una vez por encuentro** (se
  re-arma tras `enfriamiento_objeto_s` de frame limpio).
* **Recursos gestionados**: `try/finally` y context managers garantizan
  cerrar cámara, I2C y GPIO aunque falle algo.
