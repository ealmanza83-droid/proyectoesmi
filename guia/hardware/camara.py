"""Camara Raspberry Pi (Picamera2)."""

from __future__ import annotations

import time

import cv2
from picamera2 import Picamera2

from ..configuracion import Configuracion


class Camara:
    """Captura frames en formato BGR listos para OpenCV/YOLO."""

    def __init__(self, config: Configuracion, espera_arranque_s: float = 2.0):
        self._camara = Picamera2()
        self._camara.configure(
            self._camara.create_video_configuration(
                main={
                    "format": "YUYV",
                    "size": (config.ancho, config.alto),
                }
            )
        )
        self._camara.start()
        time.sleep(espera_arranque_s)
        print("CAMARA OK")

    def capturar(self):
        """Devuelve el siguiente frame en BGR."""
        frame_yuyv = self._camara.capture_array()
        return cv2.cvtColor(frame_yuyv, cv2.COLOR_YUV2BGR_YUYV)

    def cerrar(self) -> None:
        """Detiene la camara (ignora errores de cierre)."""
        try:
            self._camara.stop()
        except Exception:
            pass

    def __enter__(self) -> "Camara":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cerrar()
