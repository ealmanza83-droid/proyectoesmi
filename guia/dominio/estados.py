"""Estados del asistente de orientacion.

El sistema es una maquina de estados: en cada instante solo se ejecuta
la logica del estado actual, lo que evita banderas globales dispersas.
"""

from __future__ import annotations

from enum import Enum, auto


class Estado(Enum):
    """Fases que recorre el dispositivo."""

    GIRO_INICIAL = auto()
    """Giro de 90 grados a la derecha para encarar la busqueda."""

    BUSCAR_SENAL = auto()
    """Busqueda de la senal orientadora durante un minuto."""

    MEDIA_VUELTA = auto()
    """Media vuelta de 180 grados si la senal no aparecio."""

    CINCO_PASOS = auto()
    """Avance de 5 pasos para reintentar la busqueda."""

    ESPERANDO_DESTINO = auto()
    """Escuchando hacia donde quiere ir la persona."""

    ENCARAR_SENAL = auto()
    """Poniendo la senal de la zona enfrente antes de arrancar la ruta."""

    NAVEGANDO = auto()
    """Siguiendo la ruta (giros y pasos) hasta el destino."""

    BUSQUEDA_ACTIVA = auto()
    """Escaneo rotacional buscando la senal del siguiente punto de referencia."""

    AVANCE_REUBICACION = auto()
    """Avance de 1 paso tras escaneo fallido para reubicarse."""

    CONFIRMANDO_LLEGADA = auto()
    """Esperando ver la senal interior del destino para anunciar la llegada."""

    DETENIDO = auto()
    """Salida limpia del programa."""

    @property
    def etiqueta(self) -> str:
        """Texto breve para la ventana de diagnostico."""
        return _ETIQUETAS[self]


_ETIQUETAS: dict[Estado, str] = {
    Estado.GIRO_INICIAL: "Giro 90 grados a la derecha",
    Estado.BUSCAR_SENAL: "Buscando senal orientadora",
    Estado.MEDIA_VUELTA: "Media vuelta (180 grados)",
    Estado.CINCO_PASOS: "Caminando 5 pasos",
    Estado.ESPERANDO_DESTINO: "Esperando destino",
    Estado.ENCARAR_SENAL: "Encarando la senal orientadora",
    Estado.NAVEGANDO: "Navegando hacia el destino",
    Estado.BUSQUEDA_ACTIVA: "Escaneo rotacional buscando senal",
    Estado.AVANCE_REUBICACION: "Avanzando 1 paso para reubicarse",
    Estado.CONFIRMANDO_LLEGADA: "Confirmando llegada con la senal",
    Estado.DETENIDO: "Sistema detenido",
}
