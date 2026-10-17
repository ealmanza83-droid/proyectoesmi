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

import glob
import os
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
        self._lgpio = None
        self._trigger = config.pin_trigger
        self._echo = config.pin_echo
        self._distancia_alerta_cm = config.distancia_alerta_cm
        self._distancia: float | None = None
        self._aviso_pendiente = True
        self._ultimo_aviso = 0.0
        self._detener = threading.Event()
        self._manejo = None
        self._chip_info = "chip lgpio desconocido"
        self._hilo: threading.Thread | None = None

        try:
            import lgpio

            self._lgpio = lgpio
        except ImportError:
            print(
                "AVISO HC-SR04: falta la libreria lgpio "
                "(sudo apt install python3-lgpio). La guia sigue sin pitidos."
            )

        if self._lgpio is not None:
            self._manejo = self._abrir(config.chip_lgpio)

        # Un sensor ausente o mal conectado no puede tumbar la guia:
        # la orientacion y la camara siguen funcionando sin pitidos.
        if self._manejo is not None:
            self._hilo = threading.Thread(
                target=self._bucle, name="hc-sr04", daemon=True
            )
            self._hilo.start()

    # ------------------------------------------------------------------
    # Arranque del GPIO
    # ------------------------------------------------------------------
    def _abrir(self, chip_preferido: int | None):
        """Reclama los dos pines en el chip de la cabecera GPIO.

        En cada modelo de Raspberry Pi los pines del conector estan en
        el chip cuya etiqueta empieza por "pinctrl" ("pinctrl-rp1" en
        la Pi 5, "pinctrl-bcm2711" en la Pi 4, "pinctrl-bcm2835" en la
        3).  Los demas chips dejan reclamar lineas sin error, pero no
        van al conector: por eso el chip se identifica por su etiqueta
        y los que no son "pinctrl" se descartan (ademas suelen controlar
        el PCIe o el firmware, y tocarlos seria peligroso).
        """
        lgpio = self._lgpio
        ultimo_error = "sin intentos: no hay chips gpio visibles"

        for numero in self._chips_candidatos(chip_preferido):
            try:
                manejo = lgpio.gpiochip_open(numero)
            except Exception as error:
                ultimo_error = f"chip {numero}: no se pudo abrir ({error})"
                continue

            info = self._info_chip(manejo)
            try:
                lgpio.gpio_claim_output(manejo, self._trigger, 0)
                lgpio.gpio_claim_input(manejo, self._echo)
            except Exception as error:
                ultimo_error = f"chip {numero} ({info}): pines ocupados ({error})"
                try:
                    lgpio.gpiochip_close(manejo)
                except Exception:
                    pass
                continue

            if self._es_chip_cabecera(manejo):
                self._chip_info = f"chip lgpio {numero} ({info})"
                print(f"HC-SR04 OK ({self._chip_info})")
                return manejo

            # Lineas reclamadas, pero el chip no controla la cabecera:
            # se libera y se sigue buscando el "pinctrl".
            ultimo_error = (
                f"chip {numero} ({info}): no es el de la cabecera (pinctrl)"
            )
            try:
                lgpio.gpiochip_close(manejo)
            except Exception:
                pass

        self._avisar_fallo_gpio(ultimo_error)
        return None

    def _leer_info_chip(self, manejo):
        """(lineas, nombre, etiqueta) del chip o ``None`` si no se puede."""
        funcion = getattr(self._lgpio, "gpio_get_chip_info", None)
        if funcion is None:
            return None
        try:
            datos = funcion(manejo)
            return int(datos[0]), str(datos[1]), str(datos[2])
        except Exception:
            return None

    def _info_chip(self, manejo) -> str:
        datos = self._leer_info_chip(manejo)
        if datos is None:
            return "etiqueta desconocida"
        lineas, nombre, etiqueta = datos
        return f"{etiqueta or nombre}, {lineas} lineas"

    def _es_chip_cabecera(self, manejo) -> bool:
        """El chip de la cabecera siempre se llama "pinctrl-*"."""
        datos = self._leer_info_chip(manejo)
        if datos is None:
            # Sin etiqueta no se puede descartar: se acepta el chip.
            return True
        return "pinctrl" in datos[2].lower()

    def _chips_candidatos(self, chip_preferido: int | None):
        """Numeros de chip a probar: el preferido y todos los existentes."""
        encontrados: list[int] = []
        for ruta in glob.glob("/dev/gpiochip*"):
            try:
                encontrados.append(int(ruta.rsplit("chip", 1)[1]))
            except ValueError:
                continue
        lista = [chip_preferido, *sorted(encontrados), 0, 4]
        return dict.fromkeys(c for c in lista if c is not None)

    def _avisar_fallo_gpio(self, ultimo_error: str) -> None:
        """Diagnostico concreto: permisos, sistema o pines ocupados."""
        chips = sorted(glob.glob("/dev/gpiochip*"))
        legibles = [c for c in chips if os.access(c, os.R_OK | os.W_OK)]

        print(
            f"AVISO HC-SR04: no se pudo usar el sensor (TRIG={self._trigger}, "
            f"ECHO={self._echo}). La guia sigue funcionando, solo sin pitidos."
        )
        print(f"Motivo: {ultimo_error}")
        print(
            f"Chips GPIO visibles: {chips or 'ninguno'} | "
            f"legibles para este usuario: {legibles or 'ninguno'}"
        )

        # Etiqueta de cada chip: el de la cabecera se llama "pinctrl-*".
        etiquetas = []
        for ruta in chips:
            try:
                numero = int(ruta.rsplit("chip", 1)[1])
            except ValueError:
                continue
            try:
                manejo = self._lgpio.gpiochip_open(numero)
            except Exception:
                continue
            try:
                etiquetas.append(f"gpiochip{numero}: {self._info_chip(manejo)}")
            finally:
                try:
                    self._lgpio.gpiochip_close(manejo)
                except Exception:
                    pass
        if etiquetas:
            print("Etiquetas: " + " | ".join(etiquetas))
            if not any("pinctrl" in texto for texto in etiquetas):
                print(
                    "Ningun chip se llama 'pinctrl-*': el sistema no esta "
                    "expuesto con la GPIO de la cabecera (revisa "
                    "'sudo raspi-config' > Interfases > GPIO y reinstala "
                    "'python3-lgpio')."
                )

        if chips and not legibles:
            print(
                "Causa: PERMISOS sobre /dev/gpiochip*. Solucion: "
                "'sudo usermod -aG gpio $USER' y despues reinicia "
                "(o cierra y abre sesion).  Prueba rapida con: "
                "'sudo venv/bin/python main.py'."
            )
        elif not chips:
            print(
                "Causa: el sistema no expone /dev/gpiochip*. Solucion: "
                "'sudo apt install --reinstall python3-lgpio' y revisa "
                "'ls /dev/gpiochip*'."
            )
        else:
            print(
                "Causa: los pines TRIG/ECHO no estan libres en el chip de "
                "la cabecera (revisa que ningun otro programa los use)."
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
            f"AVISO HC-SR04: sin eco usando el {self._chip_info}. "
            "Revisa VCC (5V), GND, TRIG y ECHO (con divisor de voltaje: "
            "el ECHO del sensor es de 5V y el GPIO solo admite 3.3V). "
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
        if self._hilo is not None and self._hilo.is_alive():
            self._hilo.join(2.0)
        if self._manejo is not None:
            try:
                self._lgpio.gpiochip_close(self._manejo)
            except Exception:
                pass
            self._manejo = None

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
