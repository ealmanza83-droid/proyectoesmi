"""Estimador Kalman 1D de avance dentro del tramo actual."""

from __future__ import annotations

import math

from ..configuracion import Configuracion


class EstimadorPosicionTramo:
    """Fusiona predicciones por paso con mediciones de distancia."""

    def __init__(self, config: Configuracion):
        self._config = config
        self._distancia_m = 0.0
        self._posicion_m = 0.0
        self._varianza_m2 = config.sigma_inicial_posicion_m**2

    @property
    def posicion_m(self) -> float:
        return self._posicion_m

    @property
    def incertidumbre_m(self) -> float:
        return math.sqrt(max(0.0, self._varianza_m2))

    @property
    def varianza_m2(self) -> float:
        return self._varianza_m2

    def reiniciar(self, distancia_m: float) -> None:
        self._distancia_m = max(0.0, distancia_m)
        self._posicion_m = 0.0
        self._varianza_m2 = self._config.sigma_inicial_posicion_m**2

    def predecir(self, pasos: int, zancada_m: float) -> None:
        """Predice avance por pasos rectos y aumenta incertidumbre."""
        if pasos <= 0 or zancada_m <= 0:
            return
        self._posicion_m += pasos * zancada_m
        self._varianza_m2 += pasos * self._config.varianza_proceso_paso_m2
        self._limitar_posicion()

    def corregir(self, posicion_m: float, varianza_medicion_m2: float) -> bool:
        """Aplica una medición escalar; devuelve False si no es válida."""
        if (
            not math.isfinite(posicion_m)
            or not math.isfinite(varianza_medicion_m2)
            or varianza_medicion_m2 <= 0
        ):
            return False
        ganancia = self._varianza_m2 / (
            self._varianza_m2 + varianza_medicion_m2
        )
        self._posicion_m += ganancia * (posicion_m - self._posicion_m)
        self._varianza_m2 *= 1.0 - ganancia
        self._limitar_posicion()
        return True

    def _limitar_posicion(self) -> None:
        if self._distancia_m > 0:
            self._posicion_m = min(
                max(self._posicion_m, 0.0), self._distancia_m
            )
