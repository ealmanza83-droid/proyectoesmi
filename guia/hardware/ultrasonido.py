"""Sensor ultrasonico HC-SR04 y su alerta sonora de proximidad.

La regla es sencilla: cuanto mas cerca esta un obstaculo, mas
frecuentes son los pitidos (y mas de la distancia de alerta = silencio).
El ritmo crece de forma continua, sin saltos: el sonido "se acelera"
solo a medida que la persona se acerca al objeto.

La medicion del eco se hace en un hilo propio con ``lgpio`` (la
Raspberry Pi 5 no soporta RPi.GPIO).  La propiedad ``distancia_cm``
**nunca se bloquea**: devuelve la ultima medida conocida o ``None``
mientras no llegue eco, de modo que un sensor mal conectado deja al
asistente sin pitidos, pero jamas congelado.
"""

from __future__ import annotations

import threading
import time

from ..configuracion import Configuracion
from ..audio.alarma import ReproductorBeep

# Segundos entre pitidos en los extremos del rango de alerta.
INTERVALO_LEJANO_S = 1.20  # justo en el limite de la distancia de alerta
INTERVALO_CERCA_S = 0.10   # rozando el obstaculo

# Medicion del HC-SR04.
INTERVALO_MIDIENDO_S = 0.12   # el sensor pide al menos 60 ms entre ecos
INTERVALO_SIN_ECO_S = 0.5     # sin eco no hay que apurar
TIEMPO_ECO_S = 0.03           # sin eco en este tiempo, obstaculo lejisimo
MICROSEGUNDOS_POR_CM = 58.0   # ida y vuelta del sonido en el aire
PAUSA_TRIGGER_S = 10e-6       # pulso minimo de activacion
AVISO_SIN_ECO_S = 5.0         # no llenar la consola si el sensor no esta


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
    """Distancia al obstaculo frontal, en centimetros (hilo propio)."""

    def __init__(self, config: Configuracion):
        try:
            import lgpio
        except ImportError as error:
            raise RuntimeError(
                "Falta la libreria lgpio: sudo apt install python3-lgpio"
            ) from error

        self._lgpio = lgpio
        self._trigger = config.pin_trigger
        self._echo = config.pin_echo
        self._distancia_alerta_cm = config.distancia_alerta_cm
        self._distancia: float | None = None
        self._aviso_pendiente = True
        self._ultimo_aviso = 0.0
        self._detener = threading.Event()
        self._manejo = self._abrir(config.chip_lgpio)

        self._hilo = threading.Thread(
            target=self._bucle, name="hc-sr04", daemon=True
        )
        self._hilo.start()

    # ------------------------------------------------------------------
    # Arranque del GPIO
    # ------------------------------------------------------------------
    def _abrir(self, chip_preferido: int | None):
        """Reclama los dos pines en el chip lgpio que los contenga.

        El numero de chip del encabezado varia entre versiones del
        sistema (en la Pi 5 suele ser el 0 o el 4), y un chip que no
        contiene las lineas no falla al abrir: por eso la comprobacion
        es intentar reclamar los pines en cada candidato.
        """
        lgpio = self._lgpio
        candidatos = [chip_preferido, 0, 4, 1, 2, 3]
        ultimo_error: Exception | None = None

        for numero in dict.fromkeys(c for c in candidatos if c is not None):
            try:
                manejo = lgpio.gpiochip_open(numero)
            except Exception as error:
                ultimo_error = error
                continue
            try:
                lgpio.gpio_claim_output(manejo, self._trigger, 0)
                lgpio.gpio_claim_input(manejo, self._echo)
            except Exception as error:
                ultimo_error = error
                try:
                    lgpio.gpiochip_close(manejo)
                except Exception:
                    pass
                continue
            print(f"HC-SR04 OK (chip lgpio {numero})")
            return manejo

        raise RuntimeError(
            f"HC-SR04: no se pudieron reclamar los pines TRIG={self._trigger} "
            f"ECHO={self._echo} (ultimo error: {ultimo_error})"
        )

    # ------------------------------------------------------------------
    # Medicion
    # ------------------------------------------------------------------
    def _bucle(self) -> None:
        while not self._detener.is_set():
            medida = self._medir()
            if medida is None:
                self._avisar_sin_eco()
                self._detener.wait(INTERVALO_SIN_ECO_S)
            else:
                if self._aviso_pendiente:
                    print(f"HC-SR04: eco recuperado ({medida:.0f} cm)")
                    self._aviso_pendiente = False
                self._distancia = medida
                self._detener.wait(INTERVALO_MIDIENDO_S)

    def _medir(self) -> float | None:
        """Dispara un pulso y mide el eco (centimetros, ``None`` si no hay)."""
        lgpio = self._lgpio
        manejo = self._manejo

        try:
            lgpio.gpio_write(manejo, self._trigger, 1)
            time.sleep(PAUSA_TRIGGER_S)
            lgpio.gpio_write(manejo, self._trigger, 0)

            # Espera del eco: si no llega, hay que seguir con la guia.
            limite = time.perf_counter() + TIEMPO_ECO_S
            while lgpio.gpio_read(manejo, self._echo) == 0:
                if time.perf_counter() >= limite:
                    return None

            inicio = time.perf_counter()
            while lgpio.gpio_read(manejo, self._echo) == 1:
                if time.perf_counter() - inicio >= TIEMPO_ECO_S:
                    return None
            microsegundos = (time.perf_counter() - inicio) * 1e6
        except Exception as error:
            self._avisar_sin_eco()
            print("ERROR HC-SR04:", error)
            return None

        return microsegundos / MICROSEGUNDOS_POR_CM

    def _avisar_sin_eco(self) -> None:
        """Aviso unico y repetido cada rato si el sensor no contesta."""
        ahora = time.monotonic()
        if ahora - self._ultimo_aviso < AVISO_SIN_ECO_S:
            return
        self._ultimo_aviso = ahora
        if not self._aviso_pendiente:
            self._aviso_pendiente = True
            print("HC-SR04: se perdio el eco.")
        print(
            "AVISO HC-SR04: sin eco (revisa VCC, GND, ECHO y TRIG). "
            "La guia sigue funcionando, solo sin pitidos."
        )

    # ------------------------------------------------------------------
    # Interfaz publica
    # ------------------------------------------------------------------
    @property
    def distancia_cm(self) -> float | None:
        """Ultima distancia medida; ``None`` mientras no llegue eco."""
        return self._distancia

    @property
    def distancia_alerta_cm(self) -> float:
        return self._distancia_alerta_cm

    def cerrar(self) -> None:
        """Detiene la medicion y libera los pines GPIO."""
        self._detener.set()
        if self._hilo.is_alive():
            self._hilo.join(2.0)
        try:
            self._lgpio.gpiochip_close(self._manejo)
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
