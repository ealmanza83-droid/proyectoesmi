"""Catalogo de senales orientadoras, obstaculos y destinos.

Estos diccionarios son el "vocabulario" del sistema: lo que la camara
puede ver y lo que la persona puede pedir por voz.
"""

from __future__ import annotations

# Senales orientadoras: cada imagen (clase de YOLO) identifica una zona
# de la casa.  La persona invidente reconoce la zona por el audio.
SENALES_ORIENTADORAS: dict[str, str] = {
    "cow": "Inicio",
    "zebra": "Sala",
    "giraffe": "Baño",
    "sheep": "Cuarto",
}

# Obstaculos que conviene anunciar en voz alta para evitar golpes.
OBJETOS_PERMITIDOS: dict[str, str] = {
    "chair": "silla",
    "table": "mesa",
    "potted plant": "maceta",
}

# Clases que la camara tiene permiso para dibujar/anunciar.
CLASES_PERMITIDAS: frozenset[str] = (
    frozenset(SENALES_ORIENTADORAS) | frozenset(OBJETOS_PERMITIDOS)
)

# Lugares que la persona puede pedir por voz.
# La clave es la palabra ya normalizada (sin tildes, en minusculas).
DESTINOS_POR_PALABRA: dict[str, str] = {
    "inicio": "Inicio",
    "sala": "Sala",
    "bano": "Baño",
    "cuarto": "Cuarto",
    "cocina": "Cocina",
}


def lugar_de_senal(clase: str) -> str | None:
    """Devuelve la zona que representa una senal, o ``None``."""
    return SENALES_ORIENTADORAS.get(clase)


def objeto_de_clase(clase: str) -> str | None:
    """Devuelve el nombre en espanol de un obstaculo, o ``None``."""
    return OBJETOS_PERMITIDOS.get(clase)
