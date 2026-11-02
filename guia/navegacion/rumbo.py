"""Rumbo: orientacion relativa de la persona entre senales.

El MPU6050 no tiene magnetometro: no puede saber el norte.  Pero para
navegar basta con saber *hacia donde mira la persona respecto de la
ultima referencia conocida*, y la referencia la da la propia senal
orientadora: cuando la camara la confirma, la persona la tiene
enfrente.  En ese momento se ancla el rumbo en cero.

Entre anclajes, la rotacion se acumula con el giroscopio (misma
proyeccion sobre la vertical y mismo umbral de temblor que
:class:`MedidorGiro`, que le pasa cada giro ya depurado).  Asi el
sistema puede responder a "quiero ir al bano" aunque la persona ya se
haya girado: conoce su orientacion *relativa* y puede pedirle que
encare la senal antes de dar instrucciones.

El rumbo es el unico usuario sensible al balanceo de la cintura
(montaje en la cintura: al caminar el torso gira a cada paso), por eso
MedidorGiro solo le pasa el giro SOSTENIDO (ver tau_sostenida_giro_s):
el vaiven periodico y la deriva lenta del giroscopio no llegan a el.

La deriva del giroscopio hace que el rumbo pierda precision con los
minutos: por eso se vuelve a anclar en cada senal encontrada.
"""

from __future__ import annotations


class Rumbo:
    """Orientacion relativa en grados (positiva = giro a la derecha)."""

    def __init__(self):
        self._grados = 0.0

    def girar(self, grados: float) -> None:
        """Acumula una rotacion pequena (positiva = derecha)."""
        self._grados += grados

    def anclar(self) -> None:
        """Fija el rumbo a cero: la referencia esta enfrente."""
        self._grados = 0.0

    @property
    def grados(self) -> float:
        """Rotacion acumulada desde el ultimo anclaje (derecha +)."""
        return self._grados
