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
import platform
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
        """Reclama los dos pines en el chip indicado por la configuracion.

        Primero se usa el chip de ``configuracion.py`` (el que se
        verifico con el sensor en esta Raspberry Pi: su numero va
        cambiando con las actualizaciones del kernel) sin exigirle
        etiqueta alguna.  Si ese chip falta o no deja reclamar los
        pines, se autodetecta el de la cabecera: en cada modelo esta
        en el chip cuya etiqueta empieza por "pinctrl" ("pinctrl-rp1"
        en la Pi 5, "pinctrl-bcm2711" en la Pi 4).  Los demas chips
        dejan reclamar lineas sin error, pero no van al conector:
        controlan cosas internas (PCIe, PMIC, firmware), asi que a
        ellos si se les exige la etiqueta "pinctrl".
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
            if numero != chip_preferido and not self._es_chip_cabecera(manejo):
                # Chip interno del sistema: ni se toca, se sigue buscando
                # el "pinctrl" de la cabecera.
                ultimo_error = (
                    f"chip {numero} ({info}): no es el de la cabecera (pinctrl)"
                )
                try:
                    lgpio.gpiochip_close(manejo)
                except Exception:
                    pass
                continue

            try:
                self._reclamar_pines(manejo)
            except Exception as error:
                ultimo_error = f"chip {numero} ({info}): no se pudo reclamar ({error})"
                try:
                    lgpio.gpiochip_close(manejo)
                except Exception:
                    pass
                continue

            self._chip_info = f"chip lgpio {numero} ({info})"
            print(f"HC-SR04 OK ({self._chip_info})")
            return manejo

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
        """El chip de la cabecera siempre se llama "pinctrl-*".

        En algunos sistemas la lectura de la etiqueta viene vacia o a
        ceros (no fiable): en ese caso no se descarta el chip, para no
        dejar el sensor entero sin usar por una lectura mala.  Solo se
        rechazan los chips con etiqueta *real* que no es "pinctrl"
        (p. ej. "gpio-brcmstb", que controla el PCIe).
        """
        datos = self._leer_info_chip(manejo)
        if datos is None:
            return True
        _, _, etiqueta = datos
        if not etiqueta.strip():
            return True
        return "pinctrl" in etiqueta.lower()

    def _orden_reclamo(self) -> str:
        """Que firma usa ``lgpio.gpio_claim_*`` en esta instalacion.

        Hay dos formas en circulacion: la del modulo C,
        ``(manejo, flags, gpio)``, y la del envoltorio python,
        ``(manejo, gpio, nivel)``.  Se distinguen por el nombre del
        segundo parametro.  Un ``apt full-upgrade`` puede cambiar de
        una a otra.
        """
        try:
            import inspect

            parametros = list(
                inspect.signature(self._lgpio.gpio_claim_input).parameters
            )
        except Exception:
            return "gpio"
        if len(parametros) >= 3 and "flag" in parametros[1].lower():
            return "flags"
        return "gpio"

    def _reclamar_pines(self, manejo) -> None:
        """Reclama TRIG (salida) y ECHO (entrada) con la firma detectada.

        lgpio circula en dos formas: la del modulo C, ``(manejo, flags,
        gpio)``, y la del envoltorio python, ``(manejo, gpio, ...)``.
        Se intenta la detectada y, si revienta, se libera lo reclamado y
        se prueba la contraria.  (Antes la segunda prueba solo existia
        cuando la detectada era "flags": con la detectada "gpio" el
        reintento repetia el mismo orden equivocado.)
        """
        lgpio = self._lgpio
        detectado = self._orden_reclamo()
        contrario = "flags" if detectado == "gpio" else "gpio"
        errores: list[Exception] = []

        for orden in (detectado, contrario):
            try:
                if orden == "flags":
                    lgpio.gpio_claim_output(manejo, 0, self._trigger, 0)
                    lgpio.gpio_claim_input(manejo, 0, self._echo)
                else:
                    lgpio.gpio_claim_output(manejo, self._trigger, 0)
                    lgpio.gpio_claim_input(manejo, self._echo)
                return
            except Exception as error:
                errores.append(error)
                # Se libera lo que pudo haberse reclamado mal (en el
                # orden equivocado se coge la linea 0 por accidente).
                for linea_gpio in (0, self._trigger, self._echo):
                    try:
                        lgpio.gpio_free(manejo, linea_gpio)
                    except Exception:
                        pass

        # Si el primer intento fallo con TypeError, el orden estaba
        # equivocado y el error bueno es el del segundo; si fallo de
        # otra forma, el primero era el orden correcto y su error
        # explica que le pasa al pin.
        if errores and not isinstance(errores[0], TypeError):
            raise errores[0]
        raise errores[-1]

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
        """Diagnostico concreto: sistema, permisos o pines ocupados."""
        chips = sorted(glob.glob("/dev/gpiochip*"))
        legibles = [c for c in chips if os.access(c, os.R_OK | os.W_OK)]

        print(
            f"AVISO HC-SR04: no se pudo usar el sensor (TRIG={self._trigger}, "
            f"ECHO={self._echo}). La guia sigue funcionando, solo sin pitidos."
        )
        print(f"Motivo: {ultimo_error}")
        print(f"Kernel: {platform.release()}")
        print(
            f"Chips GPIO visibles: {chips or 'ninguno'} | "
            f"legibles para este usuario: {legibles or 'ninguno'}"
        )

        # Etiqueta y lineas de cada chip: el de la cabecera se llama
        # "pinctrl-*" y en la Raspberry Pi 5 son 54 lineas (pinctrl-rp1).
        # Un chip con 0 lineas no deja reclamar ningun pin.
        etiquetas = []
        lineas_vistas: list[int] = []
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
                informacion = self._leer_info_chip(manejo)
                if informacion is not None:
                    lineas_vistas.append(int(informacion[0]))
                etiquetas.append(f"gpiochip{numero}: {self._info_chip(manejo)}")
            finally:
                try:
                    self._lgpio.gpiochip_close(manejo)
                except Exception:
                    pass

        sin_pinctrl = bool(etiquetas) and not any(
            "pinctrl" in texto.lower() for texto in etiquetas
        )
        todos_vacios = bool(lineas_vistas) and all(
            lineas == 0 for lineas in lineas_vistas
        )
        if etiquetas:
            print("Etiquetas: " + " | ".join(etiquetas))

        if sin_pinctrl and todos_vacios:
            print(
                "Causa: NINGUN chip tiene lineas (todos con 0): el kernel no "
                "registro los controladores de la GPIO.  En una Raspberry Pi "
                "5 la cabecera es el chip 'pinctrl-rp1' con 54 lineas.  "
                "Solucion: 'sudo apt update && sudo apt full-upgrade && "
                "sudo reboot' (kernel y firmware se actualizan juntos).  Si "
                "tras reiniciar sigue igual, el kernel no es el de Raspberry "
                "Pi OS: reinstala la imagen oficial de Raspberry Pi OS."
            )
        elif sin_pinctrl:
            print(
                "Causa: ningun chip se llama 'pinctrl-*': la GPIO de la "
                "cabecera no esta expuesta por el kernel.  Revisa "
                "'sudo raspi-config' > Interfases > GPIO y reinicia."
            )
        elif chips and not legibles:
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

    def _estado_echo(self) -> str:
        """Que hace la linea ECHO ahora mismo (diagnostico de cableado)."""
        try:
            lecturas = [
                self._lgpio.gpio_read(self._manejo, self._echo)
                for _ in range(200)
            ]
        except Exception:
            return "sin lectura"
        altos = sum(lecturas)
        if altos == 0:
            return (
                "SIEMPRE EN BAJO: el sensor no contesta; revisa VCC (5V) "
                "y GND, y que TRIG llegue al pin 12"
            )
        if altos == len(lecturas):
            return (
                "SIEMPRE EN ALTO: casi seguro ECHO conectado a 5V sin "
                "divisor de voltaje; el GPIO solo admite 3.3V"
            )
        return f"alterna correctamente ({altos}/200 altos)"

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
            f"ECHO {self._estado_echo()}. "
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
