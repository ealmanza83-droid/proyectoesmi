"""Contadores de movimiento basados en el MPU6050.

El giroscopio no mide angulos: mide *velocidad angular* (grados por
segundo).  Para saber cuantos grados ha girado la persona hace falta
integrar esa velocidad a una frecuencia fija.  Por eso
:class:`MuestreadorMPU` lee el sensor en su propio hilo (100 veces por
segundo) y le pasa cada lectura a los oyentes:

* :class:`MedidorGiro` integra la velocidad angular en grados.
* :class:`ContadorPasos` detecta los picos de aceleracion al caminar.

Asi un giro lento, una pausa del bucle principal o la carga de la
camara ya no hacen perder la cuenta de grados.
"""

from __future__ import annotations

import threading
import time

from ..configuracion import Configuracion
from ..hardware.mpu6050 import LecturaMPU, MPU6050

# Tope del dt integrado: una parada del hilo no debe traducirse en un
# salto enorme de grados.
DT_MAXIMO_S = 0.1


class MuestreadorMPU(threading.Thread):
    """Lee el MPU6050 a frecuencia fija y reparte cada lectura."""

    def __init__(self, mpu: MPU6050, config: Configuracion, oyentes):
        super().__init__(name="muestreador-mpu", daemon=True)
        self._mpu = mpu
        self._intervalo = config.intervalo_muestreo_mpu_s
        self._oyentes = tuple(oyentes)
        self._detener = threading.Event()

    def muestrear(self, lectura: LecturaMPU, dt: float) -> None:
        """Reparte una lectura entre los oyentes (tambien en pruebas)."""
        for oyente in self._oyentes:
            oyente(lectura, dt)

    def run(self) -> None:
        anterior = time.monotonic()

        while not self._detener.is_set():
            ahora = time.monotonic()
            dt = min(ahora - anterior, DT_MAXIMO_S)
            anterior = ahora

            try:
                self.muestrear(self._mpu.leer(), dt)
            except Exception as error:
                print("ERROR MPU6050:", error)

            self._detener.wait(self._intervalo)

    def detener(self, espera_s: float = 2.0) -> None:
        """Senala la salida y espera a que el hilo termine."""
        self._detener.set()
        if self.is_alive():
            self.join(espera_s)


class MedidorGiro:
    """Integra la velocidad angular hasta alcanzar un objetivo."""

    def __init__(self, config: Configuracion):
        self._config = config
        self._angulo_derecha = 0.0
        self._angulo_izquierda = 0.0
        self._sesgo = config.offset_giroscopio

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Integra una lectura del sensor (la llama el muestreador).

        En reposo el giroscopio deberia marcar cero: lo que lee es su
        propio sesgo, que se aprende despacio para no depender de un
        offset fijo que cambia con la temperatura.  Solo se integra
        cuando la persona gira de verdad (por encima de la velocidad
        minima), asi el temblor de la mano no cuenta como giro.
        """
        dt = min(dt, DT_MAXIMO_S)
        velocidad = lectura.gz

        if abs(velocidad - self._sesgo) <= self._config.velocidad_reposo_dps:
            if dt > 0:
                alfa = dt / self._config.constante_sesgo_s
                self._sesgo += (velocidad - self._sesgo) * alfa

        direccion = (velocidad - self._sesgo) * self._config.signo_derecha
        umbral = self._config.velocidad_minima_giro_dps

        if direccion >= umbral:
            self._angulo_derecha += abs(direccion) * dt
        elif direccion <= -umbral:
            self._angulo_izquierda += abs(direccion) * dt

    def avanzar(self, derecha: bool) -> float:
        """Grados acumulados en el sentido pedido desde ``reiniciar()``."""
        return self._angulo_derecha if derecha else self._angulo_izquierda

    def reiniciar(self) -> None:
        """Pone ambos acumulados en cero."""
        self._angulo_derecha = 0.0
        self._angulo_izquierda = 0.0


class ContadorPasos:
    """Detecta pasos por picos de aceleracion (con enfriamiento)."""

    def __init__(self, config: Configuracion):
        self._config = config
        self._pasos = 0
        self._ultimo_paso = 0.0

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Cuenta un paso si la muestra trae un pico de aceleracion."""
        ahora = time.monotonic()

        if (
            lectura.magnitud_aceleracion >= self._config.umbral_paso_g
            and ahora - self._ultimo_paso >= self._config.intervalo_paso_s
        ):
            self._pasos += 1
            self._ultimo_paso = ahora
            print(f"PASO: {self._pasos}")

    @property
    def pasos(self) -> int:
        """Pasos contados desde la ultima vez que se reinicio."""
        return self._pasos

    def avanzar(self) -> int:
        """Devuelve los pasos contados hasta ahora."""
        return self._pasos

    def reiniciar(self) -> None:
        """Pone el contador en cero."""
        self._pasos = 0
        self._ultimo_paso = 0.0
