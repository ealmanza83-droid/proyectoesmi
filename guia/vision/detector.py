"""Deteccion visual con YOLO: senales orientadoras y obstaculos.

Una sola inferencia por frame devuelve todo lo util, en vez de
volver a recorrer los resultados en varias funciones.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
from ultralytics import YOLO

from ..configuracion import Configuracion
from ..dominio.catalogo import (
    CLASES_PERMITIDAS,
    OBJETOS_PERMITIDOS,
    SENALES_ORIENTADORAS,
    lugar_de_senal,
)


@dataclass(frozen=True)
class Deteccion:
    """Un objeto visto por la camara en el frame actual."""

    nombre: str
    confianza: float
    caja: tuple[int, int, int, int]
    es_senal: bool
    es_obstaculo: bool


@dataclass(frozen=True)
class ResultadoVision:
    """Lo que la camara descubrio en un frame."""

    detecciones: tuple[Deteccion, ...] = ()
    senal: str | None = None
    """Zona confirmada (3 detecciones seguidas) o ``None``."""
    obstaculos: tuple[str, ...] = ()
    """Obstaculos que conviene anunciar ahora (respeta el enfriamiento)."""


class DetectorVisual:
    """Analiza frames y confirma senales sin falsos positivos."""

    def __init__(self, config: Configuracion):
        self._config = config
        self._senales = SENALES_ORIENTADORAS
        self._objetos = OBJETOS_PERMITIDOS
        self._clase_actual: str | None = None
        self._racha = 0
        self._ultimo_aviso: dict[str, float] = {}
        self._modelo = YOLO(str(config.ruta_modelo), task="detect")
        print("YOLO OK")

    # ------------------------------------------------------------------
    # Analisis
    # ------------------------------------------------------------------
    def analizar(self, frame) -> ResultadoVision:
        """Ejecuta YOLO una vez y resume lo interesante del frame."""
        resultados = self._modelo(
            frame, conf=self._config.confianza_minima, verbose=False
        )
        ahora = time.monotonic()

        detecciones: list[Deteccion] = []
        obstaculos: list[str] = []
        senal_vista: str | None = None

        for caja in resultados[0].boxes:
            clase_id = int(caja.cls[0])
            nombre = self._modelo.names[clase_id]
            confianza = float(caja.conf[0])

            if nombre not in CLASES_PERMITIDAS:
                continue

            x1, y1, x2, y2 = map(int, caja.xyxy[0])
            es_senal = nombre in self._senales
            es_obstaculo = nombre in self._objetos

            detecciones.append(
                Deteccion(
                    nombre=nombre,
                    confianza=confianza,
                    caja=(x1, y1, x2, y2),
                    es_senal=es_senal,
                    es_obstaculo=es_obstaculo,
                )
            )

            if es_senal:
                senal_vista = nombre
            elif es_obstaculo:
                objeto = self._objetos[nombre]
                # Enfriamiento: no repetir "alto, hay una silla" cada frame.
                if ahora - self._ultimo_aviso.get(objeto, 0.0) >= self._config.enfriamiento_objeto_s:
                    self._ultimo_aviso[objeto] = ahora
                    obstaculos.append(objeto)

        return ResultadoVision(
            detecciones=tuple(detecciones),
            senal=self._confirmar_senal(senal_vista),
            obstaculos=tuple(obstaculos),
        )

    def _confirmar_senal(self, senal_vista: str | None) -> str | None:
        """Pide varias detecciones seguidas para evitar parpadeos."""
        if senal_vista is None:
            self._clase_actual = None
            self._racha = 0
            return None

        if senal_vista == self._clase_actual:
            self._racha += 1
        else:
            self._clase_actual = senal_vista
            self._racha = 1

        if self._racha >= self._config.detecciones_senal_requeridas:
            return lugar_de_senal(senal_vista)
        return None

    def reiniciar_confirmacion(self) -> None:
        """Borra la racha de detecciones (al cambiar de fase)."""
        self._clase_actual = None
        self._racha = 0


def dibujar_detecciones(frame, resultado: ResultadoVision) -> None:
    """Dibuja cajas y etiquetas sobre el frame (solo para vision humana)."""
    for deteccion in resultado.detecciones:
        x1, y1, x2, y2 = deteccion.caja
        color = (0, 255, 0) if deteccion.es_senal else (0, 165, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            frame,
            f"{deteccion.nombre} {deteccion.confianza:.2f}",
            (x1, max(y1 - 10, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
