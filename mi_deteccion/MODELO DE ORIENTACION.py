import cv2
import time
import subprocess
import wave
import math
import struct
import os
import json
import queue
import threading
import unicodedata

from ultralytics import YOLO
from picamera2 import Picamera2

from gpiozero import DistanceSensor
from gpiozero.pins.lgpio import LGPIOFactory

from smbus2 import SMBus

from piper import PiperVoice

import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel


# ============================================================
# CONFIGURACION
# ============================================================

ANCHO = 640
ALTO = 480

CONF_YOLO = 0.75

# BUSQUEDA DE SEÑAL = 1 MINUTO
TIEMPO_BUSQUEDA = 60



# RUTAS

RUTA_MODELO = (
    "/home/esmeralda/Proyectoesmi/"
    "mi_deteccion/yolo11n_ncnn_model"
)

RUTA_VOZ = (
    "/home/esmeralda/Proyectoesmi/"
    "piper_voices/es_MX-claude-high.onnx"
)

RUTA_VOSK = (
    "/home/esmeralda/Proyectoesmi/"
    "vosk/vosk-model-small-es-0.42"
)



# MICROFONO
MICROFONO_ID = None

MIC_WAKE = "wake"
MIC_DESTINO = "destino"
MIC_COMANDO = "comando"
MIC_OFF = "off"

modo_microfono = MIC_OFF

lock_mic = threading.Lock()

cola_audio_mic = queue.Queue()

cola_eventos_voz = queue.Queue()

detener_mic = threading.Event()

# ESTADOS
GIRODERECHA = 0
BUSCAR_SENAL1 = 1
MEDIA_VUELTA_IZQUIERDA = 2
BUSCAR_SENAL2 = 3
CINCO_PASOS = 4
ESPERANDO_DESTINO = 5
NAVEGANDO = 6


estado = GIRODERECHA



# UBICACION / DESTINO


ubicacion_actual = None
destino_actual = None



# SEÑALES ORIENTADORAS

SENALES_ORIENTADORAS = {
    "cow": "Inicio",
    "zebra": "Sala",
    "giraffe": "Baño",
    "sheep": "Cuarto"
}


#LIMITACION DE YOLO 

OBJETOS_PERMITIDOS = {
    "chair": "silla",
    "table": "mesa",
    "potted plant": "maceta",
 
}


CLASES_PERMITIDAS = (
    set(SENALES_ORIENTADORAS.keys())
    |
    set(OBJETOS_PERMITIDOS.keys())
)



# RUTAS

RUTAS = {
    ("Inicio", "Sala"):{
       "pasos": 10,
        "giros": ["derecha"]
    }
 }
        



# AUDIO

CARPETA_AUDIO = "/tmp/audio_orientacion"

os.makedirs(
    CARPETA_AUDIO,
    exist_ok=True
)



# PIPER

print()
print("CARGANDO PIPER")


voz = PiperVoice.load(
    RUTA_VOZ
)

print("Piper OK")              


AUDIOS = {}

voz_hablando = threading.Event()


def normalizar_nombre_archivo(texto):

    texto = unicodedata.normalize(
        "NFKD",
        texto
    )

    texto = texto.encode(
        "ascii",
        "ignore"
    ).decode(
        "ascii"
    )

    salida = []

    for caracter in texto.lower():

        if caracter.isalnum():

            salida.append(caracter)

        else:

            salida.append("_")

    return "".join(
        salida[:80]
    )


def generar_audio(texto):

    if texto in AUDIOS:

        return AUDIOS[texto]


    nombre = (
        normalizar_nombre_archivo(texto)
        + ".wav"
    )


    ruta = os.path.join(
        CARPETA_AUDIO,
        nombre
    )


    with wave.open(
        ruta,
        "wb"
    ) as archivo:

        voz.synthesize_wav(
            texto,
            archivo
        )


    AUDIOS[texto] = ruta

    return ruta


# ============================================================
# MICROFONO - CONTROL
# ============================================================

def establecer_modo_microfono(modo):

    global modo_microfono

    with lock_mic:

        modo_microfono = modo


    while True:

        try:

            cola_audio_mic.get_nowait()

        except queue.Empty:

            break


def obtener_modo_microfono():

    with lock_mic:

        return modo_microfono


# ============================================================
# HABLAR
# ============================================================

def hablar(
    texto,
    modo_retorno=None
):

    print(
        "VOZ:",
        texto
    )


    # El microfono no debe escuchar
    # mientras la Raspberry habla.

    establecer_modo_microfono(
        MIC_OFF
    )


    voz_hablando.set()


    try:

        ruta = generar_audio(
            texto
        )


        proceso = subprocess.Popen(
            [
                "aplay",
                "-q",
                ruta
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )


        proceso.wait()


    except Exception as error:

        print(
            "ERROR DE AUDIO:",
            error
        )


    finally:

        voz_hablando.clear()


    if modo_retorno is not None:

        establecer_modo_microfono(
            modo_retorno
        )


# ============================================================
# BEEP
# ============================================================

RUTA_BEEP = os.path.join(
    CARPETA_AUDIO,
    "beep.wav"
)


def crear_beep():

    frecuencia = 1000

    duracion = 0.08

    frecuencia_muestreo = 44100

    muestras = int(
        frecuencia_muestreo *
        duracion
    )


    with wave.open(
        RUTA_BEEP,
        "wb"
    ) as archivo:

        archivo.setnchannels(1)

        archivo.setsampwidth(2)

        archivo.setframerate(
            frecuencia_muestreo
        )


        for i in range(
            muestras
        ):

            valor = int(
                15000
                *
                math.sin(
                    2
                    *
                    math.pi
                    *
                    frecuencia
                    *
                    i
                    /
                    frecuencia_muestreo
                )
            )


            archivo.writeframes(
                struct.pack(
                    "<h",
                    valor
                )
            )


crear_beep()


proceso_beep = None


def reproducir_beep():

    global proceso_beep


    # No interrumpir la voz.

    if voz_hablando.is_set():

        return


    # Evitar superponer beeps.

    if (
        proceso_beep is not None
        and proceso_beep.poll() is None
    ):

        return


    try:

        proceso_beep = subprocess.Popen(
            [
                "aplay",
                "-q",
                RUTA_BEEP
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )


    except Exception as error:

        print(
            "ERROR BEEP:",
            error
        )


# ============================================================
# FRECUENCIA DEL BEEP
# ============================================================

def intervalo_beep(
    distancia_cm
):

    # MAS DE 2 METROS = SILENCIO

    if distancia_cm > 200:

        return None


    # 1.50 - 2.00 m

    if distancia_cm > 150:

        return 1.20


    # 1.00 - 1.50 m

    if distancia_cm > 100:

        return 0.70


    # 0.50 - 1.00 m

    if distancia_cm > 50:

        return 0.35


    # 0.25 - 0.50 m

    if distancia_cm > 25:

        return 0.18


    # MENOS DE 25 cm

    return 0.12



# HC-SR04
print("INICIALIZANDO HC-SR04")



factory = LGPIOFactory(
    chip=15
)


sensor = DistanceSensor(
    echo=23,
    trigger=18,
    pin_factory=factory,
    max_distance=4
)


print(
    "HC-SR04 OK"
)


# HILO HC-SR04


detener_alerta = threading.Event()


def hilo_alerta_ultrasonica():

    ultima_alerta = 0


    while not detener_alerta.is_set():

        try:

            distancia = (
                sensor.distance
                *
                100
            )


            intervalo = intervalo_beep(
                distancia
            )


            ahora = time.monotonic()


            if (
                intervalo is not None
                and
                ahora -
                ultima_alerta
                >=
                intervalo
            ):

                reproducir_beep()

                ultima_alerta = ahora


        except Exception as error:

            print(
                "ERROR HC-SR04:",
                error
            )


        time.sleep(
            0.03
        )



# MPU6050

print("INICIALIZANDO MPU6050")

MPU6050_ADDR = 0x68

PWR_MGMT_1 = 0x6B

ACCEL_XOUT_H = 0x3B


bus = SMBus(1)


bus.write_byte_data(
    MPU6050_ADDR,
    PWR_MGMT_1,
    0
)


print(
    "MPU6050 OK"
)


# Offset que ya habias calculado
GYRO_Z_OFFSET = 0.445992


# Umbral para considerar que realmente esta girando
MIN_GIRO_DPS = 8.0


# IMPORTANTE:
#
# 1 = derecha corresponde a GZ positivo
# -1 = derecha corresponde a GZ negativo
#
# Por defecto dejamos 1.
#
SIGNO_DERECHA = 1


def convertir_16bits(
    valor
):

    if valor > 32767:

        valor -= 65536

    return valor


def leer_mpu6050():

    datos = bus.read_i2c_block_data(
        MPU6050_ADDR,
        ACCEL_XOUT_H,
        14
    )


    ax_raw = convertir_16bits(
        (datos[0] << 8)
        |
        datos[1]
    )


    ay_raw = convertir_16bits(
        (datos[2] << 8)
        |
        datos[3]
    )


    az_raw = convertir_16bits(
        (datos[4] << 8)
        |
        datos[5]
    )


    gx_raw = convertir_16bits(
        (datos[8] << 8)
        |
        datos[9]
    )


    gy_raw = convertir_16bits(
        (datos[10] << 8)
        |
        datos[11]
    )


    gz_raw = convertir_16bits(
        (datos[12] << 8)
        |
        datos[13]
    )


    ax = ax_raw / 16384.0

    ay = ay_raw / 16384.0

    az = az_raw / 16384.0


    gx = gx_raw / 131.0

    gy = gy_raw / 131.0

    gz = gz_raw / 131.0


    return (
        ax,
        ay,
        az,
        gx,
        gy,
        gz
    )



# CAMARA
print("INICIALIZANDO CAMARA")
camera = Picamera2()


camera.configure(
    camera.create_video_configuration(
        main={
            "format": "YUYV",
            "size": (
                ANCHO,
                ALTO
            )
        }
    )
)


camera.start()


time.sleep(2)


print(
    "CAMARA OK"
)



# YOLO
print()
print("======================================")
print("CARGANDO YOLO")
print("======================================")


model = YOLO(
    RUTA_MODELO,
    task="detect"
)


print(
    "YOLO OK"
)



# VOSK


print()
print("======================================")
print("CARGANDO RECONOCIMIENTO DE VOZ")
print("======================================")


SetLogLevel(-1)


if not os.path.exists(
    RUTA_VOSK
):

    print(
        "ADVERTENCIA: no se encontro "
        "el modelo Vosk:"
    )

    print(
        RUTA_VOSK
    )

else:

    print(
        "Modelo Vosk encontrado."
    )


modelo_vosk = None


# ============================================================
# NORMALIZAR TEXTO
# ============================================================

def normalizar_texto(
    texto
):

    texto = texto.lower()


    texto = unicodedata.normalize(
        "NFD",
        texto
    )


    texto = "".join(
        c
        for c in texto
        if unicodedata.category(c)
        != "Mn"
    )


    return texto


# ============================================================
# DESTINO
# ============================================================

def extraer_destino(
    texto
):

    texto = normalizar_texto(
        texto
    )


    if "bano" in texto:

        return "Baño"


    if "sala" in texto:

        return "Sala"


    if "cuarto" in texto:

        return "Cuarto"


    if "cocina" in texto:

        return "Cocina"


    if "inicio" in texto:

        return "Inicio"


    return None


# ============================================================
# COMANDO
# ============================================================

def reconocer_comando(
    texto
):

    texto = normalizar_texto(
        texto
    )


    destino = extraer_destino(
        texto
    )


    if destino is not None:

        return (
            "destino",
            destino
        )


    if (
        "para" in texto
        or
        "alto" in texto
        or
        "detente" in texto
    ):

        return (
            "parar",
            None
        )


    if (
        "reanuda" in texto
        or
        "reanudar" in texto
        or
        "continua" in texto
        or
        "continuar" in texto
    ):

        return (
            "reanudar",
            None
        )


    if (
        "gira a la derecha"
        in texto
    ):

        return (
            "girar_derecha",
            None
        )


    if (
        "gira a la izquierda"
        in texto
    ):

        return (
            "girar_izquierda",
            None
        )


    return (
        "desconocido",
        None
    )


# ============================================================
# QUITAR RASPI
# ============================================================

def quitar_raspi(
    texto
):

    texto = normalizar_texto(
        texto
    )


    texto = texto.replace(
        "raspi",
        ""
    )


    texto = texto.replace(
        "ras pi",
        ""
    )


    texto = texto.replace(
        "ras py",
        ""
    )


    return texto.strip()


# ============================================================
# SELECCIONAR MICROFONO
# ============================================================

def seleccionar_microfono():

    if MICROFONO_ID is not None:

        return MICROFONO_ID


    dispositivos = sd.query_devices()

    candidatos = []


    for i, dispositivo in enumerate(
        dispositivos
    ):

        if (
            dispositivo[
                "max_input_channels"
            ]
            <= 0
        ):

            continue


        nombre = dispositivo[
            "name"
        ].lower()


        puntuacion = 0


        if "usb" in nombre:

            puntuacion += 3


        if "microphone" in nombre:

            puntuacion += 2


        if "mic" in nombre:

            puntuacion += 1


        if "headset" in nombre:

            puntuacion += 1


        if puntuacion > 0:

            candidatos.append(
                (
                    puntuacion,
                    i,
                    dispositivo[
                        "name"
                    ]
                )
            )


    if candidatos:

        candidatos.sort(
            reverse=True
        )

        return candidatos[0][1]


    return sd.default.device[0]


# ============================================================
# CALLBACK MICROFONO
# ============================================================

def callback_microfono(
    indata,
    frames,
    tiempo,
    status
):

    if status:

        print(
            "MIC:",
            status
        )


    cola_audio_mic.put(
        bytes(indata)
    )


# ============================================================
# GRAMATICAS
# ============================================================

GRAMATICA_WAKE = json.dumps(
    [
        "raspi",
        "ras pi",
        "ras py",

        "raspi baño",
        "raspi bano",
        "raspi sala",
        "raspi cuarto",
        "raspi cocina",

        "raspi ve al baño",
        "raspi ve a la sala",
        "raspi ve al cuarto",
        "raspi ve a la cocina",

        "raspi para",
        "raspi alto",
        "raspi detente",

        "raspi reanuda",
        "raspi continuar",

        "raspi gira a la derecha",
        "raspi gira a la izquierda",

        "[unk]"
    ],
    ensure_ascii=False
)


GRAMATICA_DESTINO = json.dumps(
    [
        "baño",
        "bano",
        "sala",
        "cuarto",
        "cocina",
        "inicio",

        "quiero ir al baño",
        "quiero ir a la sala",
        "quiero ir al cuarto",
        "quiero ir a la cocina",

        "raspi baño",
        "raspi bano",
        "raspi sala",
        "raspi cuarto",
        "raspi cocina",

        "[unk]"
    ],
    ensure_ascii=False
)


GRAMATICA_COMANDO = json.dumps(
    [
        "baño",
        "bano",
        "sala",
        "cuarto",
        "cocina",
        "inicio",

        "quiero ir al baño",
        "quiero ir a la sala",
        "quiero ir al cuarto",
        "quiero ir a la cocina",

        "ve al baño",
        "ve a la sala",
        "ve al cuarto",
        "ve a la cocina",

        "para",
        "alto",
        "detente",

        "reanuda",
        "reanudar",
        "continua",
        "continuar",

        "gira a la derecha",
        "gira a la izquierda",

        "[unk]"
    ],
    ensure_ascii=False
)


# ============================================================
# HILO MICROFONO
# ============================================================

def hilo_microfono():

    global modelo_vosk


    try:

        if not os.path.exists(
            RUTA_VOSK
        ):

            print(
                "ERROR: falta el modelo Vosk."
            )

            return


        modelo_vosk = Model(
            RUTA_VOSK
        )


        dispositivo = (
            seleccionar_microfono()
        )


        info = sd.query_devices(
            dispositivo,
            "input"
        )


        nombre = info[
            "name"
        ]


        frecuencia = int(
            info[
                "default_samplerate"
            ]
        )


        if frecuencia <= 0:

            frecuencia = 16000


        print(
            "MICROFONO:",
            nombre
        )


        print(
            "FRECUENCIA:",
            frecuencia
        )


        reconocedor = None

        modo_anterior = None


        with sd.RawInputStream(

            samplerate=frecuencia,

            blocksize=4000,

            device=dispositivo,

            dtype="int16",

            channels=1,

            callback=callback_microfono

        ):


            print(
                "MICROFONO OK"
            )


            while (
                not detener_mic.is_set()
            ):


                modo = (
                    obtener_modo_microfono()
                )


                # --------------------------------------------
                # CAMBIO DE MODO
                # --------------------------------------------

                if modo != modo_anterior:

                    reconocedor = None

                    modo_anterior = modo


                    if modo == MIC_WAKE:

                        reconocedor = (
                            KaldiRecognizer(
                                modelo_vosk,
                                frecuencia,
                                GRAMATICA_WAKE
                            )
                        )


                    elif modo == MIC_DESTINO:

                        reconocedor = (
                            KaldiRecognizer(
                                modelo_vosk,
                                frecuencia,
                                GRAMATICA_DESTINO
                            )
                        )


                    elif modo == MIC_COMANDO:

                        reconocedor = (
                            KaldiRecognizer(
                                modelo_vosk,
                                frecuencia,
                                GRAMATICA_COMANDO
                            )
                        )


                    while True:

                        try:

                            cola_audio_mic.get_nowait()

                        except queue.Empty:

                            break


                if (
                    modo == MIC_OFF
                    or
                    reconocedor is None
                ):

                    time.sleep(
                        0.05
                    )

                    continue


                try:

                    datos = (
                        cola_audio_mic.get(
                            timeout=0.2
                        )
                    )

                except queue.Empty:

                    continue


                if (
                    reconocedor.AcceptWaveform(
                        datos
                    )
                ):

                    resultado = json.loads(
                        reconocedor.Result()
                    )


                    texto = resultado.get(
                        "text",
                        ""
                    ).strip()


                    if not texto:

                        continue


                    print(
                        "VOZ RECONOCIDA:",
                        texto
                    )


                    if modo == MIC_WAKE:

                        texto_normal = (
                            normalizar_texto(
                                texto
                            )
                        )


                        if (
                            "raspi"
                            in
                            texto_normal
                            or
                            "ras pi"
                            in
                            texto_normal
                            or
                            "ras py"
                            in
                            texto_normal
                        ):

                            cola_eventos_voz.put(
                                (
                                    "wake",
                                    texto
                                )
                            )


                    elif modo == MIC_DESTINO:

                        cola_eventos_voz.put(
                            (
                                "destino",
                                texto
                            )
                        )


                    elif modo == MIC_COMANDO:

                        cola_eventos_voz.put(
                            (
                                "comando",
                                texto
                            )
                        )


    except Exception as error:

        print(
            "ERROR MICROFONO:",
            error
        )


# ============================================================
# FUNCION PARA PROCESAR LA VOZ
# ============================================================

def atender_eventos_voz():

    global destino_actual


    while True:

        try:

            tipo, texto = (
                cola_eventos_voz.get_nowait()
            )

        except queue.Empty:

            break


        # ====================================================
        # ERROR
        # ====================================================

        if tipo == "error":

            print(
                "ERROR VOZ:",
                texto
            )

            continue


        # ====================================================
        # WAKE: RASPI
        # ====================================================

        if tipo == "wake":

            orden = quitar_raspi(
                texto
            )


            # -----------------------------------------------
            # RASPI + ORDEN
            # -----------------------------------------------

            if orden:

                ejecutar_orden(
                    orden
                )


            # -----------------------------------------------
            # SOLO RASPI
            # -----------------------------------------------

            else:

                hablar(
                    "Sí, dime.",
                    modo_retorno=MIC_COMANDO
                )


                inicio = (
                    time.monotonic()
                )


                while (
                    time.monotonic()
                    -
                    inicio
                    <
                    8
                ):


                    try:

                        tipo2, texto2 = (
                            cola_eventos_voz.get(
                                timeout=0.2
                            )
                        )

                    except queue.Empty:

                        continue


                    if tipo2 == "comando":

                        ejecutar_orden(
                            texto2
                        )

                        break


                establecer_modo_microfono(
                    MIC_WAKE
                )


        # ====================================================
        # DESTINO
        # ====================================================

        elif tipo == "destino":

            texto_normal = normalizar_texto(
                texto
            )


            # Permitir tambien:
            # "RASPI, ve al baño"

            if (
                "raspi" in texto_normal
                or
                "ras pi" in texto_normal
                or
                "ras py" in texto_normal
            ):

                texto_normal = quitar_raspi(
                    texto_normal
                )


                ejecutar_orden(
                    texto_normal
                )

                continue


            destino = extraer_destino(
                texto
            )


            if destino is not None:

                destino_actual = destino


                print(
                    "DESTINO:",
                    destino_actual
                )


                if (
                    ubicacion_actual,
                    destino_actual
                ) in RUTAS:

                    hablar(
                        f"Entendido. "
                        f"Vamos a {destino_actual}.",
                        modo_retorno=MIC_WAKE
                    )

                    # Aqui entra la navegacion
                    # cuando configuremos las rutas.

                else:

                    hablar(
                        f"Entendido. "
                        f"Quieres ir a {destino_actual}. "
                        "La ruta todavía no "
                        "está configurada.",
                        modo_retorno=MIC_WAKE
                    )


        # ====================================================
        # COMANDO
        # ====================================================

        elif tipo == "comando":

            ejecutar_orden(
                texto
            )


# ============================================================
# EJECUTAR ORDEN
# ============================================================

def ejecutar_orden(
    texto
):

    global destino_actual


    tipo, valor = (
        reconocer_comando(
            texto
        )
    )


    # --------------------------------------------------------
    # DESTINO
    # --------------------------------------------------------

    if tipo == "destino":

        destino_actual = valor


        print(
            "ORDEN:",
            destino_actual
        )


        if (
            ubicacion_actual,
            destino_actual
        ) in RUTAS:

            hablar(
                f"Destino recibido. "
                f"Vamos a {destino_actual}.",
                modo_retorno=MIC_WAKE
            )

        else:

            hablar(
                f"Destino recibido: "
                f"{destino_actual}. "
                "La ruta todavía no "
                "está configurada.",
                modo_retorno=MIC_WAKE
            )


        return


    # --------------------------------------------------------
    # PARAR
    # --------------------------------------------------------

    if tipo == "parar":

        hablar(
            "Alto. Navegación en pausa.",
            modo_retorno=MIC_WAKE
        )

        return


    # --------------------------------------------------------
    # REANUDAR
    # --------------------------------------------------------

    if tipo == "reanudar":

        hablar(
            "Navegación reanudada.",
            modo_retorno=MIC_WAKE
        )

        return


    # --------------------------------------------------------
    # DERECHA
    # --------------------------------------------------------

    if tipo == "girar_derecha":

        hablar(
            "Gira a la derecha suavemente.",
            modo_retorno=MIC_WAKE
        )

        return


    # --------------------------------------------------------
    # IZQUIERDA
    # --------------------------------------------------------

    if tipo == "girar_izquierda":

        hablar(
            "Gira a la izquierda suavemente.",
            modo_retorno=MIC_WAKE
        )

        return


    # --------------------------------------------------------
    # DESCONOCIDO
    # --------------------------------------------------------

    hablar(
        "No entendí la orden.",
        modo_retorno=MIC_WAKE
    )


# ============================================================
# GIRO
# ============================================================

def iniciar_giro():

    global angulo_acumulado
    global tiempo_giro_anterior

    angulo_acumulado = 0.0

    tiempo_giro_anterior = (
        time.monotonic()
    )


def actualizar_giro(
    derecha,
    objetivo
):

    global angulo_acumulado
    global tiempo_giro_anterior


    ahora = time.monotonic()


    dt = (
        ahora
        -
        tiempo_giro_anterior
    )


    tiempo_giro_anterior = ahora


    (
        ax,
        ay,
        az,
        gx,
        gy,
        gz
    ) = leer_mpu6050()


    gz_corregido = (
        gz -
        GYRO_Z_OFFSET
    )


    # -----------------------------------------------
    # FILTRAR EL SENTIDO
    # -----------------------------------------------

    valor_direccion = (
        gz_corregido
        *
        SIGNO_DERECHA
    )


    movimiento_correcto = False


    if derecha:

        if (
            valor_direccion
            >=
            MIN_GIRO_DPS
        ):

            movimiento_correcto = True

    else:

        if (
            valor_direccion
            <=
            -MIN_GIRO_DPS
        ):

            movimiento_correcto = True


    if movimiento_correcto:

        angulo_acumulado += (
            abs(gz_corregido)
            *
            dt
        )


    return (
        angulo_acumulado
        >=
        objetivo
    )


# ============================================================
# PASOS
# ============================================================

pasos = 0

ultimo_paso = 0


def iniciar_pasos():

    global pasos
    global ultimo_paso

    pasos = 0

    ultimo_paso = 0


def actualizar_pasos():

    global pasos
    global ultimo_paso


    (
        ax,
        ay,
        az,
        gx,
        gy,
        gz
    ) = leer_mpu6050()


    magnitud = math.sqrt(
        ax * ax
        +
        ay * ay
        +
        az * az
    )


    ahora = time.monotonic()


    if (
        magnitud >= 1.12
        and
        ahora
        -
        ultimo_paso
        >=
        0.35
    ):

        pasos += 1

        ultimo_paso = ahora

        print(
            "PASO:",
            pasos,
            "/ 5"
        )


    return pasos >= 5


# ============================================================
# PROCESAR OBJETOS VISUALES
# ============================================================

ultimo_objeto = {}

COOLDOWN_OBJETO = 4


def procesar_objetos(
    results
):

    ahora = time.monotonic()


    for box in results[0].boxes:

        clase_id = int(
            box.cls[0]
        )


        nombre = model.names[
            clase_id
        ]


        if (
            nombre
            not in
            OBJETOS_PERMITIDOS
        ):

            continue


        objeto = (
            OBJETOS_PERMITIDOS[
                nombre
            ]
        )


        ultima_vez = (
            ultimo_objeto.get(
                objeto,
                0
            )
        )


        if (
            ahora
            -
            ultima_vez
            <
            COOLDOWN_OBJETO
        ):

            continue


        hablar(
            f"Alto, hay un "
            f"{objeto}.",
            modo_retorno=MIC_WAKE
        )


        ultimo_objeto[
            objeto
        ] = ahora


# ============================================================
# DETECTAR SEÑAL
# ============================================================

senal_anterior = None

contador_senal = 0


def detectar_senal(
    results
):

    global senal_anterior
    global contador_senal


    senal = None


    for box in results[0].boxes:

        clase_id = int(
            box.cls[0]
        )


        nombre = (
            model.names[
                clase_id
            ]
        )


        confianza = float(
            box.conf[0]
        )


        if (
            nombre
            in
            SENALES_ORIENTADORAS
            and
            confianza
            >=
            CONF_YOLO
        ):

            senal = nombre

            break


    if senal is None:

        senal_anterior = None

        contador_senal = 0

        return None


    if (
        senal
        ==
        senal_anterior
    ):

        contador_senal += 1

    else:

        senal_anterior = senal

        contador_senal = 1


    # Tres detecciones consecutivas
    # para evitar falsos positivos

    if contador_senal >= 3:

        return (
            SENALES_ORIENTADORAS[
                senal
            ]
        )


    return None


# ============================================================
# MOSTRAR INFORMACION EN CAMARA
# ============================================================

def mostrar_frame(
    frame,
    distancia,
    texto_estado
):

    cv2.putText(
        frame,
        f"Distancia: "
        f"{distancia:.1f} cm",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 255, 255),
        2
    )


    cv2.putText(
        frame,
        texto_estado,
        (10, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.60,
        (255, 255, 255),
        2
    )


    if ubicacion_actual is not None:

        cv2.putText(
            frame,
            f"Ubicacion: "
            f"{ubicacion_actual}",
            (10, 90),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.60,
            (255, 255, 255),
            2
        )


    if destino_actual is not None:

        cv2.putText(
            frame,
            f"Destino: "
            f"{destino_actual}",
            (10, 120),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.60,
            (255, 255, 255),
            2
        )


    cv2.imshow(
        "Sistema de Orientacion",
        frame
    )


# ============================================================
# CAPTURAR Y PROCESAR CAMARA
# ============================================================

def capturar_yolo():

    frame_yuyv = (
        camera.capture_array()
    )


    frame = cv2.cvtColor(
        frame_yuyv,
        cv2.COLOR_YUV2BGR_YUYV
    )


    results = model(
        frame,
        conf=CONF_YOLO,
        verbose=False
    )


    # Dibujar solo clases utiles

    for box in results[0].boxes:

        clase_id = int(
            box.cls[0]
        )


        nombre = (
            model.names[
                clase_id
            ]
        )


        if (
            nombre
            not in
            CLASES_PERMITIDAS
        ):

            continue


        confianza = float(
            box.conf[0]
        )


        x1, y1, x2, y2 = map(
            int,
            box.xyxy[0]
        )


        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            2
        )


        cv2.putText(
            frame,
            f"{nombre} "
            f"{confianza:.2f}",
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2
        )


    return (
        frame,
        results
    )


# ============================================================
# ARRANQUE
# ============================================================

print()
print("======================================")
print("INICIANDO HILOS")
print("======================================")


hilo_ultrasonico = threading.Thread(
    target=hilo_alerta_ultrasonica,
    daemon=True
)

hilo_ultrasonico.start()


hilo_mic = threading.Thread(
    target=hilo_microfono,
    daemon=True
)

hilo_mic.start()


time.sleep(2)


establecer_modo_microfono(
    MIC_WAKE
)


# ============================================================
# MOSTRAR CAMARA INMEDIATAMENTE
# ============================================================

print()
print(
    "MOSTRANDO CAMARA..."
)


frame_inicial, resultados_iniciales = (
    capturar_yolo()
)


distancia_inicial = (
    sensor.distance * 100
)


mostrar_frame(
    frame_inicial,
    distancia_inicial,
    "INICIANDO SISTEMA"
)


cv2.waitKey(1)


# ============================================================
# PRIMERA INSTRUCCION
# ============================================================

hablar(
    "Gira 90 grados a la derecha "
    "suavemente para buscar la señal "
    "orientadora.",
    modo_retorno=MIC_WAKE
)


iniciar_giro()


estado = GIRO_DERECHA


# ============================================================
# BUCLE PRINCIPAL
# ============================================================

try:

    while True:


        # ====================================================
        # CAMARA SIEMPRE ACTIVA
        # ====================================================

        frame, results = (
            capturar_yolo()
        )


        distancia_cm = (
            sensor.distance * 100
        )


        # ====================================================
        # RASPI
        # ====================================================

        atender_eventos_voz()


        # ====================================================
        # GIRO 90 DERECHA
        # ====================================================

        if estado == GIRO_DERECHA:

            terminado = actualizar_giro(
                True,
                90
            )


            texto_estado = (
                f"Giro derecha: "
                f"{angulo_acumulado:.1f}/90 grados"
            )


            if terminado:

                estado = BUSCAR_SENAL_1

                reiniciar_busqueda = True

                tiempo_inicio_busqueda = (
                    time.monotonic()
                )

                senal_anterior = None

                contador_senal = 0


                hablar(
                    "Buscando la señal "
                    "durante un minuto.",
                    modo_retorno=MIC_WAKE
                )


        # ====================================================
        # BUSCAR SEÑAL PRIMERA VEZ
        # ====================================================

        elif estado == BUSCAR_SENAL_1:

            texto_estado = (
                "BUSCANDO SEÑAL - "
                "1 MINUTO"
            )


            lugar = detectar_senal(
                results
            )


            # Objetos utiles

            procesar_objetos(
                results
            )


            if lugar is not None:

                ubicacion_actual = lugar


                print(
                    "SEÑAL ENCONTRADA:",
                    lugar
                )


                hablar(
                    f"Estás en "
                    f"{ubicacion_actual}."
                )


                estado = ESPERANDO_DESTINO


                hablar(
                    "¿A dónde quieres ir?",
                    modo_retorno=MIC_DESTINO
                )


            elif (
                time.monotonic()
                -
                tiempo_inicio_busqueda
                >=
                TIEMPO_BUSQUEDA
            ):

                estado = (
                    MEDIA_VUELTA_IZQUIERDA
                )


                iniciar_giro()


                hablar(
                    "No encontré la señal. "
                    "Da media vuelta a la izquierda "
                    "suavemente.",
                    modo_retorno=MIC_WAKE
                )


        # ====================================================
        # MEDIA VUELTA IZQUIERDA
        # ====================================================

        elif estado == MEDIA_VUELTA_IZQUIERDA:

            terminado = actualizar_giro(
                False,
                180
            )


            texto_estado = (
                f"Media vuelta: "
                f"{angulo_acumulado:.1f}/180 grados"
            )


            if terminado:

                estado = BUSCAR_SENAL_2

                tiempo_inicio_busqueda = (
                    time.monotonic()
                )

                senal_anterior = None

                contador_senal = 0


                hablar(
                    "Buscando la señal "
                    "durante un minuto.",
                    modo_retorno=MIC_WAKE
                )


        # ====================================================
        # BUSCAR SEÑAL SEGUNDA VEZ
        # ====================================================

        elif estado == BUSCAR_SENAL_2:

            texto_estado = (
                "BUSCANDO SEÑAL - "
                "1 MINUTO"
            )


            lugar = detectar_senal(
                results
            )


            procesar_objetos(
                results
            )


            if lugar is not None:

                ubicacion_actual = lugar


                print(
                    "SEÑAL ENCONTRADA:",
                    lugar
                )


                hablar(
                    f"Estás en "
                    f"{ubicacion_actual}."
                )


                estado = ESPERANDO_DESTINO


                hablar(
                    "¿A dónde quieres ir?",
                    modo_retorno=MIC_DESTINO
                )


            elif (
                time.monotonic()
                -
                tiempo_inicio_busqueda
                >=
                TIEMPO_BUSQUEDA
            ):

                estado = CINCO_PASOS

                iniciar_pasos()


                hablar(
                    "No encontré la señal. "
                    "Camina cinco pasos.",
                    modo_retorno=MIC_WAKE
                )


        # ====================================================
        # CAMINAR CINCO PASOS
        # ====================================================

        elif estado == CINCO_PASOS:

            texto_estado = (
                f"CAMINANDO - "
                f"{pasos}/5 PASOS"
            )


            terminado = (
                actualizar_pasos()
            )


            if terminado:

                hablar(
                    "Cinco pasos detectados. "
                    "Gira 90 grados a la derecha "
                    "suavemente para buscar "
                    "la señal.",
                    modo_retorno=MIC_WAKE
                )


                iniciar_giro()


                estado = GIRO_DERECHA


        # ====================================================
        # ESPERANDO DESTINO
        # ====================================================

        elif estado == ESPERANDO_DESTINO:

            texto_estado = (
                "ESPERANDO DESTINO"
            )


        # ====================================================
        # NAVEGANDO
        # ====================================================

        elif estado == NAVEGANDO:

            texto_estado = (
                f"NAVEGANDO A "
                f"{destino_actual}"
            )


        else:

            texto_estado = (
                "SISTEMA"
            )


        # ====================================================
        # MOSTRAR CAMARA
        # ====================================================

        mostrar_frame(
            frame,
            distancia_cm,
            texto_estado
        )


        # ====================================================
        # SALIR
        # ====================================================

        tecla = (
            cv2.waitKey(1)
            &
            0xFF
        )


        if tecla == 27:

            break


# ============================================================
# CIERRE
# ============================================================

except KeyboardInterrupt:

    print()
    print(
        "Sistema detenido por el usuario."
    )


finally:

    print()
    print(
        "Cerrando sistema..."
    )


    detener_mic.set()

    detener_alerta.set()


    establecer_modo_microfono(
        MIC_OFF
    )


    try:

        camera.stop()

    except Exception:

        pass


    try:

        sensor.close()

    except Exception:

        pass


    try:

        factory.close()

    except Exception:

        pass


    try:

        bus.close()

    except Exception:

        pass


    cv2.destroyAllWindows()


    try:

        for archivo in os.listdir(
            CARPETA_AUDIO
        ):

            ruta = os.path.join(
                CARPETA_AUDIO,
                archivo
            )


            os.remove(
                ruta
            )


        os.rmdir(
            CARPETA_AUDIO
        )


    except Exception:

        pass


    print(
        "Sistema cerrado correctamente."
    )