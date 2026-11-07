"""Deteccion visual con YOLO: senales orientadoras y obstaculos.

Una sola inferencia por frame devuelve todo lo util, en vez de
volver a recorrer los resultados en varias funciones.
"""

from __future__ import annotations

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


# Posicion de un obstaculo respecto al camino de la persona (valor de
# ``ObstaculoVisto.lado``): o esta en medio del frame, donde puede
# cruzarse, o se queda a un lado.
EN_MEDIO_DEL_CAMINO = "centro"


@dataclass(frozen=True)
class ObstaculoVisto:
    """Un obstaculo por anunciar y donde esta respecto a la persona."""

    nombre: str
    """Nombre en espanol (ver catalogo.OBJETOS_PERMITIDOS)."""

    lado: str
    """``EN_MEDIO_DEL_CAMINO`` o el lado del frame en el que esta."""


@dataclass(frozen=True)
class ResultadoVision:
    """Lo que la camara descubrio en un frame."""

    detecciones: tuple[Deteccion, ...] = ()
    senal: Senal | None = None
    """Senal confirmada (3 detecciones seguidas): zona y lado de puerta."""
    obstaculos: tuple[ObstaculoVisto, ...] = ()
    """Obstaculos que se ven EN ESTE frame, con su lado.

    Sin enfriamiento: aqui solo se dice que hay.  Cuanto tarda en
    repetirse el anuncio lo decide la aplicacion (un aviso por
    encuentro), porque con el enfriamiento aqui el objeto
    "desaparecia" y "volvia" cada pocos segundos y el anuncio se
    repetia igual.
    """


def posicion_en_frame(
    caja: tuple[int, int, int, int],
    *,
    ancho: int,
    minimo: float,
    maximo: float,
) -> str:
    """Dice en cual de los 3 fragmentos de la imagen esta el objeto.

    La imagen se divide en tres fragmentos iguales (izquierda, centro,
    derecha): ``minimo`` y ``maximo`` marcan los limites del central
    (por defecto 1/3 y 2/3 del ancho).  La camara mira hacia adelante,
    asi que la posicion horizontal del objeto indica si se cruza en la
    ruta (centro) o si queda a la izquierda o a la derecha de la
    persona.
    """
    x1, _, x2, _ = caja
    centro = (x1 + x2) / 2 / ancho
    if centro < minimo:
        return "izquierda"
    if centro > maximo:
        return "derecha"
    return EN_MEDIO_DEL_CAMINO


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

        detecciones: list[Deteccion] = []
        obstaculos: list[ObstaculoVisto] = []
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
                # Sin enfriamiento: se anuncia lo que se VE ahora y
                # quien decide cuándo repetir es la aplicacion (ver
                # Aplicacion._anunciar_obstaculos).  Con el
                # enfriamiento aqui, el objeto parecia irse y volver
                # cada pocos segundos y la voz no paraba de repetirlo.
                obstaculos.append(
                    ObstaculoVisto(
                        nombre=objeto,
                        lado=posicion_en_frame(
                            (x1, y1, x2, y2),
                            ancho=self._config.ancho,
                            minimo=self._config.obstaculo_centro_min,
                            maximo=self._config.obstaculo_centro_max,
                        ),
                    )
                )

        return ResultadoVision(
            detecciones=tuple(detecciones),
            senal=self._confirmador.observar(senal_vista),
            obstaculos=tuple(obstaculos),
        )

    def reiniciar_confirmacion(self) -> None:
        """Borra la racha de detecciones (al cambiar de fase)."""
        self._confirmador.reiniciar()


def dibujar_detecciones(
    frame, resultado: ResultadoVision, *, centro_min: float, centro_max: float
) -> None:
    """Dibuja cajas, etiquetas y los limites del fragmento central.

    Las dos lineas verticales marcan donde empieza y termina el
    fragmento central (el tercio por donde se camina): sirven para
    calibrar ``obstaculo_centro_min/max`` en casa, viendo en la
    ventana si un objeto cae dentro o fuera de ese fragmento.
    """
    alto, ancho = frame.shape[:2]
    for fraccion in (centro_min, centro_max):
        x = int(round(fraccion * ancho))
        cv2.line(frame, (x, 0), (x, alto), (90, 90, 90), 1)
    cv2.putText(
        frame,
        "centro",
        (int(round(centro_min * ancho)) + 6, 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (90, 90, 90),
        1,
    )
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
