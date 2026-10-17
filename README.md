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
│   │   └── ultrasonido.py   HC-SR04 + hilo de pitidos
│   ├── audio/
│   │   ├── locutor.py       Piper (voz) con caché de audios
│   │   ├── microfono.py     Vosk (escucha) en hilo propio
│   │   ├── gramaticas.py    gramáticas reducidas por modo
│   │   └── alarma.py        beep de proximidad + alarma continua de caída
│   ├── vision/
│   │   ├── detector.py      YOLO: señales + obstáculos (1 inferencia)
│   │   └── interfaz.py      ventana de diagnóstico
│   └── navegacion/
│       ├── movimiento.py    MuestreadorMPU (100 Hz) + MedidorGiro + ContadorPasos
│       └── caida.py         DetectorCaida (acelerómetro)
├── pruebas/test_flujo.py    prueba completa sin hardware
├── mi_deteccion/            modelos YOLO (best.pt, yolo11n_ncnn_model…)
├── piper_voices/            voces Piper (es_MX-claude-high…)
├── vosk/                    modelo Vosk en español
└── antiguo/                 scripts antiguos (ver antiguo/README.md)
```

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

---

## Configuración importante

Todo vive en **`guia/configuracion.py`** (dataclass `Configuracion`):

| Qué | Parámetro | Valor por defecto |
|-----|-----------|-------------------|
| Confianza mínima de YOLO | `confianza_minima` | `0.60` |
| Detecciones seguidas para confirmar una señal | `detecciones_senal_requeridas` | `3` |
| Frames perdidos tolerados antes de reiniciar la confirmación | `perdidas_senal_toleradas` | `2` |
| Tiempo de búsqueda de señal | `tiempo_busqueda_s` | `60` |
| Frecuencia de muestreo del MPU6050 | `intervalo_muestreo_mpu_s` | `0.01` (100 Hz) |
| Velocidad mínima para contar giro | `velocidad_minima_giro_dps` | `8.0` |
| Delta de aceleración que vale como paso | `delta_paso_g` | `0.12` g sobre el reposo |
| Pasos a dar si no hay señal | `pasos_objetivo` | `5` |
| Umbral de impacto para detectar caída | `umbral_caida_g` | `2.5` |
| Cambio brusco de aceleración que delata una caída | `umbral_cambio_caida_g` | `1.5` |
| Muestras seguidas que confirman una caída (mata glitches I2C) | `muestras_caida_requeridas` | `2` |
| Gracia al arrancar (ignora caidas mientras se manipula el equipo) | `gracia_caida_s` | `8` |
| Enfriamiento tras una caída (evita dobles alarmas) | `enfriamiento_caida_s` | `15` |
| Duración máxima del beep continuo de caída | `duracion_alarma_caida_s` | `60` |
| Pines HC-SR04 | `pin_echo` / `pin_trigger` | `23` / `18` |
| Chip lgpio del encabezado (None = autodetectar) | `chip_lgpio` | `None` |

Las rutas de los modelos se calculan desde la ubicación del proyecto:
funcionan igual en la Raspberry Pi que en el PC (sin rutas absolutas fijas).

### Señales orientadoras (`guia/dominio/catalogo.py`)

```python
SENALES_ORIENTADORAS = {"cow": "Inicio", "zebra": "Sala",
                        "giraffe": "Baño", "sheep": "Cuarto"}
OBJETOS_PERMITIDOS   = {"chair": "silla", "table": "mesa",
                        "potted plant": "maceta"}
```

### Mapa de la casa (`guia/dominio/rutas.py`)

```python
TRAMOS_DEMO = {
    ("Inicio", "Sala"): TramoRuta(pasos=10, giros=("derecha",)),
    ("Sala", "Baño"):   TramoRuta(pasos=6,  giros=("izquierda",)),
}
```

Añade aquí los tramos reales de tu casa; el planificador busca el camino
más corto aunque la ruta sea de varios tramos (por ejemplo
`Inicio → Sala → Baño`).

### Comandos de voz

Palabra clave: **"Raspi"** (también "ras pi", "ras py").

* *"Raspi"* → "Sí, dime." y abre 8 s para la orden.
* *"Raspi, ve al baño"* / *"quiero ir a la sala"* → inicia la ruta.
* *"Raspi, para"* / *"alto"* → pausa · *"reanuda"* / *"continuar"* → sigue.
* *"gira a la derecha"* / *"gira a la izquierda"* → indicación de giro.
* *"silencia"* / *"apaga"* / *"estoy bien"* → apaga la alarma de caída.

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
  (lineal entre 1.2 s de intervalo a 2 m y 0.1 s rozando el objeto).
  Si hay una alarma de caída en marcha, los pitidos se callan.

### Diagnóstico por consola

Mientras la persona gira, la consola imprime cada segundo una línea
como::

    GIRO derecha: 45.3/90 grados | vel +87.2 dps

La velocidad de giro es **positiva hacia la derecha** y se proyecta
sobre la vertical (funciona con el módulo plano, de canto o invertido).
Si al girar a la derecha la velocidad sale negativa, cambia
`signo_derecha` de `1` a `-1` en `guia/configuracion.py`.

Si el HC-SR04 no devuelve eco, la consola avisa `AVISO HC-SR04: sin
eco (revisa VCC, GND, ECHO y TRIG)` — **la guia sigue funcionando**
solo sin pitidos de proximidad (antes ese bloqueo congelaba todo el
sistema esperando el eco). Al reconectarlo aparece
`HC-SR04: eco recuperado (NN cm)`.

Si al arrancar aparece `AVISO HC-SR04: no se pudo usar el sensor`, la
causa habitual son **permisos** sobre `/dev/gpiochip*`: comprueba
`ls -l /dev/gpiochip*`, agrégate al grupo con
`sudo usermod -aG gpio $USER` (luego cierra sesión o reinicia) o
prueba una vez con `sudo venv/bin/python main.py`. El sensor inoperante
**nunca detiene la guía**: solo se quedan sin pitidos.

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
  seguidas y enfriamiento de obstáculos (4 s) para no repetir anuncios.
* **Recursos gestionados**: `try/finally` y context managers garantizan
  cerrar cámara, I2C y GPIO aunque falle algo.
