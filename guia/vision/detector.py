"""Deteccion visual con YOLO: senales orientadoras y obstaculos.

Una sola inferencia por frame devuelve todo lo util, en vez de
volver a recorrer los resultados en varias funciones.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np
from ultralytics import YOLO

from ..configuracion import Configuracion
from ..dominio.catalogo import (
    CLASES_PERMITIDAS,
    OBJETOS_PERMITIDOS,
    SENALES_ORIENTADORAS,
    Senal,
    senal_de_clase,
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
    senal: Senal | None = None
    """Senal confirmada (3 detecciones seguidas): zona y lado de puerta."""
    obstaculos: tuple[str, ...] = ()
    """Obstaculos que conviene anunciar ahora (respeta el enfriamiento)."""


class ConfirmadorSenal:
    """Exige varias detecciones seguidas, tolerando parpadeos.

    La camara y el modelo no aciertan en todos los frames: un frame
    sin senal solo *frena* la racha, y el contador se reinicia cuando
    se acumulan ``perdidas_senal_toleradas`` fallos seguidos.  Asi un
    parpadeo puntual no obliga a empezar la confirmacion desde cero
    (que era lo que hacia tardar demasiado en reconocer la senal).
    """

    def __init__(self, config: Configuracion):
        self._requeridas = config.detecciones_senal_requeridas
        self._toleradas = config.perdidas_senal_toleradas
        self._clase_actual: str | None = None
        self._racha = 0
        self._perdidas = 0

    def observar(self, senal_vista: str | None) -> Senal | None:
        """Suma la observacion del frame y devuelve la senal o ``None``."""
        if senal_vista is None:
            if self._clase_actual is None:
                return None
            self._perdidas += 1
            if self._perdidas > self._toleradas:
                self.reiniciar()
            return None

        if senal_vista == self._clase_actual:
            self._racha += 1
        else:
            self._clase_actual = senal_vista
            self._racha = 1
        self._perdidas = 0

        if self._racha >= self._requeridas:
            return senal_de_clase(senal_vista)
        return None

    def reiniciar(self) -> None:
        """Borra la racha (al cambiar de fase o tras perderla)."""
        self._clase_actual = None
        self._racha = 0
        self._perdidas = 0


class DetectorVisual:
    """Analiza frames y confirma senales sin falsos positivos."""

    def __init__(self, config: Configuracion):
        self._config = config
        self._senales = SENALES_ORIENTADORAS
        self._objetos = OBJETOS_PERMITIDOS
        self._confirmador = ConfirmadorSenal(config)
        self._ultimo_aviso: dict[str, float] = {}
        self._modelo = YOLO(str(config.ruta_modelo), task="detect")
        print("YOLO OK")
        self._precalentar()

    def _precalentar(self) -> None:
        """Carga NCNN y hace una inferencia de prueba al arrancar.

        Sin esto la *primera* llamada a ``analizar`` tarda varios
        segundos (carga del modelo NCNN + calentamiento) y bloquea el
        bucle principal justo cuando la persona esta girando.
        """
        try:
            frame_negro = np.zeros(
                (self._config.alto, self._config.ancho, 3), dtype=np.uint8
            )
            self._modelo(frame_negro, conf=self._config.confianza_minima, verbose=False)
            print("YOLO CALIENTE")
        except Exception as error:
            print("AVISO YOLO (precalentamiento):", error)

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
            senal=self._confirmador.observar(senal_vista),
            obstaculos=tuple(obstaculos),
        )

    def reiniciar_confirmacion(self) -> None:
        """Borra la racha de detecciones (al cambiar de fase)."""
        self._confirmador.reiniciar()


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
