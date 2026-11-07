"""Filtrado de lecturas frontales para el odómetro opcional de pasillo."""

from __future__ import annotations

from collections import deque
from statistics import median

from ..configuracion import Configuracion


class OdometroUltrasonico:
    """Devuelve distancias estables en cm, filtrando ruido y saltos."""

    def __init__(
        self,
        config: Configuracion,
        *,
        muestras_requeridas: int | None = None,
        salto_maximo_cm: float | None = None,
    ):
        self._muestras_requeridas = max(
            1,
            config.muestras_ultrasonido_odometro
            if muestras_requeridas is None
            else muestras_requeridas,
        )
        self._salto_maximo_cm = (
            config.salto_maximo_ultrasonido_cm
            if salto_maximo_cm is None
            else salto_maximo_cm
        )
        self._muestras: deque[float] = deque(maxlen=self._muestras_requeridas)

    def observar(self, distancia_cm: float | None) -> float | None:
        """Entrega la mediana tras N medidas válidas y estables."""
        if distancia_cm is None or distancia_cm <= 0:
            self.reiniciar()
            return None

        if self._muestras:
            centro = float(median(self._muestras))
            if abs(distancia_cm - centro) > self._salto_maximo_cm:
                return None

        self._muestras.append(distancia_cm)
        if len(self._muestras) < self._muestras_requeridas:
            return None
        return float(median(self._muestras))

    def reiniciar(self) -> None:
        self._muestras.clear()
