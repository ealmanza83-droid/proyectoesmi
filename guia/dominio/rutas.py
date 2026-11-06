"""Mapa de la casa y planificacion de rutas.

Cada tramo indica como ir de una zona a otra: cuantos giros y cuantos
pasos.  El :class:`PlanificadorRutas` convierte esos tramos en una
lista de :class:`Instruccion` que el sistema ejecuta paso a paso.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum, auto

# Cada giro del mapa es de 90 grados (cuatro orientaciones cardinales).
GRADOS_POR_GIRO = 90


@dataclass(frozen=True)
class TramoRuta:
    """Como se recorre un tramo del mapa."""

    pasos: int = 0
    giros: tuple[str, ...] = ()


class Accion(Enum):
    """Tipo de instruccion de navegacion."""

    GIRAR_DERECHA = auto()
    GIRAR_IZQUIERDA = auto()
    CAMINAR = auto()
    LLEGADA = auto()


@dataclass(frozen=True)
class Instruccion:
    """Instruccion individual: un giro, un tramo a pie o la llegada."""

    accion: Accion
    cantidad: int = 0  # grados en los giros, pasos al caminar

    @property
    def es_giro(self) -> bool:
        return self.accion in (Accion.GIRAR_DERECHA, Accion.GIRAR_IZQUIERDA)

    @property
    def es_pasos(self) -> bool:
        return self.accion is Accion.CAMINAR


# Orden dentro de un tramo: primero se gira para encarar y luego se
# camina.
#
# La casa es una cadena: Inicio -> Cuarto -> Sala -> Baño (cada zona
# colinda con la siguiente).  Las senales van impresas del lado
# DERECHO del pasillo, asi que caminando hacia adelante la persona
# gira a la IZQUIERDA para encarar la imagen de cada puerta; en el
# camino de vuelta las imagenes quedarian a su izquierda y el giro se
# invierte solo (derecha), como calcula _ampliar de abajo.
#
# Pasos calculados con la zancada real de la persona (0.46 m por paso,
# ver configuracion.py) a partir de las distancias medidas desde
# Inicio:
#   Inicio -> Cuarto   6.1 m               6.1 / 0.46 = 13 pasos
#   Cuarto -> Sala     10.5 - 6.1 = 4.4 m   4.4 / 0.46 = 10 pasos
#   Sala   -> Baño     30.2 - 10.5 = 19.7 m 19.7 / 0.46 = 43 pasos
#
# Basta con poner cada tramo en UN solo sentido (A -> B): el camino de
# vuelta (B -> A) se calcula solo, con los mismos pasos y los giros en
# sentido contrario.  Asi "Sala -> Baño" implica tambien "Baño ->
# Sala", y el planificador busca el camino mas corto aunque la ruta
# cruce varios tramos ("Inicio -> Cuarto -> Sala -> Baño").
#
# IMPORTANTE: cada tramo se midio de frente a la senal INTERIOR de la
# zona de partida (la cara de la su puerta que se ve estando dentro,
# ver catalogo.py).  Por eso la ruta no arranca hasta que la camara
# confirma esa cara (Aplicacion._en_encarar_senal): si la persona
# esta del lado del pasillo, los giros del plan saldrian dados al
# reves.
TRAMOS_DEMO: dict[tuple[str, str], TramoRuta] = {
    ("Inicio", "Cuarto"): TramoRuta(pasos=14, giros=("izquierda",)),
    ("Cuarto", "Sala"): TramoRuta(pasos=10, giros=("izquierda",)),
    ("Sala", "Baño"): TramoRuta(pasos=45, giros=("izquierda",)),
}
#TRAMOS_DEMO: dict[tuple[str, str], TramoRuta] = {
#    ("Inicio", "Cuarto"): TramoRuta(pasos=7, giros=("izquierda",)),
#    ("Cuarto", "Sala"): TramoRuta(pasos=7, giros=("izquierda",)),
#    ("Sala", "Baño"): TramoRuta(pasos=5, giros=("izquierda",)),
#}


_ACCIONES_POR_GIRO: dict[str, Accion] = {
    "derecha": Accion.GIRAR_DERECHA,
    "izquierda": Accion.GIRAR_IZQUIERDA,
}

# Recorrer un tramo al reves invierte el giro: lo que al llegar fue
# girar a la izquierda, de vuelta es girar a la derecha.
_GIRO_ESPEJO: dict[str, str] = {
    "derecha": "izquierda",
    "izquierda": "derecha",
}

# Giro con el que hay que encarar la senal de la puerta (ver
# PlanificadorRutas.lado_encarada).  Las imagenes van impresas del
# lado DERECHO del pasillo: recorriendo la casa en el sentido en que
# se midieron los tramos la persona gira a la IZQUIERDA para poner la
# imagen enfrente, y en el sentido inverso (por ejemplo ir del Bano a
# la Sala, donde la imagen le quedaria a su izquierda) la indicacion
# se invierte y hay que girar a la DERECHA.
GIRO_SENAL_NORMAL = "izquierda"
GIRO_SENAL_INVERSO = "derecha"


class PlanificadorRutas:
    """Calcula la ruta entre dos zonas del mapa."""

    def __init__(self, tramos: dict[tuple[str, str], TramoRuta] | None = None):
        base = TRAMOS_DEMO if tramos is None else tramos
        # Tramos tal como se midieron (un solo sentido): con ellos se
        # sabe por que lado quedan las imagenes al recorrer la ruta.
        self._directos = frozenset(base)
        self._tramos = self._ampliar(base)
        self._grafo = self._construir_grafo()

    @staticmethod
    def _ampliar(
        tramos: dict[tuple[str, str], TramoRuta],
    ) -> dict[tuple[str, str], TramoRuta]:
        """Añade el sentido inverso de cada tramo.

        Recorrer al reves un pasillo recto da los mismos pasos y los
        giros espejados (y en orden inverso si el tramo lleva varios).

        El espejo supone que el tramo directo TERMINA de frente a la
        senal interior del destino (asi se midio): por eso la llegada
        debe confirmarse con esa senal antes de dar por hecho el
        sentido de la ruta de vuelta.
        """
        completos = dict(tramos)
        for (origen, destino), tramo in tramos.items():
            for giro in tramo.giros:
                if giro not in _GIRO_ESPEJO:
                    raise ValueError(
                        f"Giro desconocido en la ruta {origen!r} -> "
                        f"{destino!r}: {giro!r}"
                    )
            completos.setdefault(
                (destino, origen),
                TramoRuta(
                    pasos=tramo.pasos,
                    giros=tuple(_GIRO_ESPEJO[g] for g in reversed(tramo.giros)),
                ),
            )
        return completos

    def _construir_grafo(self) -> dict[str, list[str]]:
        grafo: dict[str, list[str]] = {}
        for (origen, destino) in self._tramos:
            grafo.setdefault(origen, []).append(destino)
        return grafo

    def existe_ruta(self, origen: str | None, destino: str | None) -> bool:
        """Comprueba si el mapa conoce un camino entre dos zonas."""
        return self.planificar(origen, destino) is not None

    def camino(self, origen: str | None, destino: str | None) -> list[str] | None:
        """Zonas en orden que une la ruta, o ``None`` si no hay.

        La navegacion lo usa para seguir el avance: saber que zona le
        toca confirmar despues y detectar si la persona se devolvio.
        """
        if origen is None or destino is None:
            return None
        return self._camino_mas_corto(origen, destino)

    def lado_encarada(self, origen: str | None, destino: str | None) -> str:
        """Sentido en el que hay que girar para encarar la senal.

        El mapa se midio recorriendo la casa en un solo sentido, con
        las imagenes del lado derecho del pasillo.  Si la ruta sigue
        ese sentido (por ejemplo Inicio -> Cuarto) la imagen esta a la
        derecha y la encarada es a la IZQUIERDA; si la ruta va al
        reves (por ejemplo Bano -> Sala) la imagen le queda al otro
        lado y el giro se invierte (DERECHA).

        Mira solo el sentido del camino entre las dos zonas, asi que
        sirve igual para el tramo de salida y para confirmar la
        llegada a la puerta del destino.
        """
        camino = self.camino(origen, destino)
        if camino is None or len(camino) < 2:
            return GIRO_SENAL_NORMAL

        directos = 0
        inversos = 0
        for tramo in zip(camino, camino[1:]):
            if tramo in self._directos:
                directos += 1
            elif tramo[::-1] in self._directos:
                inversos += 1

        return GIRO_SENAL_INVERSO if inversos > directos else GIRO_SENAL_NORMAL

    def planificar(self, origen: str | None, destino: str | None) -> list[Instruccion] | None:
        """Devuelve las instrucciones para ir de ``origen`` a ``destino``.

        * ``None``     -> no hay ruta conocida (o faltan los datos).
        * ``[]``       -> la persona ya esta en el destino.
        * ``[...]``    -> instrucciones a ejecutar en orden.
        """
        if origen is None or destino is None:
            return None

        if origen == destino:
            return []

        camino = self._camino_mas_corto(origen, destino)
        if camino is None:
            return None

        instrucciones: list[Instruccion] = []
        for tramo_enlace in zip(camino, camino[1:]):
            tramo = self._tramos[tramo_enlace]
            for giro in tramo.giros:
                instrucciones.append(Instruccion(_ACCIONES_POR_GIRO[giro], GRADOS_POR_GIRO))
            if tramo.pasos > 0:
                instrucciones.append(Instruccion(Accion.CAMINAR, tramo.pasos))

        instrucciones.append(Instruccion(Accion.LLEGADA))
        return instrucciones

    def _camino_mas_corto(self, origen: str, destino: str) -> list[str] | None:
        """Busqueda en anchura (BFS) entre dos zonas."""
        visitados = {origen}
        cola: deque[list[str]] = deque([[origen]])

        while cola:
            camino = cola.popleft()
            ultimo = camino[-1]

            if ultimo == destino:
                return camino

            for vecino in self._grafo.get(ultimo, []):
                if vecino not in visitados:
                    visitados.add(vecino)
                    cola.append(camino + [vecino])

        return None
