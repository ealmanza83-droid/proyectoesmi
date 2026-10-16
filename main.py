"""Punto de entrada del asistente de orientacion para personas invidentes.

Uso::

    python main.py

El programa espera, en la Raspberry Pi: camara con senales
orientadoras, MPU6050, HC-SR04, microfono y altavoz conectados.
"""

from __future__ import annotations

from guia.aplicacion import Aplicacion
from guia.audio.alarma import ReproductorBeep
from guia.audio.locutor import Locutor
from guia.audio.microfono import Microfono
from guia.configuracion import Configuracion
from guia.hardware.camara import Camara
from guia.hardware.mpu6050 import MPU6050
from guia.hardware.ultrasonido import SensorUltrasonico
from guia.vision.detector import DetectorVisual


def construir_aplicacion(config: Configuracion) -> Aplicacion:
    """Monta el equipo completo y lo entrega a la aplicacion."""
    camara = Camara(config)
    mpu = MPU6050(config)
    sensor = SensorUltrasonico(config)

    alarma = ReproductorBeep(config.carpeta_audio)
    locutor = Locutor(config.ruta_voz, config.carpeta_audio)
    microfono = Microfono(config)
    detector = DetectorVisual(config)

    return Aplicacion(
        config=config,
        camara=camara,
        mpu=mpu,
        sensor=sensor,
        alarma=alarma,
        locutor=locutor,
        microfono=microfono,
        detector=detector,
    )


def main() -> int:
    config = Configuracion()
    config.preparar()

    print()
    print("======================================")
    print("GUIA DE ORIENTACION - RASPBERRY PI 5")
    print("======================================")

    aplicacion: Aplicacion | None = None
    try:
        aplicacion = construir_aplicacion(config)
        aplicacion.ejecutar()
    finally:
        # Los recursos de hardware se liberan aunque algo falle
        # (Ctrl+C, falta un periferico, error de YOLO, ...).
        if aplicacion is not None:
            aplicacion.camara.cerrar()
            aplicacion.mpu.cerrar()
            aplicacion.sensor.cerrar()
        print("Recursos de hardware liberados.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
