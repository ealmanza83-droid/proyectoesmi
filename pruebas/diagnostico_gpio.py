"""Diagnostico del GPIO para el HC-SR04 (se corre en la Raspberry Pi).

Imprime de una sola vez todo lo necesario para saber por que el sensor
no arranca: version del kernel, chips visibles y sus permisos, etiqueta
real de cada chip, pruebas de reclamo de TRIG/ECHO con las dos firmas
de lgpio y estado de la linea ECHO.

Uso (en la Raspberry Pi):

    cd ~/Proyectoesmi && source venv/bin/activate
    git pull origin main
    python pruebas/diagnostico_gpio.py

Pega la salida completa en el chat.
"""

from __future__ import annotations

import glob
import inspect
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from guia.configuracion import Configuracion  # noqa: E402


def _titulo(texto: str) -> None:
    print("\n" + "=" * 8, texto, "=" * 8)


def _comando(*argumentos: str) -> str:
    try:
        salida = subprocess.run(
            argumentos, capture_output=True, text=True, timeout=10
        )
        return (salida.stdout or salida.stderr).strip()
    except Exception as error:
        return f"(no se pudo ejecutar: {error})"


def _numeros_de_chips() -> list[int]:
    numeros = []
    for ruta in sorted(glob.glob("/dev/gpiochip*")):
        try:
            numeros.append(int(ruta.rsplit("chip", 1)[1]))
        except ValueError:
            continue
    return numeros


def _probar_reclamos(lgpio, manejo, trigger: int, echo: int) -> str:
    """Prueba las dos firmas de lgpio; devuelve cual funciono (o '')."""
    disposiciones = {
        "(manejo, gpio)": lambda: (
            lgpio.gpio_claim_output(manejo, trigger, 0),
            lgpio.gpio_claim_input(manejo, echo),
        ),
        "(manejo, flags, gpio)": lambda: (
            lgpio.gpio_claim_output(manejo, 0, trigger, 0),
            lgpio.gpio_claim_input(manejo, 0, echo),
        ),
    }
    funciono = ""
    for nombre, reclamo in disposiciones.items():
        try:
            reclamo()
            print(f"    reclamo TRIG={trigger}/ECHO={echo} con firma {nombre}: OK")
            funciono = funciono or nombre
        except TypeError as error:
            print(f"    firma {nombre}: TypeError ({error})")
        except Exception as error:
            print(f"    firma {nombre}: fallo ({error})")
        for libre in (0, trigger, echo):
            try:
                lgpio.gpio_free(manejo, libre)
            except Exception:
                pass
    return funciono


def _muestrear_echo(lgpio, manejo, trigger: int, echo: int) -> None:
    try:
        lgpio.gpio_claim_input(manejo, 0, echo)
    except TypeError:
        lgpio.gpio_claim_input(manejo, echo)
    except Exception as error:
        print("  no se pudo reclamar ECHO:", error)
        return

    lecturas = [lgpio.gpio_read(manejo, echo) for _ in range(200)]
    altos = sum(lecturas)
    print(f"  ECHO en reposo: {altos}/200 altos")
    if altos == 200:
        print("  -> ECHO SIEMPRE EN ALTO: falta el divisor de voltaje")
    elif altos == 0:
        print("  -> ECHO SIEMPRE EN BAJO: revisa VCC (5V), GND y el sensor")

    try:
        lgpio.gpio_claim_output(manejo, 0, trigger, 0)
    except TypeError:
        lgpio.gpio_claim_output(manejo, trigger, 0)
    print("  mandando pulso de TRIG...")
    for _ in range(10):
        try:
            lgpio.gpio_write(manejo, trigger, 1)
            time.sleep(10e-3)
            lgpio.gpio_write(manejo, trigger, 0)
        except Exception as error:
            print("  TRIG no funciona:", error)
            return
        time.sleep(0.1)
    altos = sum(lgpio.gpio_read(manejo, echo) for _ in range(200))
    print(f"  ECHO tras pulsos: {altos}/200 altos")


def main() -> None:
    config = Configuracion()

    _titulo("Sistema")
    print("kernel:", _comando("uname", "-r"))
    print("grupos:", _comando("id", "-nG"))

    _titulo("Chips visibles y permisos")
    for ruta in sorted(glob.glob("/dev/gpiochip*")):
        try:
            permiso = oct(os.stat(ruta).st_mode)[-3:]
        except OSError:
            permiso = "???"
        print(f"  {ruta} permisos {permiso}")
    if not glob.glob("/dev/gpiochip*"):
        print("  NINGUNO: el kernel no expone la GPIO")

    _titulo("/sys/kernel/debug/gpio (nombres reales de los chips)")
    try:
        with open("/sys/kernel/debug/gpio", encoding="utf-8") as archivo:
            contenido = archivo.read().strip()
        print(contenido or "(vacio)")
        if "pinctrl" not in contenido:
            print("-> NO aparece 'pinctrl': el kernel no registro la GPIO")
    except PermissionError:
        print("(sin permisos; corre: sudo cat /sys/kernel/debug/gpio)")
    except OSError as error:
        print(f"(no se pudo leer: {error})")

    _titulo("gpioinfo (opcional: sudo apt install libgpiod-tools)")
    print(_comando("gpioinfo") or "(no instalado)")

    _titulo("lgpio")
    try:
        import lgpio
    except ImportError:
        print("FALTA lgpio: sudo apt install python3-lgpio")
        return
    for funcion in ("gpio_claim_input", "gpio_claim_output"):
        try:
            firma = inspect.signature(getattr(lgpio, funcion))
            print(f"  {funcion}{firma}")
        except Exception as error:
            print(f"  {funcion}: sin firma legible ({error})")

    _titulo("Prueba de reclamo de TRIG/ECHO por chip")
    print(f"TRIG={config.pin_trigger} ECHO={config.pin_echo}")
    manejo_bueno = None
    for numero in _numeros_de_chips():
        try:
            manejo = lgpio.gpiochip_open(numero)
        except Exception as error:
            print(f"gpiochip{numero}: no se pudo abrir ({error})")
            continue
        try:
            try:
                lineas, nombre, etiqueta = lgpio.gpio_get_chip_info(manejo)
                print(f"gpiochip{numero}: {lineas} lineas, etiqueta {etiqueta!r}")
            except Exception as error:
                print(f"gpiochip{numero}: sin informacion ({error})")
            if _probar_reclamos(
                lgpio, manejo, config.pin_trigger, config.pin_echo
            ):
                manejo_bueno = manejo
                manejo = None  # se conserva para medir ECHO
        finally:
            if manejo is not None:
                try:
                    lgpio.gpiochip_close(manejo)
                except Exception:
                    pass

    _titulo("Estado de la linea ECHO")
    if manejo_bueno is None:
        print("Ningun chip dejo reclamar TRIG/ECHO: no hay GPIO util.")
    else:
        try:
            _muestrear_echo(
                lgpio, manejo_bueno, config.pin_trigger, config.pin_echo
            )
        finally:
            try:
                lgpio.gpiochip_close(manejo_bueno)
            except Exception:
                pass

    _titulo("Resumen")
    chips_info: list[tuple[int, int, str]] = []
    for numero in _numeros_de_chips():
        try:
            manejo = lgpio.gpiochip_open(numero)
        except Exception:
            continue
        try:
            datos = lgpio.gpio_get_chip_info(manejo)
            chips_info.append((numero, int(datos[0]), str(datos[2])))
        except Exception:
            continue
        finally:
            try:
                lgpio.gpiochip_close(manejo)
            except Exception:
                pass

    if not chips_info:
        print("El kernel no expone NINGUN chip GPIO (/dev/gpiochip*).")
    elif all(lineas == 0 for _, lineas, _ in chips_info):
        print("Los chips existen pero TODOS tienen 0 lineas: el kernel no")
        print("registro los controladores de la GPIO de la Raspberry Pi")
        print("(en la Pi 5 la cabecera es 'pinctrl-rp1' con 54 lineas).")
        print("Solucion: sudo apt update && sudo apt full-upgrade && reboot.")
        print("Si persiste, reinstala la imagen oficial de Raspberry Pi OS.")
    elif not any("pinctrl" in etiqueta.lower() for _, _, etiqueta in chips_info):
        print("Hay chips con lineas, pero NINGUNO se llama 'pinctrl': la")
        print("cabecera no esta expuesta por el kernel.")
        print("Solucion: sudo raspi-config > Interfases > GPIO y reinicia.")
    else:
        print("Chips GPIO correctos (hay un 'pinctrl-*').")
        print(f"Si aun asi no se pudo reclamar TRIG={config.pin_trigger}/")
        print(f"ECHO={config.pin_echo}, esos pines los tiene ocupado otro")
        print("programa o falta el divisor de voltaje en ECHO.")

    _titulo("Fin del diagnostico")


if __name__ == "__main__":
    main()
