"""Reconocimiento de comandos a partir del texto transcrito por Vosk.

Separar este logico del microfono permite probarlo sin hardware y
evita que la lectura del audio se mezcle con la interpretacion.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum, auto

from .catalogo import DESTINOS_POR_PALABRA

# Palabras que despiertan al asistente.
PALABRAS_DESPERTADOR = ("raspi", "ras pi", "ras py")

_PATRON_PARAR = re.compile(r"\b(para|parar|alto|detente)\b")
_PATRON_REANUDAR = re.compile(r"\b(reanuda\w*|continua\w*)\b")
_PATRON_SILENCIAR = re.compile(
    r"\b(silencia\w*|apaga\w*|desactiva\w*|ya me recupere|estoy bien)\b"
)
_PATRON_DERECHA = re.compile(r"\b(gira|girar)\b.*\bderecha\b")
_PATRON_IZQUIERDA = re.compile(r"\b(gira|girar)\b.*\bizquierda\b")
# Recuperacion en medio de la ruta: volver a la zona anterior o
# recalcular hacia el mismo destino.
_PATRON_DEVOLVERME = re.compile(
    r"\b(devuelvete|devolverme|devuelve|regresa|regresar|vuelve|volver)\b"
)
_PATRON_OTRA_RUTA = re.compile(
    r"\b(otra ruta|otro camino|ruta distinta|cambiar de ruta|"
    r"replanifica\w*|recalcula\w*)\b"
)

# Puntuacion que puede acompañar a la palabra clave ("Raspi, ve a la sala").
_PUNTUACION = str.maketrans("", "", ".,;:!?¿¡\"'()")


class TipoComando(Enum):
    """Intencion detectada en lo que dijo la persona."""

    DESTINO = auto()
    PARAR = auto()
    REANUDAR = auto()
    SILENCIAR = auto()
    GIRAR_DERECHA = auto()
    GIRAR_IZQUIERDA = auto()
    DEVOLVERME = auto()
    OTRA_RUTA = auto()
    DESCONOCIDO = auto()


@dataclass(frozen=True)
class Comando:
    """Resultado de analizar una frase."""

    tipo: TipoComando
    valor: str | None = None


def normalizar(texto: str) -> str:
    """Minusculas y sin acentos: "Baño" -> "bano"."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def contiene_despertador(texto: str) -> bool:
    """Indica si la frase trae la palabra clave (ej. "Raspi")."""
    normal = normalizar(texto)
    return any(palabra in normal for palabra in PALABRAS_DESPERTADOR)


def quitar_despertador(texto: str) -> str:
    """Elimina la palabra clave y deja solo la orden."""
    normal = normalizar(texto)
    for palabra in PALABRAS_DESPERTADOR:
        normal = normal.replace(palabra, "")
    normal = normal.translate(_PUNTUACION)
    return " ".join(normal.split())


def extraer_destino(texto: str) -> str | None:
    """Busca un lugar conocido dentro de la frase."""
    normal = normalizar(texto)
    for palabra, lugar in DESTINOS_POR_PALABRA.items():
        if palabra in normal:
            return lugar
    return None


def reconocer(texto: str) -> Comando:
    """Convierte una frase en un :class:`Comando` accionable."""
    destino = extraer_destino(texto)
    if destino is not None:
        return Comando(TipoComando.DESTINO, destino)

    normal = normalizar(texto)

    if _PATRON_PARAR.search(normal):
        return Comando(TipoComando.PARAR)

    if _PATRON_REANUDAR.search(normal):
        return Comando(TipoComando.REANUDAR)

    if _PATRON_SILENCIAR.search(normal):
        return Comando(TipoComando.SILENCIAR)

    # "vuelve a la sala" es un destino (extraer_destino ya gano arriba):
    # aqui solo quedan las órdenes puras de devolucion/recalculo.
    if _PATRON_DEVOLVERME.search(normal):
        return Comando(TipoComando.DEVOLVERME)

    if _PATRON_OTRA_RUTA.search(normal):
        return Comando(TipoComando.OTRA_RUTA)

    if _PATRON_DERECHA.search(normal):
        return Comando(TipoComando.GIRAR_DERECHA)

    if _PATRON_IZQUIERDA.search(normal):
        return Comando(TipoComando.GIRAR_IZQUIERDA)

    return Comando(TipoComando.DESCONOCIDO)
