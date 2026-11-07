"""MPU6050 por I2C: acelerometro y giroscopio.

Encapsula el registro a registro del sensor para que el resto del
programa solo pida ``leer()`` y reciba una lectura ya escalada.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ..configuracion import Configuracion

ESCALA_ACELEROMETRO = 16384.0  # +/- 2 g
ESCALA_GIROSCOPIO = 131.0      # +/- 250 grados/segundo
# DLPF_CFG = 3: filtro digital que deja el acelerometro a 44 Hz y el
# giroscopio a 42 Hz de ancho de banda.  Con el valor por defecto
# (260 / 256 Hz) pasaba TODO el ruido y las vibraciones de la cintura;
# con el filtro la sigma en reposo baja aproximadamente a la mitad
# (comprobable con pruebas/caracterizar_mpu6050.py) mientras que las
# senales de la persona (pasos de 2 a 6 Hz, giros por debajo de 30 Hz)
# pasan con un retardo de ~5 ms, despreciable.
FILTRO_DLPF = 3


@dataclass(frozen=True)
class LecturaMPU:
    """Valores escalados de un instante del sensor."""

    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float

    @property
    def magnitud_aceleracion(self) -> float:
        """Magnitud total de la aceleracion (1.0 g en reposo)."""
        return math.sqrt(self.ax**2 + self.ay**2 + self.az**2)


class MPU6050:
    """Interfaz de alto nivel del sensor inercial."""

    def __init__(self, config: Configuracion):
        from smbus2 import SMBus

        self._config = config
        self._bus = SMBus(config.bus_i2c)
        # 0 en PWR_MGMT_1 saca al sensor del modo suspension.
        self._bus.write_byte_data(
            config.direccion_mpu, config.registro_despertar, 0
        )
        # Filtro digital activado tras despertar (ver FILTRO_DLPF).
        self._bus.write_byte_data(
            config.direccion_mpu, config.registro_filtro, FILTRO_DLPF
        )
        print("MPU6050 OK (DLPF 44/42 Hz)")

    def leer(self) -> LecturaMPU:
        """Devuelve una lectura completa del sensor."""
        datos = self._bus.read_i2c_block_data(
            self._config.direccion_mpu, self._config.registro_datos, 14
        )
        # ax, ay, az, temperatura, gx, gy, gz (14 bytes = 7 enteros).
        ax, ay, az, _temp, gx, gy, gz = struct.unpack(">7h", bytes(datos))
        return LecturaMPU(
            ax=ax / ESCALA_ACELEROMETRO,
            ay=ay / ESCALA_ACELEROMETRO,
            az=az / ESCALA_ACELEROMETRO,
            gx=gx / ESCALA_GIROSCOPIO,
            gy=gy / ESCALA_GIROSCOPIO,
            gz=gz / ESCALA_GIROSCOPIO,
        )

    def cerrar(self) -> None:
        """Cierra el bus I2C."""
        try:
            self._bus.close()
        except Exception:
            pass

    def __enter__(self) -> "MPU6050":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cerrar()
