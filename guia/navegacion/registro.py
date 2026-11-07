"""Registro opcional de sensores durante la navegacion."""

from __future__ import annotations

import csv
import time
from pathlib import Path

from ..hardware.mpu6050 import LecturaMPU

COLUMNAS_REGISTRO = (
    "t",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "distancia_ultrasonido",
)
MUESTRAS_ENTRE_FLUSH = 50


class RegistroCaminata:
    """Escribe muestras del mismo MPU y HC-SR04 que usa la aplicacion."""

    def __init__(self, salida: Path, distancia_ultrasonico) -> None:
        salida.parent.mkdir(parents=True, exist_ok=True)
        self._archivo = salida.open("w", newline="", encoding="utf-8")
        self._escritor = csv.DictWriter(
            self._archivo, fieldnames=COLUMNAS_REGISTRO
        )
        self._escritor.writeheader()
        self._archivo.flush()
        self._sensor = distancia_ultrasonico
        self._inicio = time.monotonic()
        self._muestras = 0
        self._cerrado = False
        self._fallo = False
        print(f"REGISTRO SENSORES: {salida}")

    def en_muestra(self, lectura: LecturaMPU, _dt: float) -> None:
        """Guarda la lectura del MPU y la distancia ultrasónica vigente."""
        if self._cerrado or self._fallo:
            return
        distancia = self._sensor.distancia_cm
        try:
            self._escritor.writerow(
                {
                    "t": f"{time.monotonic() - self._inicio:.6f}",
                    "ax": f"{lectura.ax:.6f}",
                    "ay": f"{lectura.ay:.6f}",
                    "az": f"{lectura.az:.6f}",
                    "gx": f"{lectura.gx:.6f}",
                    "gy": f"{lectura.gy:.6f}",
                    "gz": f"{lectura.gz:.6f}",
                    "distancia_ultrasonido": (
                        "" if distancia is None else f"{distancia:.1f}"
                    ),
                }
            )
            self._muestras += 1
            if self._muestras % MUESTRAS_ENTRE_FLUSH == 0:
                self._archivo.flush()
        except OSError as error:
            self._fallo = True
            print(f"ERROR REGISTRO SENSORES: {error}")

    def cerrar(self) -> None:
        """Vacía el buffer y cierra el archivo."""
        if self._cerrado:
            return
        self._cerrado = True
        try:
            self._archivo.flush()
        except OSError as error:
            print(f"ERROR AL GUARDAR REGISTRO SENSORES: {error}")
        finally:
            self._archivo.close()
        print(f"REGISTRO SENSORES: {self._muestras} muestras guardadas")
