"""Caracterizacion del MPU6050: offset y ruido del giroscopio y acelerometro.

Con los dos datos pedidos, medidos con el sensor EN REPOSO (quieto y
plano sobre una mesa):

  * offset: media de la lectura en reposo (lo ideal es 0 dps en el
    giroscopio y 0/0/1 g en el acelerometro; el offset es cuánto
    se desvia de ese valor).
  * desviacion estandar del ruido (sigma): cuanto fluctua esa misma
    lectura mientras no pasa nada (define el piso de ruido: el
    umbral de un paso o de un giro tiene que estar por encima).

La medida se hace a la tasa de la aplicacion (~85 muestras/seg por
I2C, bus 1, direccion 0x68), asi que los numeros sirven directamente
para justificar los umbrales de guia/configuracion.py.

Ejecutar en la Pi con la aplicacion principal CERRADA (los dos
programas no deben leer el I2C al mismo tiempo)::

    python pruebas/caracterizar_mpu6050.py            # 10 segundos
    python pruebas/caracterizar_mpu6050.py 30         # 30 segundos

No toques el equipo durante la medicion.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

# Ejecutable desde cualquier carpeta: el paquete 'guia' vive en la raiz.
RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from guia.configuracion import Configuracion  # noqa: E402
from guia.hardware.mpu6050 import MPU6050  # noqa: E402

EJES = ("ax", "ay", "az", "gx", "gy", "gz")


def main() -> int:
    segundos = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0

    print("=" * 58)
    print("CARACTERIZACION MPU6050: OFFSET Y RUIDO EN REPOSO")
    print("=" * 58)
    print("Coloca el equipo QUIETO y PLANO sobre una mesa.")
    print(f"Midiendo {segundos:.0f} s sin tocarlo (empieza en 3 s)...")
    time.sleep(3.0)

    config = Configuracion()
    mpu = MPU6050(config)
    muestras: dict[str, list[float]] = {eje: [] for eje in EJES}
    magnitudes: list[float] = []

    inicio = time.monotonic()
    while time.monotonic() - inicio < segundos:
        lectura = mpu.leer()
        for eje in EJES:
            muestras[eje].append(getattr(lectura, eje))
        magnitudes.append(lectura.magnitud_aceleracion)
        # ~85 muestras/seg, igual que el bucle de la aplicacion.
        time.sleep(0.012)
    duracion = time.monotonic() - inicio
    mpu.cerrar()

    n = len(muestras["ax"])
    if n < 2:
        print("Muy pocas muestras: revisa el I2C (i2cdetect -y 1).")
        return 1

    # Valor teorico en reposo plano: 0 g en X/Y, 1 g en Z; 0 dps.
    ideal = {"ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}

    print()
    print(f"Muestras: {n} en {duracion:.1f} s ({n / duracion:.1f} muestras/seg)")
    print()
    print(f"{'EJE':<4} {'MEDIA':>12} {'OFFSET':>12} {'RUIDO (sigma)':>14}")
    for eje in EJES:
        vals = muestras[eje]
        media = statistics.mean(vals)
        offset = media - ideal[eje]
        sigma = statistics.stdev(vals)
        unidad = "g" if eje.startswith("a") else "dps"
        decimales = 4 if eje.startswith("a") else 3
        print(
            f"{eje:<4} {media:>10.4f} {unidad:>2} "
            f"{offset:>+10.4f} {unidad:>2} {sigma:>12.4f} {unidad:>2}"
        )

    sigma_mag = statistics.stdev(magnitudes)
    umbral = config.delta_paso_g
    print()
    print("-" * 58)
    print("DATOS PARA EL REPORTE")
    print(f"  Acelerometro: offset = {statistics.mean(muestras['az']) - 1.0:+.4f} g"
          f" (eje Z plano) | ruido sigma = {statistics.stdev(muestras['az']):.4f} g")
    print(f"  Giroscopio:   offset = {statistics.mean(muestras['gz']):+.3f} dps"
          f" (eje Z) | ruido sigma = {statistics.stdev(muestras['gz']):.3f} dps")
    print("-" * 58)
    print("Umbrales de la aplicacion frente a este ruido:")
    print(f"  Magnitud en reposo (lo que ve el contador de pasos):"
          f" sigma = {sigma_mag:.4f} g")
    print(f"  delta_paso_g = {umbral} g -> equivale a"
          f" {umbral / max(sigma_mag, 1e-9):.0f} sigma (busca >= 4)")
    print(f"  dead-band de giros = {config.velocidad_reposo_dps} dps ->"
          f" equivale a"
          f" {config.velocidad_reposo_dps / max(statistics.stdev(muestras['gz']), 1e-9):.0f}"
          " sigma")
    print()
    print("Nota: el offset del acelerometro se tomo con el equipo plano;")
    print("si esta inclinado, ax/ay reflejan la inclinacion, no un offset.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
