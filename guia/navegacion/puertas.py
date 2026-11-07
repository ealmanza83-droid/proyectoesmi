"""Seguimiento sencillo de puertas para una corrección auxiliar de avance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..configuracion import Configuracion


@dataclass
class _RastroPuerta:
    centro_x_px: float
    perdidas: int = 0
    contada: bool = False


class ContadorPuertas:
    """Cuenta una vez cada puerta que cruza el fragmento central."""

    def __init__(self, config: Configuracion):
        self._salto_maximo_px = config.salto_maximo_puerta_px
        self._perdidas_maximas = max(0, config.frames_puerta_perdida)
        self._rastros: list[_RastroPuerta] = []
        self._pasadas = 0

    @property
    def pasadas(self) -> int:
        return self._pasadas

    def reiniciar(self) -> None:
        self._rastros.clear()
        self._pasadas = 0

    def actualizar(self, puertas: Iterable[tuple[float, bool]]) -> int:
        """Asocia detecciones por posición horizontal y cuenta al centrar."""
        detecciones = tuple(puertas)
        usados: set[int] = set()

        for centro_x_px, centrada in detecciones:
            candidatos = [
                (abs(rastro.centro_x_px - centro_x_px), indice)
                for indice, rastro in enumerate(self._rastros)
                if indice not in usados
                and abs(rastro.centro_x_px - centro_x_px)
                <= self._salto_maximo_px
            ]
            if candidatos:
                _, indice = min(candidatos)
                rastro = self._rastros[indice]
                rastro.centro_x_px = centro_x_px
                rastro.perdidas = 0
            else:
                rastro = _RastroPuerta(centro_x_px)
                self._rastros.append(rastro)
                indice = len(self._rastros) - 1

            usados.add(indice)
            if centrada and not rastro.contada:
                rastro.contada = True
                self._pasadas += 1

        for indice, rastro in enumerate(self._rastros):
            if indice not in usados:
                rastro.perdidas += 1
        self._rastros = [
            rastro
            for rastro in self._rastros
            if rastro.perdidas <= self._perdidas_maximas
        ]
        return self._pasadas
