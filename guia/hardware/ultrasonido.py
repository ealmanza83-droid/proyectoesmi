"""Sensor ultrasonico HC-SR04 y su alerta sonora de proximidad.

La regla es sencilla: cuanto mas cerca esta un obstaculo, mas
frecuentes son los pitidos (y mas de la distancia de alerta = silencio).
El ritmo crece de forma continua, sin saltos: el sonido "se acelera"
solo a medida que la persona se acerca al objeto.

La medicion del eco se hace con **gpiozero** (``DistanceSensor``), tal
como se verifico el sensor en esta Raspberry Pi 5 con el script de
``pruebas/prueba_ultrasonido.py``: chip lgpio 15, TRIG en GPIO18 y
ECHO en GPIO23.  La primera lectura de ``sensor.distance`` espera el
primer eco (hasta ~0.5 s mientras gpiozero calienta su cola de
muestras), por eso **solo se lee en el hilo propio**: la propiedad
``distancia_cm`` jamas bloquea el bucle principal, devuelve la ultima
medida conocida o ``None`` mientras no llegue eco, de modo que un
sensor mal conectado deja al asistente sin pitidos, pero nunca
congelado.
"""

from __future__ import annotations

import threading
import time
import warnings

from ..configuracion import Configuracion
from ..audio.alarma import ReproductorBeep

# Segundos entre pitidos en los extremos del rango de alerta.
INTERVALO_LEJANO_S = 1.20  # justo en el limite de la distancia de alerta
INTERVALO_CERCA_S = 0.10   # rozando el obstaculo

# Medicion del HC-SR04 (en el hilo propio).
INTERVALO_MIDIENDO_S = 0.12   # el sensor pide al menos 60 ms entre ecos
INTERVALO_SIN_ECO_S = 0.5     # sin eco no hay que apurar


def intervalo_de_beep(
    distancia_cm: float | None, distancia_alerta_cm: float
) -> float | None:
    """Segundos entre pitidos para una distancia dada (``None`` = silencio).

    El intervalo se acorta de forma proporcional a la distancia: a
    medida que la persona avanza hacia el obstaculo los pitidos se
    aceleran sin saltos bruscos.  Sin medida (``None``) no hay pitidos.
    """
    if (
        distancia_cm is None
        or distancia_cm > distancia_alerta_cm
        or distancia_alerta_cm <= 0
    ):
        return None
    fraccion = max(min(distancia_cm / distancia_alerta_cm, 1.0), 0.0)
    return INTERVALO_CERCA_S + fraccion * (INTERVALO_LEJANO_S - INTERVALO_CERCA_S)


class SensorUltrasonico:
    """Distancia al obstaculo de enfrente, medida en un hilo propio.

    La creacion del sensor gpiozero se hace exactamente como en el
    script verificado en esta Raspberry Pi
    (``pruebas/prueba_ultrasonido.py``)::

        factory = LGPIOFactory(chip=15)
        sensor = DistanceSensor(echo=23, trigger=18,
                                pin_factory=factory, max_distance=4)

    Si gpiozero o el chip lgpio no estan disponibles, se imprime un
    aviso con la solucion y el asistente sigue funcionando **sin
    pitidos de proximidad** (nunca se detiene por esto).
    """

    def __init__(self, config: Configuracion):
        self._config = config
        self._distancia_cm: float | None = None
        self._secuencia_medicion = 0
        self._distancia_alerta_cm = config.distancia_alerta_cm
        self._sensor = None
        self._factoria = None
        self._aviso_pendiente = False
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None

        # gpiozero solo existe en la Raspberry Pi; se importa aqui
        # para poder correr las pruebas sin hardware.
        try:
            from gpiozero import DistanceSensor
            from gpiozero.pins.lgpio import LGPIOFactory
            from gpiozero.exc import DistanceSensorNoEcho, PWMSoftwareFallback

            # gpiozero avisa EN CADA intento de eco fallido; cuando no
            # hay nada a menos de 4 m (pasillo libre) eso llena la
            # consola y se come la salida de PASO/DESVIO.  El aviso de
            # "usa pigpio" tampoco aplica aqui: pigpio no soporta la
            # Raspberry Pi 5.
            warnings.filterwarnings("ignore", category=DistanceSensorNoEcho)
            warnings.filterwarnings("ignore", category=PWMSoftwareFallback)
        except Exception as error:
            self._avisar_sin_sensor(f"no se pudo importar gpiozero ({error})")
            return

        try:
            # El mismo patron del script verificado: chip lgpio fijo
            # (el numero va cambiando con las actualizaciones del
            # kernel; el de esta Pi es el 15).
            self._factoria = LGPIOFactory(chip=config.chip_lgpio)
            self._sensor = DistanceSensor(
                echo=config.pin_echo,
                trigger=config.pin_trigger,
                pin_factory=self._factoria,
                max_distance=config.distancia_maxima_m,
            )
        except Exception as error:
            self._cerrar_hardware()
            self._avisar_sin_sensor(str(error))
            return

        chip = getattr(self._factoria, "chip", config.chip_lgpio)
        print(
            f"HC-SR04 OK (gpiozero, chip {chip}, "
            f"TRIG={config.pin_trigger}, ECHO={config.pin_echo})"
        )

        self._hilo = threading.Thread(
            target=self._bucle, name="sensor-ultrasonido", daemon=True
        )
        self._hilo.start()

    def _avisar_sin_sensor(self, motivo: str) -> None:
        """Avisa por consola sin detener la guia (queda sin pitidos)."""
        print(
            f"AVISO HC-SR04: no se pudo usar el sensor "
            f"(TRIG={self._config.pin_trigger}, ECHO={self._config.pin_echo}). "
            f"La guia sigue funcionando, solo sin pitidos."
        )
        print(f"Motivo: {motivo}")
        if "busy" in motivo.lower():
            # Los pines ya los tiene reclamados OTRO proceso (o este
            # mismo, si se creo el sensor dos veces).
            print(
                "Causa: otro proceso esta usando los pines GPIO ahora "
                "mismo.  Habitual: la ventana de Thonny con "
                "prueba_ultrasonido.py abierta, o un main.py anterior "
                "dejado en segundo plano con Ctrl+Z (para matarlo: "
                "'jobs', luego 'kill %1').  Para ver quien los tiene: "
                "'sudo lsof /dev/gpiochip15'.  Cierra ese proceso y "
                "vuelve a arrancar main.py."
            )
        else:
            print(
                "Solucion: instalar gpiozero "
                "('(venv/bin/)pip install gpiozero' o 'sudo apt install "
                "python3-gpiozero') y comprobar el cableado: TRIG en GPIO18 "
                "(pin fisico 12), ECHO en GPIO23 (pin fisico 16) con divisor "
                "de voltaje. Ver pruebas/prueba_ultrasonido.py"
            )

    def _bucle(self) -> None:
        """Hilo propio: mide sin tocar el bucle principal.

        La primera lectura de ``sensor.distance`` espera el primer eco
        (gpiozero tarda hasta ~0.5 s mientras calienta su cola de
        muestras); por eso se lee aqui y jamas desde el bucle
        principal: antes ese bloqueo congelaba la guia entera
        (commit b653e16).
        """
        recuperado = False
        while not self._detener.is_set():
            try:
                metros = self._sensor.distance
            except Exception as error:
                self._distancia_cm = None
                self._secuencia_medicion += 1
                if not self._aviso_pendiente:
                    self._aviso_pendiente = True
                    print(
                        f"AVISO HC-SR04: sin eco ({error}). "
                        f"La guia sigue funcionando, solo sin pitidos."
                    )
                self._detener.wait(INTERVALO_SIN_ECO_S)
                continue

            if metros is None:
                self._distancia_cm = None
                self._secuencia_medicion += 1
                # gpiozero avisa que no llego eco en el tiempo esperado.
                if not self._aviso_pendiente:
                    self._aviso_pendiente = True
                    print(
                        "AVISO HC-SR04: sin eco. La guia sigue "
                        "funcionando, solo sin pitidos."
                    )
                self._detener.wait(INTERVALO_SIN_ECO_S)
                continue

            self._distancia_cm = round(metros * 100.0, 1)
            self._secuencia_medicion += 1
            if self._aviso_pendiente or recuperado:
                self._aviso_pendiente = False
                recuperado = True
                print(f"HC-SR04: eco recuperado ({self._distancia_cm:.0f} cm)")

            self._detener.wait(INTERVALO_MIDIENDO_S)

    # ------------------------------------------------------------------
    # Interfaz publica
    # ------------------------------------------------------------------
    @property
    def distancia_cm(self) -> float | None:
        """Ultima distancia medida; ``None`` mientras no llegue eco."""
        return self._distancia_cm

    @property
    def distancia_alerta_cm(self) -> float:
        return self._distancia_alerta_cm

    @property
    def secuencia_medicion(self) -> int:
        """Identificador creciente para procesar cada lectura una sola vez."""
        return self._secuencia_medicion

    def _cerrar_hardware(self) -> None:
        """Cierra el sensor y su factoria (en ese orden), sin protestas."""
        for objeto in (self._sensor, self._factoria):
            if objeto is not None:
                try:
                    objeto.close()
                except Exception:
                    pass
        self._sensor = None
        self._factoria = None

    def cerrar(self) -> None:
        """Detiene la medicion y libera el hardware GPIO."""
        self._detener.set()
        if self._hilo is not None and self._hilo.is_alive():
            self._hilo.join(2.0)
        self._cerrar_hardware()

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
                # Solo la ultima medida ya hecha: leer nunca bloquea.
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
