"""Gramaticas de Vosk: limitan lo que el reconocedor intenta transcribir.

Gramaticas pequenas = mas rapido y mas preciso en la Raspberry Pi.
"""

from __future__ import annotations

import json

# Escucha permanente: palabras clave y ordenes dichas con "Raspi".
#
# Nota: la palabra "raspi" no existe en el vocabulario del modelo Vosk
# (Kaldi avisa "word missing in vocabulary" y la ignora), asi que se
# pide como "ras pi", que si esta en el vocabulario y se transcribe
# igual cuando la persona dice "Raspi".
GRAMATICA_DESPERTADOR = json.dumps(
    [
        "ras pi",
        "ras py",
        "ras pi baño",
        "ras pi bano",
        "ras pi sala",
        "ras pi cuarto",
        "ras pi cocina",
        "ras pi ve al baño",
        "ras pi ve a la sala",
        "ras pi ve al cuarto",
        "ras pi ve a la cocina",
        "ras pi para",
        "ras pi alto",
        "ras pi detente",
        "ras pi reanuda",
        "ras pi continuar",
        "ras pi gira a la derecha",
        "ras pi gira a la izquierda",
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
        "ras pi baño",
        "ras pi bano",
        "ras pi sala",
        "ras pi cuarto",
        "ras pi cocina",
        "[unk]",
    ],
    ensure_ascii=False,
)

# Alarma de caida sonando: solo la orden que la apaga.  Con la
# gramatica minima Vosk la reconoce mas rapido, incluso con el tono
# de 1200 Hz saliendo del altavoz mientras la persona habla.
GRAMATICA_SILENCIO = json.dumps(
    [
        "silencia",
        "silenciar",
        "apaga",
        "ya me recupere",
        "estoy bien",
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
