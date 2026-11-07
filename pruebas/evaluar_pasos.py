"""Reproduce un CSV de caminata con ContadorPasos y calcula el error.

Ejecutar desde la raiz del proyecto::

    python pruebas/evaluar_pasos.py pruebas/caminata.csv --pasos-reales 42
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from guia.configuracion import Configuracion  # noqa: E402
from guia.hardware.mpu6050 import LecturaMPU  # noqa: E402
from guia.navegacion.movimiento import ContadorPasos  # noqa: E402

COLUMNAS_REQUERIDAS = ("t", "ax", "ay", "az", "gx", "gy", "gz")


@dataclass(frozen=True)
class ResultadoEvaluacion:
    pasos_detectados: int
    pasos_reales: int
    error_porcentual: float


def evaluar_csv(
    ruta: Path, pasos_reales: int, config: Configuracion | None = None
) -> ResultadoEvaluacion:
    """Reproduce aceleraciones de un CSV y devuelve el error porcentual."""
    if pasos_reales <= 0:
        raise ValueError("pasos_reales debe ser mayor que cero")

    config = config or Configuracion()
    contador = ContadorPasos(config)
    anterior: float | None = None
    with ruta.open(newline="", encoding="utf-8-sig") as archivo:
        lector = csv.DictReader(archivo)
        if lector.fieldnames is None:
            raise ValueError("el CSV está vacío o no tiene encabezado")
        faltantes = set(COLUMNAS_REQUERIDAS) - set(lector.fieldnames)
        if faltantes:
            raise ValueError(
                "faltan columnas requeridas: " + ", ".join(sorted(faltantes))
            )

        for numero_fila, fila in enumerate(lector, start=2):
            try:
                t = float(fila["t"])
                lectura = LecturaMPU(
                    ax=float(fila["ax"]),
                    ay=float(fila["ay"]),
                    az=float(fila["az"]),
                    gx=float(fila["gx"]),
                    gy=float(fila["gy"]),
                    gz=float(fila["gz"]),
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"muestra inválida en fila {numero_fila}") from error

            if anterior is not None and t <= anterior:
                raise ValueError(
                    f"el tiempo debe aumentar (fila {numero_fila}: {t})"
                )
            dt = (
                config.intervalo_muestreo_mpu_s
                if anterior is None
                else t - anterior
            )
            contador.en_muestra(lectura, dt)
            anterior = t

    detectados = contador.pasos
    error = abs(detectados - pasos_reales) / pasos_reales * 100.0
    return ResultadoEvaluacion(detectados, pasos_reales, error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV generado por registrar_caminata.py")
    parser.add_argument(
        "--pasos-reales",
        type=int,
        required=True,
        help="número de pasos contados manualmente durante la caminata",
    )
    args = parser.parse_args()
    try:
        resultado = evaluar_csv(args.csv, args.pasos_reales)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Pasos detectados: {resultado.pasos_detectados}")
    print(f"Pasos reales:     {resultado.pasos_reales}")
    print(f"Error porcentual: {resultado.error_porcentual:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
