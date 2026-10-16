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
│   │   └── alarma.py        generación del beep de proximidad
│   ├── vision/
│   │   ├── detector.py      YOLO: señales + obstáculos (1 inferencia)
│   │   └── interfaz.py      ventana de diagnóstico
│   └── navegacion/
│       └── movimiento.py    MedidorGiro y ContadorPasos
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

```bash
python main.py
```

* `Escape` cierra el programa (también funciona `Ctrl+C`).
* La ventana *"Sistema de Orientacion"* muestra distancia, estado,
  ubicación y destino (solo diagnóstico: el usuario trabaja por audio).

### Prueba sin hardware

```bash
python pruebas/test_flujo.py
```

Comprueba comandos de voz, planificación de rutas y el flujo completo
(giro → señal → destino → navegación → llegada) con sensores simulados.

---

## Configuración importante

Todo vive en **`guia/configuracion.py`** (dataclass `Configuracion`):

| Qué | Parámetro | Valor por defecto |
|-----|-----------|-------------------|
| Confianza mínima de YOLO | `confianza_minima` | `0.75` |
| Tiempo de búsqueda de señal | `tiempo_busqueda_s` | `60` |
| Detecciones seguidas para confirmar una señal | `detecciones_senal_requeridas` | `3` |
| Offset del giroscopio (calibración) | `offset_giroscopio` | `0.445992` |
| Velocidad mínima para contar giro | `velocidad_minima_giro_dps` | `8.0` |
| Pasos a dar si no hay señal | `pasos_objetivo` | `5` |
| Pines HC-SR04 | `pin_echo` / `pin_trigger` | `23` / `18` |

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

---

## Decisiones de diseño

* **POO por capas**: hardware, audio, visión, navegación y dominio están
  separados; cada clase tiene una única responsabilidad y se le pasa por
  constructor (facilita simularla en pruebas).
* **Máquina de estados**: `Aplicacion` despacha a un manejador por estado;
  no hay banderas globales ni `if` encadenados de 700 líneas.
* **Hilos acotados**: solo dos (micrófono y alerta de proximidad), ambos
  con parada ordenada (`detener()` + `join`).
* **Sin bloqueos innecesarios**: la voz se atiende desde la cola de
  eventos en cada vuelta del bucle; la cámara sigue trabajando.
* **Una sola inferencia YOLO por frame**, con confirmación de 3 detecciones
  seguidas y enfriamiento de obstáculos (4 s) para no repetir anuncios.
* **Recursos gestionados**: `try/finally` y context managers garantizan
  cerrar cámara, I2C y GPIO aunque falle algo.
