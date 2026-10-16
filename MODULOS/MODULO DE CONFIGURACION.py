import os
from enum import Enum, auto

ANCHO, ALTO = 640, 480
CONF_YOLO = 0.75
TIEMPO_BUSQUEDA = 60

RUTA_MODELO = "/home/esmeralda/Proyectoesmi/mi_deteccion/yolo11n_ncnn_model"
RUTA_VOZ = "/home/esmeralda/Proyectoesmi/piper_voices/es_MX-claude-high.onnx"
RUTA_VOSK = "/home/esmeralda/Proyectoesmi/vosk/vosk-model-small-es-0.42"
CARPETA_AUDIO = "/tmp/audio_orientacion"
RUTA_BEEP = os.path.join(CARPETA_AUDIO, "beep.wav")

os.makedirs(CARPETA_AUDIO, exist_ok=True) #CREA UNA CARPETA TEMPORAL

MPU6050_ADDR = 0x68
PWR_MGMT_1 = 0x6B #para quitar el modo suspension
ACCEL_XOU_TH = 0x3B
GYRO_Z_OFFSET = 0.445992
MIN_GIRO_DPS = 8.0 #la velocidad minima del giro es de 8 grados para considerar que realmente esta girando 
SIGNO_DERECHA = 1 #si es positivo es derecha

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

CLASES_PERMITIDAS = set(SENALES_ORIENTADORAS.keys()) | set(OBJETOS_PERMITIDOS.keys()) #genera una lista con todos los objetos que la camara tiene permiso de reconocer une las señales y los obstaculos

#ruta de navegacion como ir de un punto a otro

RUTAS = {
    ("Inicio", "Sala"): {
        "pasos": 10,
        "giros": ["derecha"]
    },
    ("Sala", "Baño"): {
        "pasos": 6,
        "giros": ["izquierda"]
    }
}


MICROFONO_ID = None #busqueda de microfono disponible
#modos del microfono
MIC_WAKE = "wake" #escuchando palabras claves 
MIC_DESTINO = "destino" # escuchando lugar a donde ir 
MIC_COMANDO = "comando" # escuchando ordenes directas 
MIC_OFF = "off" #no escucharse a si mismo 

# paso a paso que el sistema va a seguir 
class Estado(Enum):
    GIRO_DERECHA = auto()
    BUSCAR_SENAL_1 = auto()
    MEDIA_VUELTA_IZQ = auto()
    BUSCAR_SENAL_2 = auto()
    CINCO_PASOS = auto()
    ESPERANDO_DESTINO = auto()
    NAVEGANDO = auto()