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
# camina.  Anade aqui los tramos reales de tu casa.
#
# Basta con poner cada tramo en UN solo sentido (A -> B): el camino de
# vuelta (B -> A) se calcula solo, con los mismos pasos y los giros en
# sentido contrario.  Para el Cuarto pon tus medidas reales (pasos
# desde Sala y el giro inicial al salir de Sala).
TRAMOS_DEMO: dict[tuple[str, str], TramoRuta] = {
    ("Inicio", "Sala"): TramoRuta(pasos=10, giros=("derecha",)),
    ("Sala", "Baño"): TramoRuta(pasos=6, giros=("izquierda",)),
    # AJUSTAR a la casa real: pasos desde Sala hasta el Cuarto y el
    # giro de salida (derecha o izquierda).
    ("Sala", "Cuarto"): TramoRuta(pasos=8, giros=("derecha",)),
}

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


class PlanificadorRutas:
    """Calcula la ruta entre dos zonas del mapa."""

    def __init__(self, tramos: dict[tuple[str, str], TramoRuta] | None = None):
        self._tramos = self._ampliar(TRAMOS_DEMO if tramos is None else tramos)
        self._grafo = self._construir_grafo()

    @staticmethod
    def _ampliar(
        tramos: dict[tuple[str, str], TramoRuta],
    ) -> dict[tuple[str, str], TramoRuta]:
        """Añade el sentido inverso de cada tramo.

        Recorrer al reves un pasillo recto da los mismos pasos y los
        giros espejados (y en orden inverso si el tramo lleva varios).
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
