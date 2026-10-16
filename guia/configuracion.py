"""Configuracion central del sistema de orientacion.

Todos los valores ajustables viven aqui: rutas de los modelos,
umbrales de los sensores y tiempos de espera.  De esta forma no hay
numeros "magicos" repartidos por el codigo y cambiar un umbral es una
sola linea.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Raiz del proyecto: funciona igual en la Raspberry Pi que en el PC.
RAIZ_DEL_PROYECTO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Configuracion:
    """Ajustes generales.  Son inmutables durante la ejecucion."""

    # ------------------------------------------------------------------
    # Vision (camara + YOLO)
    # ------------------------------------------------------------------
    ancho: int = 640
    alto: int = 480
    confianza_minima: float = 0.75
    detecciones_senal_requeridas: int = 3
    enfriamiento_objeto_s: float = 4.0
    tiempo_busqueda_s: float = 60.0
    ruta_modelo: Path = RAIZ_DEL_PROYECTO / "mi_deteccion" / "yolo11n_ncnn_model"

    # ------------------------------------------------------------------
    # Audio (Piper + Vosk)
    # ------------------------------------------------------------------
    ruta_voz: Path = RAIZ_DEL_PROYECTO / "piper_voices" / "es_MX-claude-high.onnx"
    ruta_vosk: Path = RAIZ_DEL_PROYECTO / "vosk" / "vosk-model-small-es-0.42"
    carpeta_audio: Path = Path("/tmp/audio_orientacion")
    microfono_id: int | None = None
    ventana_comando_s: float = 8.0

    # ------------------------------------------------------------------
    # MPU6050 (giroscopio + acelerometro)
    # ------------------------------------------------------------------
    bus_i2c: int = 1
    direccion_mpu: int = 0x68
    registro_despertar: int = 0x6B
    registro_datos: int = 0x3B
    offset_giroscopio: float = 0.445992
    velocidad_minima_giro_dps: float = 8.0
    signo_derecha: int = 1
    umbral_paso_g: float = 1.12
    intervalo_paso_s: float = 0.35

    # ------------------------------------------------------------------
    # Movimiento (giro inicial, media vuelta y pasos)
    # ------------------------------------------------------------------
    grados_giro_inicial: int = 90
    grados_media_vuelta: int = 180
    pasos_objetivo: int = 5

    # ------------------------------------------------------------------
    # HC-SR04 (alerta de proximidad)
    # ------------------------------------------------------------------
    chip_gpio: int = 15
    pin_echo: int = 23
    pin_trigger: int = 18
    distancia_maxima_m: float = 4.0
    distancia_alerta_cm: float = 200.0

    def preparar(self) -> None:
        """Crea las carpetas que el sistema necesita para funcionar."""
        self.carpeta_audio.mkdir(parents=True, exist_ok=True)
