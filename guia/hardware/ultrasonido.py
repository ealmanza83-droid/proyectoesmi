"""Sensor ultrasonico HC-SR04 y su alerta sonora de proximidad.

La regla es sencilla: cuanto mas cerca esta un obstaculo, mas
frecuentes son los pitidos (y mas de la distancia de alerta = silencio).
El ritmo crece de forma continua, sin saltos: el sonido "se acelera"
solo a medida que la persona se acerca al objeto.
"""

from __future__ import annotations

import threading
import time

from gpiozero import DistanceSensor
from gpiozero.pins.lgpio import LGPIOFactory

from ..configuracion import Configuracion
from ..audio.alarma import ReproductorBeep

# Segundos entre pitidos en los extremos del rango de alerta.
INTERVALO_LEJANO_S = 1.20  # justo en el limite de la distancia de alerta
INTERVALO_CERCA_S = 0.10   # rozando el obstaculo


def intervalo_de_beep(distancia_cm: float, distancia_alerta_cm: float) -> float | None:
    """Segundos entre pitidos para una distancia dada (``None`` = silencio).

    El intervalo se acorta de forma proporcional a la distancia: a
    medida que la persona avanza hacia el obstaculo los pitidos se
    aceleran sin saltos bruscos.
    """
    if distancia_cm > distancia_alerta_cm or distancia_alerta_cm <= 0:
        return None
    fraccion = max(min(distancia_cm / distancia_alerta_cm, 1.0), 0.0)
    return INTERVALO_CERCA_S + fraccion * (INTERVALO_LEJANO_S - INTERVALO_CERCA_S)


class SensorUltrasonico:
    """Distancia al obstaculo frontal, en centimetros."""

    def __init__(self, config: Configuracion):
        self._factoria = LGPIOFactory(chip=config.chip_gpio)
        self._sensor = DistanceSensor(
            echo=config.pin_echo,
            trigger=config.pin_trigger,
            pin_factory=self._factoria,
            max_distance=config.distancia_maxima_m,
        )
        self._distancia_alerta_cm = config.distancia_alerta_cm
        print("HC-SR04 OK")

    @property
    def distancia_cm(self) -> float:
        """Distancia al obstaculo en centimetros."""
        return self._sensor.distance * 100

    @property
    def distancia_alerta_cm(self) -> float:
        return self._distancia_alerta_cm

    def cerrar(self) -> None:
        """Libera los pines GPIO."""
        try:
            self._sensor.close()
        except Exception:
            pass
        try:
            self._factoria.close()
        except Exception:
            pass

    def __enter__(self) -> "SensorUltrasonico":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cerrar()


class AlertaProximidad(threading.Thread):
    """Hilo que emite pitidos mientras la persona camina."""

    def __init__(
        self,
        sensor: SensorUltrasonico,
        alarma: ReproductorBeep,
        voz_activa: threading.Event,
        tiempo_muestreo_s: float = 0.03,
    ):
        super().__init__(name="alerta-proximidad", daemon=True)
        self._sensor = sensor
        self._alarma = alarma
        self._voz_activa = voz_activa
        self._tiempo_muestreo_s = tiempo_muestreo_s
        self._detener = threading.Event()

    def run(self) -> None:
        ultima_alerta = 0.0

        while not self._detener.is_set():
            try:
                ahora = time.monotonic()
                distancia = self._sensor.distancia_cm
                intervalo = intervalo_de_beep(
                    distancia, self._sensor.distancia_alerta_cm
                )

                # Nunca tapar la voz, superponer pitidos ni competir
                # con la alarma continua de una caida.
                if (
                    intervalo is not None
                    and ahora - ultima_alerta >= intervalo
                    and not self._voz_activa.is_set()
                    and not self._alarma.alarma_activa
                ):
                    self._alarma.reproducir()
                    ultima_alerta = ahora
            except Exception as error:
                print("ERROR HC-SR04:", error)

            self._detener.wait(self._tiempo_muestreo_s)

    def detener(self, espera_s: float = 2.0) -> None:
        """Senala la salida y espera a que el hilo termine."""
        self._detener.set()
        if self.is_alive():
            self.join(espera_s)
