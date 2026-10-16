"""Sensor ultrasonico HC-SR04 y su alerta sonora de proximidad.

La regla es sencilla: cuanto mas cerca esta un obstaculo, mas
frecuentes son los pitidos (y mas de 2 metros = silencio).
"""

from __future__ import annotations

import threading
import time

from gpiozero import DistanceSensor
from gpiozero.pins.lgpio import LGPIOFactory

from ..configuracion import Configuracion
from ..audio.alarma import ReproductorBeep

# Tramos de distancia -> segundos entre pitidos.
TRAMAS_BEEP: tuple[tuple[float, float], ...] = (
    (150.0, 1.20),  # 1.50 - 2.00 m
    (100.0, 0.70),  # 1.00 - 1.50 m
    (50.0, 0.35),   # 0.50 - 1.00 m
    (25.0, 0.18),   # 0.25 - 0.50 m
    (0.0, 0.12),    # menos de 25 cm
)


def intervalo_de_beep(distancia_cm: float, distancia_alerta_cm: float) -> float | None:
    """Segundos entre pitidos para una distancia dada (``None`` = silencio)."""
    if distancia_cm > distancia_alerta_cm:
        return None
    for limite, intervalo in TRAMAS_BEEP:
        if distancia_cm > limite:
            return intervalo
    return TRAMAS_BEEP[-1][1]


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

                # Nunca tapar la voz ni superponer pitidos.
                if (
                    intervalo is not None
                    and ahora - ultima_alerta >= intervalo
                    and not self._voz_activa.is_set()
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
