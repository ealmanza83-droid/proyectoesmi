import os
import time
import math
import wave
import json
import queue
import struct
import threading
import subprocess
import unicodedata
from enum import Enum, auto

import cv2
import sounddevice as sd
from ultralytics import YOLO
from picamera2 import Picamera2
from gpiozero import DistanceSensor
from gpiozero.pins.lgpio import LGPIOFactory
from smbus2 import SMBus
from piper import PiperVoice
from vosk import Model, KaldiRecognizer, SetLogLevel

# Configuracion General

ANCHO, ALTO = 640, 480
CONF_YOLO = 0.75
TIEMPO_BUSQUEDA = 60

RUTA_MODELO = "/home/esmeralda/Proyectoesmi/mi_deteccion/yolo11n_ncnn_model"
RUTA_VOZ = "/home/esmeralda/Proyectoesmi/piper_voices/es_MX-claude-high.onnx"
RUTA_VOSK = "/home/esmeralda/Proyectoesmi/vosk/vosk-model-small-es-0.42"
CARPETA_AUDIO = "/tmp/audio_orientacion"

os.makedirs(CARPETA_AUDIO, exist_ok=True)

MPU6050_ADDR = 0x68
PWR_MGMT_1 = 0x6B
ACCEL_XOUT_H = 0x3B
GYRO_Z_OFFSET = 0.445992
MIN_GIRO_DPS = 8.0
SIGNO_DERECHA = 1

SENALES_ORIENTADORAS = {
    "cow": "Inicio",
    "zebra": "Sala",
    "giraffe": "Baño",
    "sheep": "Cuarto"
}

OBJETOS_PERMITIDOS = {
    "chair": "silla",
    "table": "mesa",
    "potted plant": "maceta",
}

CLASES_PERMITIDAS = set(SENALES_ORIENTADORAS.keys()) | set(OBJETOS_PERMITIDOS.keys())

RUTAS = {
    ("Inicio", "Sala"): {
        "pasos": 10,
        "giros": ["derecha"]
    }
}

MICROFONO_ID = None
MIC_WAKE, MIC_DESTINO, MIC_COMANDO, MIC_OFF = "wake", "destino", "comando", "off"

class Estado(Enum):
    GIRO_DERECHA = auto()
    BUSCAR_SENAL_1 = auto()
    MEDIA_VUELTA_IZQ = auto()
    BUSCAR_SENAL_2 = auto()
    CINCO_PASOS = auto()
    ESPERANDO_DESTINO = auto()
    NAVEGANDO = auto()

estado = Estado.GIRO_DERECHA
modo_microfono = MIC_OFF
ubicacion_actual = None
destino_actual = None

lock_mic = threading.Lock()
cola_audio_mic = queue.Queue()
cola_eventos_voz = queue.Queue()
detener_mic = threading.Event()
voz_hablando = threading.Event()
AUDIOS = {}

# Inicializacion de Componentes

print("Cargando Piper Voice...")
voz = PiperVoice.load(RUTA_VOZ)

print("Inicializando HC-SR04...")
factory = LGPIOFactory(chip=15)
sensor = DistanceSensor(echo=23, trigger=18, pin_factory=factory, max_distance=4)

print("Inicializando MPU6050...")
bus = SMBus(1)
bus.write_byte_data(MPU6050_ADDR, PWR_MGMT_1, 0)

print("Inicializando Cámara...")
camera = Picamera2()
camera.configure(
    camera.create_video_configuration(main={"format": "YUYV", "size": (ANCHO, ALTO)})
)
camera.start()
time.sleep(2)

print("Cargando YOLO...")
model = YOLO(RUTA_MODELO, task="detect")

SetLogLevel(-1)
modelo_vosk = Model(RUTA_VOSK) if os.path.exists(RUTA_VOSK) else None
if not modelo_vosk:
    print(f"ADVERTENCIA: Modelo Vosk no encontrado en {RUTA_VOSK}")

# Procesamiento de Texto y TTS Piper

def normalizar_texto(texto):
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")

def normalizar_nombre_archivo(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    salida = [c if c.isalnum() else "_" for c in texto.lower()]
    return "".join(salida)[:80]

def generar_audio(texto):
    if texto in AUDIOS:
        return AUDIOS[texto]

    nombre = f"{normalizar_nombre_archivo(texto)}.wav"
    ruta = os.path.join(CARPETA_AUDIO, nombre)

    with wave.open(ruta, "wb") as archivo:
        voz.synthesize_wav(texto, archivo)

    AUDIOS[texto] = ruta
    return ruta

def hablar(texto, modo_retorno=None):
    print("VOZ:", texto)
    establecer_modo_microfono(MIC_OFF)
    voz_hablando.set()

    try:
        ruta = generar_audio(texto)
        subprocess.run(["aplay", "-q", ruta], check=True)
    except Exception as error:
        print("ERROR DE AUDIO:", error)
    finally:
        voz_hablando.clear()

    if modo_retorno:
        establecer_modo_microfono(modo_retorno)

# Sensor Ultrasonico y Alerta Sonora

RUTA_BEEP = os.path.join(CARPETA_AUDIO, "beep.wav")
proceso_beep = None

def crear_beep():
    frecuencia, duracion, sr = 1000, 0.08, 44100
    muestras = int(sr * duracion)

    with wave.open(RUTA_BEEP, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(sr)
        datos = bytearray()
        for i in range(muestras):
            valor = int(15000 * math.sin(2 * math.pi * frecuencia * i / sr))
            datos.extend(struct.pack("<h", valor))
        archivo.writeframes(datos)

crear_beep()

def reproducir_beep():
    global proceso_beep
    if voz_hablando.is_set():
        return
    if proceso_beep and proceso_beep.poll() is None:
        return

    try:
        proceso_beep = subprocess.Popen(
            ["aplay", "-q", RUTA_BEEP],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as error:
        print("ERROR BEEP:", error)

def intervalo_beep(distancia_cm):
    if distancia_cm > 200: return None
    if distancia_cm > 150: return 1.20
    if distancia_cm > 100: return 0.70
    if distancia_cm > 50:  return 0.35
    if distancia_cm > 25:  return 0.18
    return 0.12

detener_alerta = threading.Event()

def hilo_alerta_ultrasonica():
    ultima_alerta = 0
    while not detener_alerta.is_set():
        try:
            distancia = sensor.distance * 100
            intervalo = intervalo_beep(distancia)
            ahora = time.monotonic()

            if intervalo and (ahora - ultima_alerta >= intervalo):
                reproducir_beep()
                ultima_alerta = ahora
        except Exception as error:
            print("ERROR HC-SR04:", error)

        time.sleep(0.03)

# Sensor MPU6050

def leer_mpu6050():
    datos = bus.read_i2c_block_data(MPU6050_ADDR, ACCEL_XOUT_H, 14)
    ax_r, ay_r, az_r, _, gx_r, gy_r, gz_r = struct.unpack(">h h h h h h h", bytes(datos))

    return (
        ax_r / 16384.0, ay_r / 16384.0, az_r / 16384.0,
        gx_r / 131.0,   gy_r / 131.0,   gz_r / 131.0
    )

# Control de Microfono

def establecer_modo_microfono(modo):
    global modo_microfono
    with lock_mic:
        modo_microfono = modo

    while not cola_audio_mic.empty():
        try:
            cola_audio_mic.get_nowait()
        except queue.Empty:
            break

def obtener_modo_microfono():
    with lock_mic:
        return modo_microfono

def callback_microfono(indata, frames, tiempo, status):
    if status:
        print("MIC:", status)
    cola_audio_mic.put(bytes(indata))

GRAMATICA_WAKE = json.dumps([
    "raspi", "ras pi", "ras py", "raspi baño", "raspi bano", "raspi sala",
    "raspi cuarto", "raspi cocina", "raspi ve al baño", "raspi ve a la sala",
    "raspi ve al cuarto", "raspi ve a la cocina", "raspi para", "raspi alto",
    "raspi detente", "raspi reanuda", "raspi continuar", "raspi gira a la derecha",
    "raspi gira a la izquierda", "[unk]"
], ensure_ascii=False)

GRAMATICA_DESTINO = json.dumps([
    "baño", "bano", "sala", "cuarto", "cocina", "inicio", "quiero ir al baño",
    "quiero ir a la sala", "quiero ir al cuarto", "quiero ir a la cocina",
    "raspi baño", "raspi bano", "raspi sala", "raspi cuarto", "raspi cocina", "[unk]"
], ensure_ascii=False)

GRAMATICA_COMANDO = json.dumps([
    "baño", "bano", "sala", "cuarto", "cocina", "inicio", "quiero ir al baño",
    "quiero ir a la sala", "quiero ir al cuarto", "quiero ir a la cocina",
    "ve al baño", "ve a la sala", "ve al cuarto", "ve a la cocina", "para", "alto",
    "detente", "reanuda", "reanudar", "continua", "continuar", "gira a la derecha",
    "gira a la izquierda", "[unk]"
], ensure_ascii=False)

def extraer_destino(texto):
    texto = normalizar_texto(texto)
    destinos = ["baño", "sala", "cuarto", "cocina", "inicio"]
    for d in destinos:
        if d in texto or (d == "baño" and "bano" in texto):
            return "Baño" if d in ["baño", "bano"] else d.capitalize()
    return None

def reconocer_comando(texto):
    texto = normalizar_texto(texto)
    destino = extraer_destino(texto)
    if destino:
        return ("destino", destino)

    if any(k in texto for k in ["para", "alto", "detente"]):
        return ("parar", None)
    if any(k in texto for k in ["reanuda", "reanudar", "continua", "continuar"]):
        return ("reanudar", None)
    if "gira a la derecha" in texto:
        return ("girar_derecha", None)
    if "gira a la izquierda" in texto:
        return ("girar_izquierda", None)

    return ("desconocido", None)

def quitar_raspi(texto):
    texto = normalizar_texto(texto)
    for kw in ["raspi", "ras pi", "ras py"]:
        texto = texto.replace(kw, "")
    return texto.strip()

def seleccionar_microfono():
    if MICROFONO_ID is not None:
        return MICROFONO_ID

    dispositivos = sd.query_devices()
    candidatos = []

    for i, dev in enumerate(dispositivos):
        if dev["max_input_channels"] <= 0:
            continue
        nombre = dev["name"].lower()
        score = 0
        if "usb" in nombre: score += 3
        if "microphone" in nombre: score += 2
        if "mic" in nombre or "headset" in nombre: score += 1

        if score > 0:
            candidatos.append((score, i))

    if candidatos:
        candidatos.sort(reverse=True)
        return candidatos[0][1]

    return sd.default.device[0]

def hilo_microfono():
    global modelo_vosk
    if not modelo_vosk:
        return

    dispositivo = seleccionar_microfono()
    info = sd.query_devices(dispositivo, "input")
    frecuencia = int(info.get("default_samplerate", 16000))
    if frecuencia <= 0: frecuencia = 16000

    print(f"MICROFONO: {info['name']} | FRECUENCIA: {frecuencia}")

    reconocedor = None
    modo_anterior = None

    with sd.RawInputStream(
        samplerate=frecuencia, blocksize=4000, device=dispositivo,
        dtype="int16", channels=1, callback=callback_microfono
    ):
        print("MICROFONO OK")
        while not detener_mic.is_set():
            modo = obtener_modo_microfono()

            if modo != modo_anterior:
                modo_anterior = modo
                if modo == MIC_WAKE:
                    reconocedor = KaldiRecognizer(modelo_vosk, frecuencia, GRAMATICA_WAKE)
                elif modo == MIC_DESTINO:
                    reconocedor = KaldiRecognizer(modelo_vosk, frecuencia, GRAMATICA_DESTINO)
                elif modo == MIC_COMANDO:
                    reconocedor = KaldiRecognizer(modelo_vosk, frecuencia, GRAMATICA_COMANDO)
                else:
                    reconocedor = None

                while not cola_audio_mic.empty():
                    try:
                        cola_audio_mic.get_nowait()
                    except queue.Empty:
                        break

            if modo == MIC_OFF or reconocedor is None:
                time.sleep(0.05)
                continue

            try:
                datos = cola_audio_mic.get(timeout=0.2)
            except queue.Empty:
                continue

            if reconocedor.AcceptWaveform(datos):
                resultado = json.loads(reconocedor.Result())
                texto = resultado.get("text", "").strip()
                if not texto:
                    continue

                print("VOZ RECONOCIDA:", texto)

                if modo == MIC_WAKE:
                    texto_normal = normalizar_texto(texto)
                    if any(k in texto_normal for k in ["raspi", "ras pi", "ras py"]):
                        cola_eventos_voz.put(("wake", texto))
                elif modo in (MIC_DESTINO, MIC_COMANDO):
                    cola_eventos_voz.put((modo, texto))

# Procesamiento de Eventos y Comandos de Voz

def atender_eventos_voz():
    global destino_actual

    while not cola_eventos_voz.empty():
        try:
            tipo, texto = cola_eventos_voz.get_nowait()
        except queue.Empty:
            break

        if tipo == "wake":
            orden = quitar_raspi(texto)
            if orden:
                ejecutar_orden(orden)
            else:
                hablar("Sí, dime.", modo_retorno=MIC_COMANDO)
                inicio = time.monotonic()
                while time.monotonic() - inicio < 8:
                    try:
                        tipo2, texto2 = cola_eventos_voz.get(timeout=0.2)
                        if tipo2 == "comando":
                            ejecutar_orden(texto2)
                            break
                    except queue.Empty:
                        continue
                establecer_modo_microfono(MIC_WAKE)

        elif tipo == "destino":
            texto_normal = normalizar_texto(texto)
            if any(k in texto_normal for k in ["raspi", "ras pi", "ras py"]):
                ejecutar_orden(quitar_raspi(texto_normal))
                continue

            destino = extraer_destino(texto)
            if destino:
                destino_actual = destino
                print("DESTINO:", destino_actual)
                msg = f"Entendido. Vamos a {destino_actual}." if (ubicacion_actual, destino_actual) in RUTAS else f"Entendido. Quieres ir a {destino_actual}. La ruta todavía no está configurada."
                hablar(msg, modo_retorno=MIC_WAKE)

        elif tipo == "comando":
            ejecutar_orden(texto)

def ejecutar_orden(texto):
    global destino_actual
    tipo, valor = reconocer_comando(texto)

    if tipo == "destino":
        destino_actual = valor
        print("ORDEN:", destino_actual)
        msg = f"Destino recibido. Vamos a {destino_actual}." if (ubicacion_actual, destino_actual) in RUTAS else f"Destino recibido: {destino_actual}. La ruta todavía no está configurada."
        hablar(msg, modo_retorno=MIC_WAKE)
    elif tipo == "parar":
        hablar("Alto. Navegación en pausa.", modo_retorno=MIC_WAKE)
    elif tipo == "reanudar":
        hablar("Navegación reanudada.", modo_retorno=MIC_WAKE)
    elif tipo == "girar_derecha":
        hablar("Gira a la derecha suavemente.", modo_retorno=MIC_WAKE)
    elif tipo == "girar_izquierda":
        hablar("Gira a la izquierda suavemente.", modo_retorno=MIC_WAKE)
    else:
        hablar("No entendí la orden.", modo_retorno=MIC_WAKE)

# Control de Navegacion Giro y Pasos

angulo_acumulado = 0.0
tiempo_giro_anterior = 0.0

def iniciar_giro():
    global angulo_acumulado, tiempo_giro_anterior
    angulo_acumulado = 0.0
    tiempo_giro_anterior = time.monotonic()

def actualizar_giro(derecha, objetivo):
    global angulo_acumulado, tiempo_giro_anterior

    ahora = time.monotonic()
    dt = ahora - tiempo_giro_anterior
    tiempo_giro_anterior = ahora

    _, _, _, _, _, gz = leer_mpu6050()
    gz_corregido = gz - GYRO_Z_OFFSET
    valor_direccion = gz_corregido * SIGNO_DERECHA

    movimiento_correcto = (valor_direccion >= MIN_GIRO_DPS) if derecha else (valor_direccion <= -MIN_GIRO_DPS)

    if movimiento_correcto:
        angulo_acumulado += abs(gz_corregido) * dt

    return angulo_acumulado >= objetivo

pasos = 0
ultimo_paso = 0

def iniciar_pasos():
    global pasos, ultimo_paso
    pasos = 0
    ultimo_paso = 0

def actualizar_pasos():
    global pasos, ultimo_paso

    ax, ay, az, _, _, _ = leer_mpu6050()
    magnitud = math.sqrt(ax**2 + ay**2 + az**2)
    ahora = time.monotonic()

    if magnitud >= 1.12 and (ahora - ultimo_paso >= 0.35):
        pasos += 1
        ultimo_paso = ahora
        print(f"PASO: {pasos} / 5")

    return pasos >= 5

# Deteccion de Objetos con YOLO

ultimo_objeto = {}
COOLDOWN_OBJETO = 4

def procesar_objetos(results):
    ahora = time.monotonic()

    for box in results[0].boxes:
        clase_id = int(box.cls[0])
        nombre = model.names[clase_id]

        if nombre not in OBJETOS_PERMITIDOS:
            continue

        objeto = OBJETOS_PERMITIDOS[nombre]
        ultima_vez = ultimo_objeto.get(objeto, 0)

        if ahora - ultima_vez < COOLDOWN_OBJETO:
            continue

        ultimo_objeto[objeto] = ahora
        hablar(f"Alto, hay un {objeto}.")