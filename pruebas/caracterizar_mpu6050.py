"""Caracterizacion del MPU6050: offset y ruido del giroscopio y acelerometro.

Con los dos datos pedidos, medidos con el sensor EN REPOSO (quieto y
plano sobre una mesa):

  * offset: media de la lectura en reposo (lo ideal es 0 dps en el
    giroscopio y 0/0/1 g en el acelerometro; el offset es cuanto se
    desvia de ese valor).
  * desviacion estandar del ruido (sigma): cuanto fluctua esa misma
    lectura mientras no pasa nada (el piso: un umbral tiene que estar
    por encima).

Uso (en la Pi, con la aplicacion principal CERRADA para no pelear por
el I2C)::

    python pruebas/caracterizar_mpu6050.py                  # 1 corrida de 10 s
    python pruebas/caracterizar_mpu6050.py 30               # 1 corrida de 30 s
    python pruebas/caracterizar_mpu6050.py --corridas       # 5 corridas de 10 s
    python pruebas/caracterizar_mpu6050.py 10 --corridas 5  # lo mismo, explicito
    python pruebas/caracterizar_mpu6050.py 10 --corridas 3  # 3 corridas

Con 3 o mas corridas, cada una se descarta automaticamente si el sigma
de algun eje supera 3 veces la mediana de las corridas (p.ej. la
primera, tomada mientras la mesa aun se asentaba).  El RESUMEN final
se calcula con las corridas validas y trae media, dispersion y sigma,
listo para copiar al reporte.

No toques el equipo durante las mediciones.
"""

from __future__ import annotations

import argparse
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
# Valor teorico en reposo plano: 0 g en X/Y, 1 g en Z; 0 dps.
IDEAL = {"ax": 0.0, "ay": 0.0, "az": 1.0, "gx": 0.0, "gy": 0.0, "gz": 0.0}


def medir(mpu: MPU6050, segundos: float):
    """Toma muestras durante ``segundos`` y devuelve las listas crudas."""
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
    return muestras, magnitudes, duracion


def estadisticas(muestras, magnitudes, duracion):
    """(offset, sigma) por eje + tasa y sigma de la magnitud."""
    n = len(muestras["ax"])
    por_eje = {}
    for eje in EJES:
        vals = muestras[eje]
        media = statistics.mean(vals)
        por_eje[eje] = (media, media - IDEAL[eje], statistics.stdev(vals))
    extra = {
        "n": n,
        "duracion": duracion,
        "hz": n / duracion if duracion > 0 else 0.0,
        "sigma_mag": statistics.stdev(magnitudes),
    }
    return por_eje, extra


def imprimir_tabla(por_eje, extra, titulo: str) -> None:
    print()
    print(titulo)
    print(
        f"Muestras: {extra['n']} en {extra['duracion']:.1f} s "
        f"({extra['hz']:.1f} muestras/seg)"
    )
    print()
    print(f"{'EJE':<4} {'MEDIA':>12} {'OFFSET':>12} {'RUIDO (sigma)':>14}")
    for eje in EJES:
        media, offset, sigma = por_eje[eje]
        unidad = "g" if eje.startswith("a") else "dps"
        print(
            f"{eje:<4} {media:>10.4f} {unidad:>2} "
            f"{offset:>+10.4f} {unidad:>2} {sigma:>12.4f} {unidad:>2}"
        )


def seleccionar(corridas):
    """Indices de corridas validas y motivo de descarte.

    Con menos de 3 corridas no hay con que comparar (la mediana seria
    la propia corrida), asi que todas valen.  El criterio: sigma de
    algun eje por encima de 3 veces la mediana de ese eje.
    """
    if len(corridas) < 3:
        return list(range(len(corridas))), []
    medianas = {
        eje: statistics.median(por_eje[eje][2] for por_eje, _ in corridas)
        for eje in EJES
    }
    validas: list[int] = []
    fuera: list[tuple[int, str]] = []
    for i, (por_eje, _) in enumerate(corridas):
        molesto = [
            eje
            for eje in EJES
            if medianas[eje] > 0 and por_eje[eje][2] > 3 * medianas[eje]
        ]
        if molesto:
            fuera.append((i + 1, ", ".join(molesto)))
        else:
            validas.append(i)
    if not validas:  # todo raro: mejor consolidar con todas
        validas = list(range(len(corridas)))
        fuera = []
    return validas, fuera


def consolidar(corridas, validas):
    """Media/dispersion de offsets y media de sigmas sobre las validas."""
    resumen = {}
    for eje in EJES:
        offsets = [corridas[i][0][eje][1] for i in validas]
        sigmas = [corridas[i][0][eje][2] for i in validas]
        resumen[eje] = (
            statistics.mean(offsets),
            min(offsets),
            max(offsets),
            statistics.mean(sigmas),
        )
    extras = {
        "hz": statistics.mean(corridas[i][1]["hz"] for i in validas),
        "sigma_mag": statistics.mean(corridas[i][1]["sigma_mag"] for i in validas),
    }
    return resumen, extras


def imprimir_reporte(datos, config: Configuracion) -> None:
    print("-" * 58)
    print("DATOS PARA EL REPORTE")
    linea_acc = (
        f"  Acelerometro: offset = {datos['az_offset']:+.4f} g (eje Z plano)"
        + (f"; entre {datos['az_min']:+.4f} y {datos['az_max']:+.4f}" if datos.get("rango") else "")
        + f" | ruido sigma = {datos['az_sigma']:.4f} g"
    )
    linea_giro = (
        f"  Giroscopio:   offset = {datos['gz_offset']:+.3f} dps (eje Z)"
        + (f"; entre {datos['gz_min']:+.3f} y {datos['gz_max']:+.3f}" if datos.get("rango") else "")
        + f" | ruido sigma = {datos['gz_sigma']:.3f} dps"
    )
    print(linea_acc)
    print(linea_giro)
    print("-" * 58)
    print(f"Medicion: {datos['muestra']}")
    print("Umbrales de la aplicacion frente a este ruido:")
    sigma_mag = max(datos["sigma_mag"], 1e-9)
    sigma_gz = max(datos["gz_sigma"], 1e-9)
    print(
        f"  Magnitud en reposo (lo que ve el contador de pasos):"
        f" sigma = {sigma_mag:.4f} g"
    )
    print(
        f"  delta_paso_g = {config.delta_paso_g} g -> equivale a"
        f" {config.delta_paso_g / sigma_mag:.0f} sigma (busca >= 4)"
    )
    print(
        f"  dead-band de giros = {config.velocidad_reposo_dps} dps ->"
        f" equivale a {config.velocidad_reposo_dps / sigma_gz:.0f} sigma"
    )
    print()
    print("Nota: el offset del acelerometro se tomo con el equipo plano;")
    print("si esta inclinado, ax/ay reflejan la inclinacion, no un offset.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offset y ruido del MPU6050 en reposo."
    )
    parser.add_argument(
        "segundos", nargs="?", type=float, default=10.0,
        help="segundos por corrida (por defecto 10)",
    )
    parser.add_argument(
        "--corridas", nargs="?", type=int, const=5, default=1,
        help="numero de corridas; sin valor = 5 (por defecto 1)",
    )
    args = parser.parse_args()

    print("=" * 58)
    print("CARACTERIZACION MPU6050: OFFSET Y RUIDO EN REPOSO")
    print("=" * 58)
    print("Coloca el equipo QUIETO y PLANO sobre una mesa y no lo toques.")

    config = Configuracion()
    mpu = MPU6050(config)

    corridas = []
    for k in range(1, args.corridas + 1):
        print(f"Corrida {k}/{args.corridas}: empieza en 3 s...")
        time.sleep(3.0)
        muestras, magnitudes, duracion = medir(mpu, args.segundos)
        por_eje, extra = estadisticas(muestras, magnitudes, duracion)
        corridas.append((por_eje, extra))
        imprimir_tabla(por_eje, extra, f"--- Corrida {k}/{args.corridas} ---")
    mpu.cerrar()

    validas, fuera = seleccionar(corridas)

    if len(corridas) == 1:
        por_eje, extra = corridas[0]
        datos = {
            "az_offset": por_eje["az"][1],
            "az_sigma": por_eje["az"][2],
            "gz_offset": por_eje["gz"][1],
            "gz_sigma": por_eje["gz"][2],
            "sigma_mag": extra["sigma_mag"],
            "muestra": (
                f"1 corrida de {args.segundos:.0f} s, "
                f"{extra['hz']:.1f} muestras/seg"
            ),
        }
        print()
        imprimir_reporte(datos, config)
        return 0

    # Varias corridas: resumen consolidado con las validas.
    resumen, extras = consolidar(corridas, validas)
    print()
    print("=" * 58)
    titulo = (
        f"RESUMEN: {len(corridas)} corridas de {args.segundos:.0f} s -> "
        f"{len(validas)} validas"
    )
    if fuera:
        detalles = "; ".join(f"corrida {n} (sigma en {ejes})" for n, ejes in fuera)
        titulo += f"\nDescartadas por sigma > 3x la mediana: {detalles}"
    print(titulo)
    print("=" * 58)
    print(f"{'EJE':<4} {'OFFSET (media)':>16} {'MIN .. MAX':>26} {'SIGMA (media)':>16}")
    for eje in EJES:
        media_off, mn, mx, media_sig = resumen[eje]
        unidad = "g" if eje.startswith("a") else "dps"
        dec = 4 if eje.startswith("a") else 3
        rango = f"{mn:+.{dec}f} .. {mx:+.{dec}f} {unidad}"
        print(
            f"{eje:<4} {media_off:>+12.{dec}f} {unidad:>3} "
            f"{rango:>26} {media_sig:>12.{dec}f} {unidad:>3}"
        )

    az = resumen["az"]
    gz = resumen["gz"]
    datos = {
        "az_offset": az[0], "az_min": az[1], "az_max": az[2], "az_sigma": az[3],
        "gz_offset": gz[0], "gz_min": gz[1], "gz_max": gz[2], "gz_sigma": gz[3],
        "sigma_mag": extras["sigma_mag"],
        "rango": True,
        "muestra": (
            f"media de {len(validas)} corridas validas de {len(corridas)} "
            f"({args.segundos:.0f} s c/u), {extras['hz']:.1f} muestras/seg"
        ),
    }
    print()
    imprimir_reporte(datos, config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
