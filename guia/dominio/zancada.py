"""Zancada de la persona: los metros que avanza con cada paso.

La zancada no es un numero fijo: cambia con la persona, el calzado y
el ritmo, y con ella el numero de pasos que hay que dar en un tramo.
Se mide en cada tramo que la CAMARA cierra (distancia medida del mapa
/ pasos contados durante el tramo) y se guarda como una media movil
exponencial: la ultima medida pesa ``alfa`` y lo acumulado pesa el
resto, de modo que un tramo raro no descuadra el plan siguiente.

La zancada solo se actualiza con un tramo VERIFICADO por la camara:
un cierre por pasos de mas (130 % sin senal) no mide nada, porque la
distancia del mapa solo vale si el tramo acabo donde debia.
"""

from __future__ import annotations


class Zancada:
    """Media movil exponencial de los metros por paso."""

    def __init__(
        self,
        *,
        inicial: float,
        alfa: float,
        minimo: float,
        maximo: float,
    ) -> None:
        self._alfa = alfa
        self._minimo = minimo
        self._maximo = maximo
        self._valor = inicial
        self._aprendida = False

    @property
    def metros(self) -> float:
        """Zancada vigente: la que usa el planificador."""
        return self._valor

    @property
    def aprendida(self) -> bool:
        """Cierto desde el primer tramo cerrado por la camara."""
        return self._aprendida

    def medir(self, distancia_m: float, pasos: int) -> float | None:
        """Actualiza la zancada con un tramo medido.

        Devuelve la zancada vigente, o ``None`` si el tramo no sirve
        (sin distancia o sin pasos).  La medida se recorta al rango
        ``[minimo, maximo]`` antes de mezclarla: un tramo contado al
        reves (pasos de mas) no puede arrastrar la zancada a valores
        absurdos.
        """
        if distancia_m <= 0 or pasos <= 0:
            return None

        medida = distancia_m / pasos
        medida = min(max(medida, self._minimo), self._maximo)
        self._valor = self._alfa * medida + (1.0 - self._alfa) * self._valor
        self._valor = min(max(self._valor, self._minimo), self._maximo)
        self._aprendida = True
        return self._valor
