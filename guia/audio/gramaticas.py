"""Gramaticas de Vosk: limitan lo que el reconocedor intenta transcribir.

Gramaticas pequenas = mas rapido y mas preciso en la Raspberry Pi.
"""

from __future__ import annotations

import json

# Escucha permanente: palabras clave y ordenes dichas con "Raspi".
GRAMATICA_DESPERTADOR = json.dumps(
    [
        "raspi",
        "ras pi",
        "ras py",
        "raspi baño",
        "raspi bano",
        "raspi sala",
        "raspi cuarto",
        "raspi cocina",
        "raspi ve al baño",
        "raspi ve a la sala",
        "raspi ve al cuarto",
        "raspi ve a la cocina",
        "raspi para",
        "raspi alto",
        "raspi detente",
        "raspi reanuda",
        "raspi continuar",
        "raspi gira a la derecha",
        "raspi gira a la izquierda",
        "[unk]",
    ],
    ensure_ascii=False,
)

# El sistema pregunto "¿A donde quieres ir?".
GRAMATICA_DESTINO = json.dumps(
    [
        "baño",
        "bano",
        "sala",
        "cuarto",
        "cocina",
        "inicio",
        "quiero ir al baño",
        "quiero ir a la sala",
        "quiero ir al cuarto",
        "quiero ir a la cocina",
        "raspi baño",
        "raspi bano",
        "raspi sala",
        "raspi cuarto",
        "raspi cocina",
        "[unk]",
    ],
    ensure_ascii=False,
)

# Ventana corta de comandos tras decir "Raspi" sin orden.
GRAMATICA_COMANDO = json.dumps(
    [
        "baño",
        "bano",
        "sala",
        "cuarto",
        "cocina",
        "inicio",
        "quiero ir al baño",
        "quiero ir a la sala",
        "quiero ir al cuarto",
        "quiero ir a la cocina",
        "ve al baño",
        "ve a la sala",
        "ve al cuarto",
        "ve a la cocina",
        "para",
        "alto",
        "detente",
        "reanuda",
        "reanudar",
        "continua",
        "continuar",
        "gira a la derecha",
        "gira a la izquierda",
        "[unk]",
    ],
    ensure_ascii=False,
)
