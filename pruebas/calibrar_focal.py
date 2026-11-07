"""Calcula focal_px con una medida conocida de la señal impresa.

Coloca la señal perpendicular a la cámara, mide su distancia real y
ejecuta, por ejemplo::

    python pruebas/calibrar_focal.py --distancia-m 1.5 --altura-real-m 0.20 --alto-px 96

Copia el resultado en ``Configuracion.focal_px``. La altura real debe
coincidir con ``altura_senal_m``.
"""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distancia-m", type=float, required=True)
    parser.add_argument("--altura-real-m", type=float, required=True)
    parser.add_argument("--alto-px", type=float, required=True)
    args = parser.parse_args()
    if min(args.distancia_m, args.altura_real_m, args.alto_px) <= 0:
        parser.error("las medidas deben ser mayores que cero")

    focal_px = args.alto_px * args.distancia_m / args.altura_real_m
    print(f"focal_px = {focal_px:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
