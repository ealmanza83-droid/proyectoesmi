"""Reproduce un CSV de caminata con ContadorPasos y calcula el error.

Ejecutar desde la raiz del proyecto::

    python pruebas/evaluar_pasos.py pruebas/caminata.csv --pasos-reales 42
"""

from __future__ import annotations

import argparse
import csv
import math
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
    distancia_real_m: float | None = None
    distancia_constante_m: float | None = None
    error_distancia_constante_pct: float | None = None
    distancia_por_pico_m: float | None = None
    error_distancia_por_pico_pct: float | None = None
    correlacion_pico_longitud: float | None = None


def evaluar_csv(
    ruta: Path,
    pasos_reales: int,
    config: Configuracion | None = None,
    distancia_real_m: float | None = None,
) -> ResultadoEvaluacion:
    """Reproduce aceleraciones de un CSV y devuelve el error porcentual."""
    if pasos_reales <= 0:
        raise ValueError("pasos_reales debe ser mayor que cero")
    if distancia_real_m is not None and (
        not math.isfinite(distancia_real_m) or distancia_real_m <= 0.0
    ):
        raise ValueError("distancia_real_m debe ser un número mayor que cero")

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
    if distancia_real_m is None:
        return ResultadoEvaluacion(detectados, pasos_reales, error)

    picos = contador.picos_por_paso
    longitudes: list[float] = []
    historial: list[float] = []
    for pico in picos:
        media = sum(historial) / len(historial) if historial else 0.0
        factor = (
            min(max((pico / media) ** 0.25, 0.75), 1.25)
            if media > 0.0
            else 1.0
        )
        longitudes.append(config.zancada_m * factor)
        historial = (historial + [pico])[-5:]

    distancia_constante = detectados * config.zancada_m
    distancia_pico = sum(longitudes)
    error_constante = (
        abs(distancia_constante - distancia_real_m) / distancia_real_m * 100.0
    )
    error_pico = abs(distancia_pico - distancia_real_m) / distancia_real_m * 100.0
    correlacion = _correlacion(picos, longitudes)
    return ResultadoEvaluacion(
        detectados,
        pasos_reales,
        error,
        distancia_real_m,
        distancia_constante,
        error_constante,
        distancia_pico,
        error_pico,
        correlacion,
    )


def _correlacion(xs: tuple[float, ...], ys: list[float]) -> float | None:
    """Correlación de Pearson; None si la muestra no permite calcularla."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    media_x = sum(xs) / len(xs)
    media_y = sum(ys) / len(ys)
    suma_xx = sum((x - media_x) ** 2 for x in xs)
    suma_yy = sum((y - media_y) ** 2 for y in ys)
    if suma_xx == 0.0 or suma_yy == 0.0:
        return None
    suma_xy = sum((x - media_x) * (y - media_y) for x, y in zip(xs, ys))
    return suma_xy / math.sqrt(suma_xx * suma_yy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="CSV generado por registrar_caminata.py")
    parser.add_argument(
        "--pasos-reales",
        type=int,
        required=True,
        help="número de pasos contados manualmente durante la caminata",
    )
    parser.add_argument(
        "--distancia-real-m",
        type=float,
        help="distancia medida del recorrido; compara zancada constante y por pico",
    )
    parser.add_argument(
        "--ojos-vendados",
        action="store_true",
        help="indica que el CSV se grabó caminando con los ojos vendados",
    )
    args = parser.parse_args()
    try:
        resultado = evaluar_csv(
            args.csv,
            args.pasos_reales,
            distancia_real_m=args.distancia_real_m,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Pasos detectados: {resultado.pasos_detectados}")
    print(f"Pasos reales:     {resultado.pasos_reales}")
    print(f"Error porcentual: {resultado.error_porcentual:.2f}%")
    if resultado.distancia_real_m is not None:
        print(f"Distancia real:             {resultado.distancia_real_m:.2f} m")
        print(
            "Zancada constante:          "
            f"{resultado.distancia_constante_m:.2f} m "
            f"(error {resultado.error_distancia_constante_pct:.2f}%)"
        )
        print(
            "Zancada por pico:           "
            f"{resultado.distancia_por_pico_m:.2f} m "
            f"(error {resultado.error_distancia_por_pico_pct:.2f}%)"
        )
        correlacion = resultado.correlacion_pico_longitud
        texto_correlacion = (
            "no disponible" if correlacion is None else f"{correlacion:.3f}"
        )
        print(f"Correlación pico/longitud:   {texto_correlacion}")
        if not args.ojos_vendados:
            print(
                "Recomendación: no activar aún; repetir esta evaluación "
                "con --ojos-vendados."
            )
        elif (
            resultado.error_distancia_por_pico_pct
            < resultado.error_distancia_constante_pct
        ):
            print(
                "Recomendación: el ajuste por pico reduce el error en este "
                "CSV vendado; validar en más recorridos antes de activarlo."
            )
        else:
            print(
                "Recomendación: mantener desactivada la zancada por pico; "
                "no reduce el error en este CSV vendado."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
