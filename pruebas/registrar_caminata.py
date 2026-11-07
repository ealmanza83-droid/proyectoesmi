"""Registra una caminata en CSV sin ejecutar la aplicacion principal.

Ejecutar con la guia cerrada en la Raspberry Pi::

    python pruebas/registrar_caminata.py --salida caminata.csv

Detener con Ctrl+C. El tiempo es monotono desde el inicio de la captura;
si el HC-SR04 no tiene eco, la columna de distancia queda vacia.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from guia.configuracion import Configuracion  # noqa: E402
from guia.hardware.mpu6050 import MPU6050  # noqa: E402
from guia.hardware.ultrasonido import SensorUltrasonico  # noqa: E402

COLUMNAS = ("t", "ax", "ay", "az", "gx", "gy", "gz", "distancia_ultrasonido")


def _argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--salida",
        type=Path,
        help="archivo CSV (por defecto, pruebas/caminata_<fecha>.csv)",
    )
    parser.add_argument(
        "--duracion-s",
        type=float,
        default=0.0,
        help="duracion de la captura; 0 continua hasta Ctrl+C",
    )
    parser.add_argument(
        "--sin-ultrasonido",
        action="store_true",
        help="registrar solo el MPU6050",
    )
    args = parser.parse_args()
    if args.duracion_s < 0:
        parser.error("--duracion-s no puede ser negativa")
    return args


def registrar(
    salida: Path,
    config: Configuracion,
    *,
    duracion_s: float = 0.0,
    usar_ultrasonido: bool = True,
) -> int:
    """Escribe muestras del MPU6050 y del HC-SR04 hasta parar o vencer."""
    salida.parent.mkdir(parents=True, exist_ok=True)
    sensor = SensorUltrasonico(config) if usar_ultrasonido else None
    inicio = time.monotonic()
    muestras = 0

    try:
        with MPU6050(config) as mpu, salida.open(
            "w", newline="", encoding="utf-8"
        ) as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)
            escritor.writeheader()
            archivo.flush()
            siguiente = inicio
            while not duracion_s or time.monotonic() - inicio < duracion_s:
                ahora = time.monotonic()
                lectura = mpu.leer()
                distancia = sensor.distancia_cm if sensor is not None else None
                escritor.writerow(
                    {
                        "t": f"{ahora - inicio:.6f}",
                        "ax": f"{lectura.ax:.6f}",
                        "ay": f"{lectura.ay:.6f}",
                        "az": f"{lectura.az:.6f}",
                        "gx": f"{lectura.gx:.6f}",
                        "gy": f"{lectura.gy:.6f}",
                        "gz": f"{lectura.gz:.6f}",
                        "distancia_ultrasonido": (
                            "" if distancia is None else f"{distancia:.1f}"
                        ),
                    }
                )
                archivo.flush()
                muestras += 1
                siguiente += config.intervalo_muestreo_mpu_s
                espera = siguiente - time.monotonic()
                if espera > 0:
                    time.sleep(espera)
                else:
                    siguiente = time.monotonic()
    except KeyboardInterrupt:
        print("\nCaptura detenida.")
    finally:
        if sensor is not None:
            sensor.cerrar()

    return muestras


def main() -> int:
    args = _argumentos()
    salida = args.salida or (
        RAIZ
        / "pruebas"
        / f"caminata_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )
    config = Configuracion()
    print("Caminata: empieza a caminar. Detén la captura con Ctrl+C.")
    muestras = registrar(
        salida,
        config,
        duracion_s=args.duracion_s,
        usar_ultrasonido=not args.sin_ultrasonido,
    )
    print(f"CSV guardado: {salida} ({muestras} muestras)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
