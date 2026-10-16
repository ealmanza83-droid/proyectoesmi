"""Contadores de movimiento basados en el MPU6050.

* :class:`MedidorGiro` integra la velocidad angular para saber cuantos
  grados ha girado la persona.
* :class:`ContadorPasos` detecta los picos de aceleracion al caminar.
"""

from __future__ import annotations

import time

from ..configuracion import Configuracion
from ..hardware.mpu6050 import MPU6050


class MedidorGiro:
    """Suma los grados girados hasta alcanzar un objetivo."""

    def __init__(self, mpu: MPU6050, config: Configuracion):
        self._mpu = mpu
        self._config = config
        self._angulo = 0.0
        self._ultimo_tiempo = 0.0
        self.reiniciar()

    @property
    def angulo(self) -> float:
        """Grados acumulados desde la ultima vez que se reinicio."""
        return self._angulo

    def reiniciar(self) -> None:
        """Pone el acumulado en cero."""
        self._angulo = 0.0
        self._ultimo_tiempo = time.monotonic()

    def avanzar(self, derecha: bool) -> float:
        """Actualiza el acumulado y devuelve los grados llevados.

        Solo suma cuando el giro va en el sentido pedido y supera la
        velocidad minima: asi el temblor de la mano no cuenta como giro.
        El ``dt`` se limita para que una pausa o una parada del sistema
        no se traduzca en un salto enorme de grados.
        """
        ahora = time.monotonic()
        dt = min(ahora - self._ultimo_tiempo, 0.2)
        self._ultimo_tiempo = ahora

        velocidad = self._mpu.leer().gz - self._config.offset_giroscopio
        direccion = velocidad * self._config.signo_derecha
        umbral = self._config.velocidad_minima_giro_dps

        movimiento_correcto = (
            direccion >= umbral if derecha else direccion <= -umbral
        )
        if movimiento_correcto:
            self._angulo += abs(velocidad) * dt

        return self._angulo


class ContadorPasos:
    """Detecta pasos por picos de aceleracion (con enfriamiento)."""

    def __init__(self, mpu: MPU6050, config: Configuracion):
        self._mpu = mpu
        self._config = config
        self._pasos = 0
        self._ultimo_paso = 0.0
        self.reiniciar()

    @property
    def pasos(self) -> int:
        """Pasos contados desde la ultima vez que se reinicio."""
        return self._pasos

    def reiniciar(self) -> None:
        """Pone el contador en cero."""
        self._pasos = 0
        self._ultimo_paso = 0.0

    def avanzar(self) -> int:
        """Mira el sensor, actualiza el contador y lo devuelve."""
        lectura = self._mpu.leer()
        ahora = time.monotonic()

        if (
            lectura.magnitud_aceleracion >= self._config.umbral_paso_g
            and ahora - self._ultimo_paso >= self._config.intervalo_paso_s
        ):
            self._pasos += 1
            self._ultimo_paso = ahora
            print(f"PASO: {self._pasos}")

        return self._pasos
