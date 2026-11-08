"""Deteccion visual con YOLO: senales orientadoras y obstaculos.

Una sola inferencia por frame devuelve todo lo util, en vez de
volver a recorrer los resultados en varias funciones.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median

import cv2
import numpy as np
from ultralytics import YOLO
from collections import deque
from statistics import median

from ..configuracion import Configuracion
from ..dominio.catalogo import (
    CLASES_PERMITIDAS,
    OBJETOS_PERMITIDOS,
    PUERTAS,
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
    distancia_m: float | None = None
    es_puerta: bool = False

    @property
    def alto_px(self) -> int:
        return max(0, self.caja[3] - self.caja[1])

    @property
    def ancho_px(self) -> int:
        return max(0, self.caja[2] - self.caja[0])

    @property
    def centro_x_px(self) -> float:
        return (self.caja[0] + self.caja[2]) / 2


class MedianaDistanciasVisuales:
    """Suaviza medidas por clase y descarta historiales ya obsoletos."""

    def __init__(self, frames: int, perdidas: int):
        self._frames = max(1, frames)
        self._perdidas_maximas = max(0, perdidas)
        self._historiales: dict[str, deque[float]] = {}
        self._perdidas: dict[str, int] = {}

    def suavizar(self, clase: str, distancia_m: float) -> float:
        historial = self._historiales.setdefault(
            clase, deque(maxlen=self._frames)
        )
        historial.append(distancia_m)
        self._perdidas[clase] = 0
        return float(median(historial))

    def cerrar_frame(self, clases_vistas: set[str]) -> None:
        for clase in tuple(self._historiales):
            if clase in clases_vistas:
                self._perdidas[clase] = 0
                continue
            perdidas = self._perdidas.get(clase, 0) + 1
            if perdidas > self._perdidas_maximas:
                del self._historiales[clase]
                self._perdidas.pop(clase, None)
            else:
                self._perdidas[clase] = perdidas


def distancia_por_altura(
    focal_px: float, altura_real_m: float, alto_px: int
) -> float | None:
    """Estima distancia de cámara a objeto con el modelo de cámara pinhole."""
    if focal_px <= 0 or altura_real_m <= 0 or alto_px <= 0:
        return None
    return focal_px * altura_real_m / alto_px


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
class PuertaVisto:
    """Una puerta detectada con geometría y distancia opcional."""

    nombre: str
    caja: tuple[int, int, int, int]
    centro_x_px: float
    alto_px: int
    ancho_px: int
    posicion: str
    distancia_m: float | None = None


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
    puertas: tuple[PuertaVisto, ...] = ()
    """Puertas detectadas en este frame (no identifican una zona)."""


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
    (por defecto 1/3 y 2/3 del ancho).

    La caja COMPLETA tiene que caber en el fragmento central para que
    el objeto este "en medio del camino": con mirar solo el centro de
    la caja, un objeto ancho que se salia de un lado (pero cuyo
    centro caia dentro) se contaba como central y frenaba sin motivo.
    Reglas:

    * cabe entera en el central -> centro;
    * se sale de UN solo lado -> ese lado;
    * se sale de los dos lados (objeto frontal muy cerca, que ocupa
      el fragmento central entero) -> centro, porque esta de frente
      en la direccion que se recorre;
    """
    x1, _, x2, _ = caja
    limite_izq = minimo * ancho
    limite_der = maximo * ancho
    sale_izquierda = x1 < limite_izq
    sale_derecha = x2 > limite_der
    if not sale_izquierda and not sale_derecha:
        return EN_MEDIO_DEL_CAMINO
    if not sale_derecha:
        return "izquierda"
    if not sale_izquierda:
        return "derecha"
    # Se sale de los dos lados: ocupa el fragmento central entero.
    centro = (x1 + x2) / 2 / ancho
    if centro < minimo:
        return "izquierda"
    if centro > maximo:
        return "derecha"
    return EN_MEDIO_DEL_CAMINO


def posicion_del_centro(
    centro_x_px: float, *, ancho: int, minimo: float, maximo: float
) -> str:
    """Clasifica el centro horizontal en izquierda, centro o derecha."""
    fraccion = centro_x_px / ancho
    if fraccion < minimo:
        return "izquierda"
    if fraccion > maximo:
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
        self._puertas = PUERTAS if config.usar_puertas else {}
        self._distancias = MedianaDistanciasVisuales(
            config.distancia_mediana_frames,
            config.distancia_mediana_perdidas,
        )
        self._confirmador = ConfirmadorSenal(config)
        self._modelo = YOLO(str(config.ruta_modelo), task="detect")
        print("YOLO OK")
        if config.focal_px <= 0.0:
            print(
                "AVISO VISION: focal_px no está calibrada; no se usarán "
                "distancias visuales de señales."
            )
        nombres = self._modelo.names
        clases_modelo = set(nombres.values() if isinstance(nombres, dict) else nombres)
        clases_puerta_ausentes = set(self._puertas) - clases_modelo
        if config.usar_puertas and clases_puerta_ausentes:
            print(
                "AVISO YOLO: el modelo no incluye la clase "
                f"{', '.join(sorted(clases_puerta_ausentes))}; "
                "no se podrán detectar puertas. Configura ruta_modelo "
                "con un modelo entrenado que incluya esa clase."
            )
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

        observaciones: list[
            tuple[str, float, tuple[int, int, int, int]]
        ] = []
        medidas_por_clase: dict[str, float] = {}
        for caja in resultados[0].boxes:
            clase_id = int(caja.cls[0])
            nombre = self._modelo.names[clase_id]
            if not self._config.usar_puertas and nombre in PUERTAS:
                continue
            if nombre not in CLASES_PERMITIDAS:
                continue
            x1, y1, x2, y2 = map(int, caja.xyxy[0])
            confianza = float(caja.conf[0])
            limites = (x1, y1, x2, y2)
            observaciones.append((nombre, confianza, limites))

            if nombre in self._senales:
                alto_px = y2 - y1
                medida = (
                    distancia_por_altura(
                        self._config.focal_px,
                        self._config.altura_senal_m,
                        alto_px,
                    )
                    if alto_px >= self._config.alto_minimo_senal_px
                    else None
                )
            elif nombre in self._puertas:
                medida = distancia_por_altura(
                    self._config.focal_px,
                    self._config.altura_puerta_m,
                    y2 - y1,
                )
                if medida is None:
                    medida = distancia_por_altura(
                        self._config.focal_px,
                        self._config.ancho_puerta_m,
                        x2 - x1,
                    )
            else:
                medida = None
            if medida is not None:
                # Un solo rango por clase y frame evita ponderar más las
                # clases que tengan varias cajas (se conserva la más cercana).
                medidas_por_clase[nombre] = min(
                    medidas_por_clase.get(nombre, medida), medida
                )

        distancias_frame = {
            clase: self._distancias.suavizar(clase, medida)
            for clase, medida in medidas_por_clase.items()
        }
        clases_vistas = set(distancias_frame)
        detecciones: list[Deteccion] = []
        obstaculos: list[ObstaculoVisto] = []
        puertas: list[PuertaVisto] = []
        candidatas_senal: list[
            tuple[float, str, tuple[int, int, int, int]]
        ] = []
        cajas_puertas: list[tuple[int, int, int, int]] = []

        for nombre, confianza, limites in observaciones:
            x1, y1, x2, y2 = limites
            es_senal = nombre in self._senales
            es_obstaculo = nombre in self._objetos
            es_puerta = nombre in self._puertas
            distancia_m = distancias_frame.get(nombre)

            detecciones.append(
                Deteccion(
                    nombre=nombre,
                    confianza=confianza,
                    caja=(x1, y1, x2, y2),
                    es_senal=es_senal,
                    es_obstaculo=es_obstaculo,
                    distancia_m=distancia_m,
                    es_puerta=es_puerta,
                )
            )

            if es_senal:
                candidatas_senal.append((confianza, nombre, (x1, y1, x2, y2)))
            elif es_puerta:
                caja = (x1, y1, x2, y2)
                cajas_puertas.append(caja)
                puertas.append(
                    PuertaVisto(
                        nombre=self._puertas[nombre],
                        caja=caja,
                        centro_x_px=(x1 + x2) / 2,
                        alto_px=max(0, y2 - y1),
                        ancho_px=max(0, x2 - x1),
                        posicion=posicion_del_centro(
                            (x1 + x2) / 2,
                            ancho=self._config.ancho,
                            minimo=self._config.obstaculo_centro_min,
                            maximo=self._config.obstaculo_centro_max,
                        ),
                        distancia_m=distancia_m,
                    )
                )
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

        self._distancias.cerrar_frame(clases_vistas)
        senal_vista: str | None = None
        if candidatas_senal:
            dentro_de_puerta = [
                candidata
                for candidata in candidatas_senal
                if any(
                    puerta[0] <= (candidata[2][0] + candidata[2][2]) / 2 <= puerta[2]
                    and puerta[1] <= (candidata[2][1] + candidata[2][3]) / 2 <= puerta[3]
                    for puerta in cajas_puertas
                )
            ]
            prioridad = dentro_de_puerta or candidatas_senal
            senal_vista = max(prioridad, key=lambda deteccion: deteccion[0])[1]

        return ResultadoVision(
            detecciones=tuple(detecciones),
            senal=self._confirmador.observar(senal_vista),
            obstaculos=tuple(obstaculos),
            puertas=tuple(puertas),
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
