"""Catalogo de senales orientadoras, obstaculos y destinos.

Estos diccionarios son el "vocabulario" del sistema: lo que la camara
puede ver y lo que la persona puede pedir por voz.

Las senales van impresas en las puertas, y cada puerta tiene DOS
caras: la cara interior (se ve estando *dentro* de la zona) y la del
pasillo (se ve desde el otro lado de la puerta).  Al distinguir ambas,
el sistema sabe no solo en que zona esta la persona, sino tambien de
que lado de la puerta se encuentra: solo la cara interior confirma
"estas en la zona" y solo desde ella arrancan las rutas medidas.
"""

from __future__ import annotations

from dataclasses import dataclass

# Lados de una puerta.
LADO_INTERIOR = "interior"
"""Cara visible desde dentro de la zona: confirma 'estas en la zona'."""

LADO_PASILLO = "pasillo"
"""Cara visible desde el otro lado: 'estas fuera, frente a la puerta'."""


@dataclass(frozen=True)
class Senal:
    """Imagen orientadora impresa en una cara de una puerta."""

    zona: str
    """Zona de la casa que identifica la imagen."""

    lado: str
    """Cara de la puerta desde la que se ve (interior o pasillo)."""


# Senales orientadoras: cada imagen (clase de YOLO) identifica una zona
# Y el lado de su puerta desde el que se ve.  Imprime la cara interior
# dentro de la zona y la del pasillo al otro lado de la puerta.
#
# El modelo en uso (yolo11n, COCO genérico) ya reconoce estos
# animales: no hace falta reentrenar nada, solo imprimir una foto por
# clase.  Las cuatro primeras (cow, zebra, giraffe, sheep) son las que
# ya estan impresas como interiores.
SENALES_ORIENTADORAS: dict[str, Senal] = {
    # Zona Inicio
    "cow": Senal("Inicio", LADO_INTERIOR),
    "elephant": Senal("Inicio", LADO_PASILLO),
    # Zona Sala
    "zebra": Senal("Sala", LADO_INTERIOR),
    "bear": Senal("Sala", LADO_PASILLO),
    # Zona Baño
    "giraffe": Senal("Baño", LADO_INTERIOR),
    "horse": Senal("Baño", LADO_PASILLO),
    # Zona Cuarto
    "sheep": Senal("Cuarto", LADO_INTERIOR),
    "dog": Senal("Cuarto", LADO_PASILLO),
}

# Obstaculos que conviene anunciar en voz alta para evitar golpes.
# Ojo: en COCO la mesa se llama "dining table" (antes el catalogo decia
# "table" y ese obstaculo nunca se anunciaba).
OBJETOS_PERMITIDOS: dict[str, str] = {
    "chair": "silla",
    "dining table": "mesa",
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


def senal_de_clase(clase: str) -> Senal | None:
    """Devuelve la senal (zona y lado) que representa una clase, o ``None``."""
    return SENALES_ORIENTADORAS.get(clase)


def objeto_de_clase(clase: str) -> str | None:
    """Devuelve el nombre en espanol de un obstaculo, o ``None``."""
    return OBJETOS_PERMITIDOS.get(clase)
