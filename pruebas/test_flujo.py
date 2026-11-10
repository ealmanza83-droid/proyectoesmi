"""Prueba de humo del flujo completo, sin hardware real.

Ejecutar desde la raiz del proyecto::

    python pruebas/test_flujo.py

Se ejecutan los cuatro pasos clave del asistente:

1. Giro inicial de 90 grados a la derecha.
2. Deteccion de la senal orientadora.
3. Peticion de destino por voz.
4. Navegacion (giro + pasos) hasta la llegada.
"""

from __future__ import annotations

import dataclasses
import importlib
import math
import queue
import sys
import tempfile
import threading
import time
import types
import csv
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


# ----------------------------------------------------------------------
# Stubs: solo para las librerias que solo existen en la Raspberry Pi
# ----------------------------------------------------------------------
def _instalar_si_falta(nombre: str, **atributos) -> None:
    """Registra un stub solo si la libreria real no esta disponible.

    Ojo: el proyecto carpetas como ``vosk/`` que Python interpreta como
    paquete sin codigo; por eso ademas se comprueban los atributos.
    """
    try:
        modulo = importlib.import_module(nombre)
        if all(hasattr(modulo, atributo) for atributo in atributos):
            return
    except Exception:
        pass

    modulo = types.ModuleType(nombre)
    for clave, valor in atributos.items():
        setattr(modulo, clave, valor)
    sys.modules[nombre] = modulo

    # Enlazar con el paquete padre: "gpiozero.pins.lgpio" ...
    if "." in nombre:
        padre, hijo = nombre.rsplit(".", 1)
        setattr(sys.modules[padre], hijo, modulo)


class _CualquierCosa:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, item):
        return _CualquierCosa()

    def __call__(self, *args, **kwargs):
        return _CualquierCosa()


_instalar_si_falta(
    "cv2",
    FONT_HERSHEY_SIMPLEX=0,
    COLOR_YUV2BGR_YUYV=0,
    putText=lambda *a, **k: None,
    rectangle=lambda *a, **k: None,
    imshow=lambda *a, **k: None,
    waitKey=lambda *a, **k: 0,
    destroyAllWindows=lambda *a, **k: None,
    cvtColor=lambda frame, codigo: frame,
)
_instalar_si_falta("picamera2", Picamera2=_CualquierCosa)
_instalar_si_falta("smbus2", SMBus=_CualquierCosa)
_instalar_si_falta("gpiozero", DistanceSensor=_CualquierCosa)
_instalar_si_falta("gpiozero.pins")
_instalar_si_falta("gpiozero.pins.lgpio", LGPIOFactory=_CualquierCosa)
_instalar_si_falta("ultralytics", YOLO=_CualquierCosa, YOLOWorld=_CualquierCosa)
_instalar_si_falta("piper", PiperVoice=_CualquierCosa)
_instalar_si_falta(
    "sounddevice",
    query_devices=lambda *a, **k: [],
    default=types.SimpleNamespace(device=[0]),
    RawInputStream=_CualquierCosa,
)
_instalar_si_falta(
    "vosk",
    KaldiRecognizer=_CualquierCosa,
    Model=_CualquierCosa,
    SetLogLevel=lambda *a, **k: None,
)

# ----------------------------------------------------------------------
# Modulos reales del sistema
# ----------------------------------------------------------------------
from guia.aplicacion import Aplicacion  # noqa: E402
from guia.audio.microfono import EventoVoz, ModoMicrofono  # noqa: E402
from guia.configuracion import Configuracion  # noqa: E402
from guia.dominio.comandos import TipoComando, extraer_destino, quitar_despertador, reconocer  # noqa: E402
from guia.dominio.catalogo import (  # noqa: E402
    LADO_INTERIOR,
    LADO_PASILLO,
    OBJETOS_PERMITIDOS,
    PUERTAS,
    SENALES_ORIENTADORAS,
    Senal,
)
from guia.dominio.estados import Estado  # noqa: E402
from guia.dominio.rutas import (  # noqa: E402
    Accion,
    GIRO_SENAL_INVERSO,
    GIRO_SENAL_NORMAL,
    Instruccion,
    PlanificadorRutas,
    TRAMOS_DEMO,
    TramoRuta,
)
from guia.dominio.zancada import Zancada  # noqa: E402
from pruebas.evaluar_pasos import evaluar_csv  # noqa: E402
from guia.hardware.mpu6050 import LecturaMPU  # noqa: E402
from guia.hardware import camara as camara_mod  # noqa: E402
from guia.hardware.ultrasonido import (  # noqa: E402
    SensorUltrasonico,
    intervalo_de_beep,
)
from guia.navegacion import movimiento as movimiento  # noqa: E402
from guia.navegacion.caida import DetectorCaida  # noqa: E402
from guia.navegacion.odometro import OdometroUltrasonico  # noqa: E402
from guia.navegacion.posicion import EstimadorPosicionTramo  # noqa: E402
from guia.navegacion.puertas import ContadorPuertas  # noqa: E402
from guia.navegacion.rumbo import Rumbo  # noqa: E402
from guia.vision.detector import (  # noqa: E402
    Deteccion,
    EN_MEDIO_DEL_CAMINO,
    ConfirmadorSenal,
    DetectorVisual,
    MedianaDistanciasVisuales,
    ObstaculoVisto,
    PuertaVisto,
    ResultadoVision,
    distancia_por_altura,
    posicion_en_frame,
)


# ----------------------------------------------------------------------
# Dobles de prueba (fakes)
# ----------------------------------------------------------------------
class RelojFalso:
    """Avanza 0.1 s por consulta: no hace falta esperar en la prueba."""

    def __init__(self) -> None:
        self.ahora = 1000.0

    def monotonic(self) -> float:
        self.ahora += 0.1
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        """Salta el reloj (para enfriamientos de varios segundos)."""
        self.ahora += segundos


class CamaraFalsa:
    def capturar(self):
        return object()

    def cerrar(self) -> None:
        pass


class MPUFalso:
    def __init__(self) -> None:
        self.gz = 0.0  # velocidad angular (grados/segundo)
        self.az = 1.0  # aceleracion vertical (1.0 g en reposo)

    def leer(self) -> LecturaMPU:
        return LecturaMPU(0.0, 0.0, self.az, 0.0, 0.0, self.gz)

    def cerrar(self) -> None:
        pass


class SensorFalso:
    def __init__(self, distancia_cm: float | None = 150.0) -> None:
        # Por defecto en rango de alerta (distancia_alerta_cm): los
        # avisos de obstaculo se prueban con el eco presente.
        self.distancia_cm = distancia_cm
        self.distancia_alerta_cm = Configuracion().distancia_alerta_cm
        self.secuencia_medicion = 0


class AlarmaFalsa:
    def __init__(self) -> None:
        self.alarma_activa = False

    def reproducir(self) -> None:
        pass

    def activar_alarma(self, duracion_s: float | None = None) -> None:
        self.alarma_activa = True

    def silenciar_alarma(self, espera_s: float = 2.0) -> None:
        self.alarma_activa = False

    def esperar(self, espera_s: float = 2.0) -> None:
        self.alarma_activa = False


class LocutorFalso:
    def __init__(self) -> None:
        self.hablando = threading.Event()
        self.frases: list[str] = []

    def hablar(self, texto: str) -> bool:
        self.frases.append(texto)
        print("   VOZ:", texto)
        return True

    def limpiar_cache(self) -> None:
        pass


class MicrofonoFalso:
    def __init__(self) -> None:
        self.eventos: queue.Queue[EventoVoz] = queue.Queue()
        self.modo = ModoMicrofono.APAGADO

    def cambiar_modo(self, modo: ModoMicrofono) -> None:
        self.modo = modo

    def iniciar(self) -> None:
        pass

    def detener(self, espera_s: float = 2.0) -> None:
        pass


class DetectorFalso:
    def __init__(self) -> None:
        self.senal: Senal | None = None
        self.obstaculos: tuple[ObstaculoVisto, ...] = ()
        self.puertas: tuple[PuertaVisto, ...] = ()

    def analizar(self, frame) -> ResultadoVision:
        detecciones = ()
        if self.senal is not None:
            clase = next(
                nombre
                for nombre, senal in SENALES_ORIENTADORAS.items()
                if senal == self.senal
            )
            detecciones = (
                Deteccion(
                    clase,
                    0.9,
                    (250, 100, 390, 300),
                    True,
                    False,
                ),
            )
        return ResultadoVision(
            detecciones=detecciones,
            senal=self.senal,
            obstaculos=self.obstaculos,
            puertas=self.puertas,
        )

    def reiniciar_confirmacion(self) -> None:
        pass


class VentanaFalsa:
    activa = True

    def mostrar(self, frame, **kwargs) -> int:
        return 0

    def cerrar(self) -> None:
        pass


# ----------------------------------------------------------------------
# Utilidades del ensayo
# ----------------------------------------------------------------------
def paso(
    aplicacion: Aplicacion, detector: DetectorFalso, mpu: MPUFalso, dt: float = 0.1
) -> None:
    """Una iteracion del bucle principal (sin ventana de OpenCV).

    Como en el sistema real, primero se muestrea el MPU6050, luego se
    atiende voz y caidas, despues decide el estado actual y AL FINAL
    los obstaculos (que esperan a que la guia hable, ver ``anunciar``).
    """
    frame = aplicacion.camara.capturar()
    resultado = detector.analizar(frame)
    aplicacion.muestreador.muestrear(mpu.leer(), dt)
    if hasattr(aplicacion.sensor, "secuencia_medicion"):
        aplicacion.sensor.secuencia_medicion += 1
    aplicacion._actualizar_distancia_puerta(aplicacion.sensor.distancia_cm)
    aplicacion._procesar_voz()
    aplicacion._vigilar_caida()
    aplicacion._manejadores[aplicacion.estado](aplicacion, resultado)
    aplicacion._anunciar_obstaculos(resultado, aplicacion.sensor.distancia_cm)


def anunciar(
    aplicacion: Aplicacion, detector: DetectorFalso, distancia: float | None
) -> None:
    """Una vuelta de ``_anunciar_obstaculos`` con la voz ya callada.

    La navegacion manda: hasta ``silencio_para_obstaculos_s`` despues
    de la ultima frase no se anuncia un obstaculo.  Aqui se adelanta
    ese silencio para poder probar el anuncio en si; la prioridad de
    la voz se prueba aparte, en [24].
    """
    aplicacion._ultima_voz_en = (
        time.monotonic() - aplicacion.config.silencio_para_obstaculos_s - 1.0
    )
    aplicacion._anunciar_obstaculos(detector.analizar(None), distancia)


def comprobar(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)
    print(f"  OK  {mensaje}")


def preparar_distancia_puerta(
    aplicacion: Aplicacion, distancia_cm: float
) -> None:
    """Prepara lecturas ultrasónicas estables en las pruebas."""
    aplicacion._medidor_distancia_puerta.reiniciar()
    aplicacion._distancia_puerta_estable_cm = None
    aplicacion.sensor.distancia_cm = distancia_cm
    for _ in range(aplicacion.config.muestras_ultrasonido_puerta):
        aplicacion.sensor.secuencia_medicion += 1
        aplicacion._actualizar_distancia_puerta(distancia_cm)


# ----------------------------------------------------------------------
# Pruebas
# ----------------------------------------------------------------------
def test_fps_camara() -> None:
    """La captura Picamera2 se limita al FPS configurado."""
    print("\n[0] Cámara: límite de 15 FPS")

    class PicameraFalsa:
        def __init__(self, controles_disponibles) -> None:
            self.camera_controls = controles_disponibles
            self.configuracion = None

        def create_video_configuration(self, **kwargs):
            return kwargs

        def configure(self, configuracion) -> None:
            self.configuracion = configuracion

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    picamera_original = camara_mod.Picamera2
    try:
        falsa = PicameraFalsa({"FrameDurationLimits": None})
        camara_mod.Picamera2 = lambda: falsa
        camara_mod.Camara(Configuracion(), espera_arranque_s=0)

        sin_limite_fps = PicameraFalsa({})
        camara_mod.Picamera2 = lambda: sin_limite_fps
        camara_mod.Camara(Configuracion(), espera_arranque_s=0)
    finally:
        camara_mod.Picamera2 = picamera_original

    comprobar(
        falsa.configuracion["controls"]["FrameDurationLimits"]
        == (66_667, 66_667),
        "aplica el límite de 15 FPS cuando la cámara lo anuncia",
    )
    comprobar(
        sin_limite_fps.configuracion["controls"] == {},
        "configura cámaras que no anuncian FrameDurationLimits",
    )
    comprobar(
        camara_mod.limites_duracion_fotograma(30.0) == (33_333, 33_333),
        "convierte correctamente otro FPS configurado",
    )
    fallo = False
    try:
        camara_mod.limites_duracion_fotograma(0.0)
    except ValueError:
        fallo = True
    comprobar(fallo, "rechaza una frecuencia de cámara inválida")


def test_comandos_de_voz() -> None:
    print("\n[1] Comandos de voz")
    comprobar(extraer_destino("quiero ir al baño") == "Baño", "destino 'baño'")
    comprobar(reconocer("raspi para").tipo is TipoComando.PARAR, "orden 'para'")
    comprobar(
        reconocer("gira a la izquierda").tipo is TipoComando.GIRAR_IZQUIERDA,
        "orden 'gira a la izquierda'",
    )
    comprobar(quitar_despertador("Raspi, ve a la sala") == "ve a la sala", "quita la palabra clave")
    comprobar(
        reconocer("silencia").tipo is TipoComando.SILENCIAR,
        "orden 'silencia' (apaga la alarma de caida)",
    )

    # La version (commit) en ejecucion sale por consola al arrancar
    # (guia/version.py) para no probar sin querer una copia vieja.
    from guia.version import obtener_version

    comprobar(obtener_version() != "", "version del codigo (commit) disponible")


def test_planificacion_de_rutas() -> None:
    print("\n[2] Planificacion de rutas")
    planificador = PlanificadorRutas()

    # La casa es una cadena (Inicio -> Cuarto -> Sala -> Baño): ir a
    # la Sala cruza dos tramos y cada uno aporta su giro y sus pasos.
    simple = planificador.planificar("Inicio", "Sala")
    comprobar(
        simple is not None and len(simple) == 5,
        "Inicio -> Sala: 5 instrucciones (dos tramos)",
    )
    comprobar(simple[0].accion is Accion.GIRAR_IZQUIERDA, "primero gira a la izquierda")
    comprobar(
        simple[1].accion is Accion.CAMINAR
        and simple[1].cantidad == TRAMOS_DEMO[("Inicio", "Cuarto")].pasos,
        "luego los pasos del tramo Inicio -> Cuarto",
    )
    comprobar(
        simple[2].accion is Accion.GIRAR_IZQUIERDA,
        "nuevo giro izquierda al salir del Cuarto",
    )
    comprobar(
        simple[3].accion is Accion.CAMINAR
        and simple[3].cantidad == TRAMOS_DEMO[("Cuarto", "Sala")].pasos,
        "luego los pasos del tramo Cuarto -> Sala",
    )
    comprobar(simple[4].accion is Accion.LLEGADA, "cierra con la llegada")

    larga = planificador.planificar("Inicio", "Baño")
    comprobar(larga is not None and len(larga) == 7, "Inicio -> Baño cruza tres tramos")

    # Cada tramo se puede recorrer al reves: mismos pasos y giros
    # espejados (antes todas las rutas de vuelta decian 'no configurada').
    # De vuelta las imagenes quedan al otro lado del pasillo: el giro
    # se invierte solo (izquierda -> derecha).
    vuelta = planificador.planificar("Baño", "Sala")
    comprobar(
        vuelta is not None
        and vuelta[0].accion is Accion.GIRAR_DERECHA
        and vuelta[1].cantidad == TRAMOS_DEMO[("Sala", "Baño")].pasos,
        "Baño -> Sala: espejo del tramo (derecha, mismos pasos)",
    )
    comprobar(
        planificador.planificar("Baño", "Inicio") is not None,
        "Baño -> Inicio: ruta de tres tramos calculada",
    )
    comprobar(
        planificador.planificar("Sala", "Cuarto") is not None
        and planificador.planificar("Cuarto", "Sala") is not None,
        "hay ruta a Cuarto en ambos sentidos",
    )

    comprobar(planificador.planificar("Sala", "Cocina") is None, "sin ruta -> None")
    comprobar(planificador.planificar(None, "Sala") is None, "ubicacion desconocida -> None")


def test_flujo_completo() -> None:
    print("\n[3] Flujo completo: giro, senal, destino y navegacion")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo

    detector = DetectorFalso()
    mpu = MPUFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()

    # Sin gracia de arranque: en el ensayo todo ocurre en menos de un
    # segundo y la prueba debe poder detectar la caida de inmediato.
    config = dataclasses.replace(Configuracion(), gracia_caida_s=0.0)

    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # --- 3.1 Giro inicial de 90 grados -----------------------------
    # Con el sensor plano (az=+1), girar a la derecha deja gz negativo
    # (regla de la mano derecha); la velocidad de giro se proyecta y
    # el signo lo convierte en positivo hacia la derecha.
    microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)  # lo hace ejecutar()
    aplicacion.giro.reiniciar()
    mpu.gz = -100.0  # giro a derecha sostenido
    for _ in range(15):
        paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.BUSCAR_SENAL, "giro de 90 grados completado")
    comprobar(
        "Puedes parar. Ya giraste 90 grados." in locutor.frases,
        "avisa cuando parar el giro de 90",
    )
    comprobar(
        not any("Vas por la mitad" in frase for frase in locutor.frases),
        "no habla a mitad del giro (la frase desfasaba el giro)",
    )
    comprobar(
        not any("Ya casi listo" in frase for frase in locutor.frases),
        "no avisa ya casi listo mientras la persona gira",
    )
    comprobar(
        microfono.modo is ModoMicrofono.DESPERTADOR,
        "el microfono vuelve a escuchar el despertador",
    )

    # --- 3.2 Aparece la senal orientadora --------------------------
    detector.senal = Senal("Sala", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "senal detectada")
    comprobar(aplicacion.ubicacion == "Sala", "ubicacion actualizada a Sala")
    comprobar(microfono.modo is ModoMicrofono.DESTINO, "pide el destino por voz")

    # --- 3.3 La persona pide un destino ----------------------------
    microfono.eventos.put(
        EventoVoz(ModoMicrofono.DESTINO, "quiero ir al baño")
    )
    # La senal de Sala sigue a la vista: el rumbo se ancla y arranca
    # sin pedir giros extra.
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.NAVEGANDO, "ruta hacia Baño iniciada")
    comprobar(aplicacion.rumbo.grados == 0.0, "rumbo anclado en la senal")
    comprobar(aplicacion.destino == "Baño", "destino registrado")
    comprobar(microfono.modo is ModoMicrofono.DESPERTADOR, "sigue escuchando ordenes")
    detector.senal = None  # la persona giro y dejo la senal atras

    # --- 3.4 Giro de la ruta (izquierda 90 grados) -----------------
    mpu.gz = 100.0  # a la izquierda, gz del lado opuesto
    for _ in range(15):
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._instruccion_actual is not None
        and aplicacion._instruccion_actual.es_pasos,
        "giro de la ruta completado, sigue caminar",
    )

    # --- 3.5 Camina los pasos del tramo ----------------------------
    # Al caminar la magnitud *oscila* (pico, reposo, pico...) y el
    # contador solo ve lo que queda DESPUES del pasa-banda 0,5 - 5 Hz:
    # la oscilacion tiene que estar dentro de banda.  Con dt = 0,1 s
    # (10 Hz) un ciclo de 4 muestras es 2,5 Hz, y cada ciclo vale un
    # paso.  El tramo Sala -> Baño son 31 pasos del mapa y el corte
    # sin senal esta en el 130 % (41 pasos): con 240 muestras (60
    # ciclos) se llega con holgura.
    mpu.gz = 0.0
    preparar_distancia_puerta(aplicacion, 75.0)
    for i in range(240):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is Estado.CONFIRMANDO_LLEGADA:
            break

    # --- 3.6 Llegada verificada con la senal del destino ----------
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "los pasos no bastan: pide confirmar la llegada",
    )
    comprobar(
        not any("Has llegado" in frase for frase in locutor.frases),
        "sin la senal no anuncia una llegada falsa",
    )
    detector.senal = Senal("Baño", LADO_INTERIOR)
    preparar_distancia_puerta(aplicacion, 75.0)
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "navegacion terminada")
    comprobar(aplicacion.ubicacion == "Baño", "ubicacion final: Baño")
    comprobar(aplicacion.lado_puerta == LADO_INTERIOR, "lado confirmado al llegar")
    comprobar(aplicacion.destino is None, "destino limpio tras la llegada")
    comprobar("Has llegado a Baño." in locutor.frases, "anuncio de llegada")
    comprobar("¿A dónde quieres ir?" in locutor.frases, "vuelve a preguntar destino")

    # --- 3.7 Caida: alarma constante y orden para apagarla ---------
    # El golpe tiene que durar varias muestras seguidas: un glitch
    # aislado del I2C no debe bastar para alarmar.
    mpu.az = 4.0  # golpe brusco contra el suelo
    paso(aplicacion, detector, mpu)
    comprobar(
        not aplicacion.alarma.alarma_activa,
        "un solo sample de golpe no dispara la alarma (glitch I2C)",
    )
    for _ in range(2):
        paso(aplicacion, detector, mpu)
    comprobar(aplicacion.alarma.alarma_activa, "la caida enciende la alarma continua")
    comprobar(
        any("Caida detectada" in frase for frase in locutor.frases),
        "anuncia la caida por voz",
    )
    comprobar(
        microfono.modo is ModoMicrofono.SILENCIO,
        "con la alarma sonando solo se escucha la orden que la apaga",
    )

    microfono.eventos.put(EventoVoz(ModoMicrofono.SILENCIO, "silencia"))
    paso(aplicacion, detector, mpu)
    comprobar(not aplicacion.alarma.alarma_activa, "la orden 'silencia' apaga la alarma")
    comprobar(
        microfono.modo is ModoMicrofono.DESPERTADOR,
        "apagada la alarma vuelve a escuchar el despertador",
    )


def test_destino_sin_ubicacion() -> None:
    print("\n[4] Destino pedido antes de conocer la ubicacion")
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    aplicacion._asignar_destino("Baño")
    comprobar(aplicacion.estado is Estado.GIRO_INICIAL, "no navega sin ubicacion")
    comprobar(
        any("no sé en qué zona" in frase for frase in locutor.frases),
        "explica que falta la senal orientadora",
    )
    comprobar(
        microfono.modo is ModoMicrofono.DESTINO,
        "tras el error vuelve a preguntar el destino (no se apaga)",
    )
    comprobar(
        "¿A dónde quieres ir?" in locutor.frases,
        "vuelve a pedir el destino tras el error",
    )

    # Con ubicacion conocida, un destino sin ruta tambien debe volver
    # a preguntar (antes el microfono se quedaba en despertador y la
    # siguiente indicacion no se reconocia).
    aplicacion.ubicacion = "Baño"
    locutor.frases.clear()
    aplicacion._asignar_destino("Cocina")
    comprobar(
        any("no está configurada" in frase for frase in locutor.frases),
        "avisa que la ruta no esta configurada",
    )
    comprobar(
        microfono.modo is ModoMicrofono.DESTINO,
        "tras 'ruta no configurada' sigue escuchando destinos",
    )


def test_giro_integra_velocidad_angular() -> None:
    """El giroscopio da grados/segundo: hay que integrarlos con el tiempo."""
    print("\n[5] Giro: integracion de la velocidad angular")
    config = Configuracion()
    giro = movimiento.MedidorGiro(config)
    mpu = MPUFalso()
    dt = config.intervalo_muestreo_mpu_s  # 0.01 s (100 Hz)

    # Giro sostenido a la derecha (gz negativo con el sensor plano)
    # de 90 dps durante 1.1 s -> mas de 90 grados.
    mpu.gz = -90.0
    for _ in range(110):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=True) >= 90.0,
        "90 dps durante 1.1 s superan los 90 grados",
    )
    comprobar(
        giro.avanzar(derecha=False) <= -90.0,
        "el giro a la derecha se DESCUENTA del sentido contrario",
    )
    comprobar(
        giro.velocidad_derecha > 0.0,
        "al girar a la derecha la velocidad de giro es positiva",
    )

    # En reposo no se acumula angulo.
    giro.reiniciar()
    mpu.gz = 0.0
    for _ in range(500):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(giro.avanzar(derecha=True) == 0.0, "en reposo no se acumulan grados")

    # Giro lento pero continuo: 10 dps durante 12 s -> 120 grados.
    giro.reiniciar()
    mpu.gz = -(config.offset_giroscopio + 10.0)
    for _ in range(1200):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=True) >= 100.0,
        "un giro lento (10 dps) tambien completa el objetivo",
    )

    # El modulo no esta obligado a ir plano: con el sensor de canto
    # (aceleracion sobre X) la rotacion se mide igual, porque la
    # velocidad se proyecta sobre la vertical.
    giro.reiniciar()
    de_canto = LecturaMPU(1.0, 0.0, 0.0, -90.0, 0.0, 0.0)  # arriba = eje X
    for _ in range(110):
        giro.en_muestra(de_canto, dt)
    comprobar(
        giro.avanzar(derecha=True) >= 90.0,
        "con el sensor de canto tambien mide el giro (proyeccion)",
    )


def test_giro_al_lado_equivocado_se_descuenta() -> None:
    """Girar al lado contrario RETRASA el giro pedido (no se pierde).

    Si la instruccion es "gira 90 grados a la izquierda" y la persona
    gira primero 45 a la derecha, al empezar a girar a la izquierda el
    acumulado arranca NEGATIVO y el giro no se da por completo hasta
    que la rotacion neta llegue a 90.  Al completarse, la persona acaba
    exactamente 90 grados a la izquierda de donde arranco.

    Con la regla vieja (contar solo el acumulado del sentido pedido) el
    giro equivocado no se descontaba y el giro se daba por completo con
    la persona ~45 grados desviada.
    """
    print("\n[5b] Giro: girar al lado equivocado se descuenta")
    config = Configuracion()
    giro = movimiento.MedidorGiro(config)
    mpu = MPUFalso()
    dt = config.intervalo_muestreo_mpu_s  # 0.01 s (100 Hz)

    # Se pide IZQUIERDA, pero la persona gira a la derecha (0.5 s a
    # 100 dps = unos 45 grados de giro mal dado).
    mpu.gz = -100.0
    for _ in range(50):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=False) <= -40.0,
        "45 grados a la derecha dejan el giro de izquierda en negativo",
    )
    comprobar(
        giro.avanzar(derecha=False) < 90.0,
        "girar al lado equivocado no acerca al objetivo de 90",
    )

    # Ahora si gira a la izquierda: los grados del giro equivocado
    # restan, asi que 75 grados de izquierda NO completan los 90.
    mpu.gz = 100.0
    for _ in range(80):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=False) < 90.0,
        "75 a la izquierda todavia no completan (faltan los mal dados)",
    )

    # Sigue a la izquierda hasta que la ROTACION NETA llegue a 90.
    for _ in range(150):
        if giro.avanzar(derecha=False) >= 90.0:
            break
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=False) >= 90.0,
        "el giro se da por completo con 90 grados netos a la izquierda",
    )
    comprobar(
        giro.avanzar(derecha=True) <= -90.0,
        "al completar acaba 90 grados a la izquierda del arranque",
    )


def test_deteccion_de_caida() -> None:
    print("\n[6] Caida: cambio brusco del acelerometro")
    # Sin gracia de arranque para poder probar la caida de inmediato.
    config = dataclasses.replace(Configuracion(), gracia_caida_s=0.0)
    detector_caida = DetectorCaida(config)
    mpu = MPUFalso()

    # Persona quieta: nada de alertas.
    for _ in range(50):
        detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(not detector_caida.hay_caida, "en reposo no hay caida")

    # Un glitch aislado del I2C (una sola muestra rara) no basta.
    mpu.az = 3.5
    detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        not detector_caida.hay_caida,
        "una sola muestra rara no dispara la caida (glitch I2C)",
    )

    # El golpe se sostiene en las muestras siguientes: si es caida.
    detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(detector_caida.hay_caida, "un golpe real (varias muestras) dispara la caida")

    # Tras avisar, el enfriamiento evita repetir la alerta.
    detector_caida.reconocer()
    comprobar(not detector_caida.hay_caida, "al avisar se limpia el aviso")
    detector_caida.en_muestra(mpu.leer(), dt=0.01)
    detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        not detector_caida.hay_caida,
        "el enfriamiento evita alarmas repetidas por el mismo golpe",
    )


def test_pasos_sin_fantasmas() -> None:
    """El sensor sin calibrar no debe contar pasos fantasma."""
    print("\n[9] Pasos: sin fantasma con el acelerometro sin calibrar")
    config = Configuracion()
    pasos = movimiento.ContadorPasos(config)
    mpu = MPUFalso()

    # El acelerometro sin calibrar se sostiene en 1.15 g: una
    # continua no genera picos (el pasa-banda no tiene entrada en
    # continua), asi que quieto no debe contar nada para siempre.
    mpu.az = 1.15
    for _ in range(400):  # 4 segundos de reposo fingido
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        pasos.pasos == 0,
        "con la magnitud alta sostenida no cuenta pasos fantasma",
    )

    # Al caminar la magnitud oscila (pico y reposo): ahi si cuenta.
    # Periodo de 50 muestras a dt = 0,01 s = 2 Hz, dentro de la banda
    # del paso; con el periodo de 4 (25 Hz) la senal quedaria fuera.
    pasos.reiniciar()
    for i in range(500):
        mpu.az = 1.6 if i % 50 < 25 else 1.0
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(pasos.pasos >= 5, "al caminar si cuenta los pasos")


def test_proximidad_se_acelera() -> None:
    print("\n[7] Proximidad: el beep se acelera al acercarse")
    lejos = intervalo_de_beep(200.0, 200.0)
    medio = intervalo_de_beep(100.0, 200.0)
    cerca = intervalo_de_beep(10.0, 200.0)

    comprobar(intervalo_de_beep(250.0, 200.0) is None, "mas alla del umbral: silencio")
    comprobar(
        lejos > medio > cerca > 0.0,
        "el intervalo baja a medida que la persona se acerca",
    )
    sensor = SensorUltrasonico.__new__(SensorUltrasonico)
    sensor._config = Configuracion()
    sensor._distancia_cm = None
    sensor._ultima_distancia_valida_cm = 72.0
    sensor._ultima_lectura_valida_en = time.monotonic()
    comprobar(
        sensor.distancia_para_pitido_cm == 72.0,
        "un eco perdido brevemente no interrumpe el aviso de proximidad",
    )
    sensor._ultima_lectura_valida_en -= 1.0
    comprobar(
        sensor.distancia_para_pitido_cm is None,
        "un eco antiguo no se conserva indefinidamente",
    )


def test_confirmacion_de_senal_tolerante() -> None:
    """Un frame perdido no debe reiniciar la confirmacion de la senal."""
    print("\n[8] Senal: la confirmacion tolera parpadeos de la camara")
    config = Configuracion()
    confirmador = ConfirmadorSenal(config)

    # Patron con parpadeos: detecta, falla, detecta, falla, detecta...
    # (antes el primer fallo borraba la racha y casi nunca se confirmaba).
    confirmador.observar("cow")
    confirmador.observar(None)   # frame perdido
    confirmador.observar("cow")
    confirmador.observar(None)   # frame perdido
    confirmador.observar("cow")
    comprobar(
        confirmador.observar("cow") == Senal("Inicio", LADO_INTERIOR),
        "confirma con parpadeos intercalados (zona y lado de puerta)",
    )

    # Demasiados fallos seguidos si reinician todo.
    confirmador.reiniciar()
    confirmador.observar("zebra")
    for _ in range(config.perdidas_senal_toleradas + 1):
        confirmador.observar(None)
    comprobar(
        confirmador.observar("zebra") is None,
        "tras perder mas frames de los tolerados, empieza de cero",
    )

    # Senal distinta a la mitad: cuenta como cambio, no como racha.
    confirmador.reiniciar()
    confirmador.observar("cow")
    confirmador.observar("cow")
    comprobar(
        confirmador.observar("giraffe") is None,
        "cambiar de senal reinicia la cuenta",
    )


def test_encarada_y_desvio() -> None:
    print("\n[10] Encarada de la senal y aviso de desvio al caminar")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        # La confirmacion del desvio usa el reloj REAL (no el falso de
        # movimiento), asi que aqui se desactiva para probar la
        # mecanica del aviso; la confirmacion en si tiene su propia
        # prueba (test_desvio_requiere_confirmacion).
        config=dataclasses.replace(Configuracion(), confirmacion_desvio_s=0.0),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # La persona esta en Sala pero ya se giro: la senal no esta a la
    # vista y pide ir al bano.
    aplicacion.ubicacion = "Sala"
    aplicacion.estado = Estado.ESPERANDO_DESTINO
    microfono.cambiar_modo(ModoMicrofono.DESTINO)
    detector.senal = None
    microfono.eventos.put(EventoVoz(ModoMicrofono.DESTINO, "quiero ir al baño"))
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "pide encarar la senal antes de arrancar",
    )
    comprobar(
        any("encárate" in frase for frase in locutor.frases),
        "explica que hay que poner la senal enfrente",
    )

    # Gira buscando; mientras tanto la ruta NO arranca a ciegas.
    mpu.gz = -90.0
    for _ in range(3):
        paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.ENCARAR_SENAL, "sigue encarando")
    comprobar(aplicacion._ruta_pendiente is not None, "la ruta queda pendiente")

    # Aparece la senal: se ancla el rumbo y arranca la navegacion.
    detector.senal = Senal("Sala", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "con la senal enfrente arranca la ruta",
    )
    comprobar(
        any("frente a ti" in frase for frase in locutor.frases),
        "confirma que la senal esta enfrente",
    )
    comprobar(aplicacion.rumbo.grados == 0.0, "rumbo anclado en cero")
    detector.senal = None  # la persona giro hacia el destino: senal atras

    # Giro de la ruta (Sala -> Baño: izquierda) completado.
    mpu.gz = 90.0
    for _ in range(12):
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._instruccion_actual is not None
        and aplicacion._instruccion_actual.es_pasos,
        "tras el giro pasa al tramo a pie",
    )

    # Al caminar se va curvando a la derecha: hay que avisar que
    # corrija a la izquierda (el rumbo acumulado delata la curva).
    # Ciclo de 4 muestras a dt = 0,1 s = 2,5 Hz: un paso por ciclo.
    locutor.frases.clear()
    mpu.gz = -60.0
    for i in range(8):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
    comprobar(
        any("desviando" in frase for frase in locutor.frases),
        "avisa del desvio de la linea recta",
    )
    comprobar(
        any("izquierda" in frase for frase in locutor.frases),
        "pide corregir al lado contrario (desvio a la derecha)",
    )
    comprobar(
        any("hasta que te diga" in frase for frase in locutor.frases),
        "promete avisar cuando parar de girar",
    )

    # Sigue desviado y caminando: el sensor cuenta pasos crudos, pero
    # NO se acreditan (la ruta medida es en linea recta).
    locutor.frases.clear()
    for i in range(8):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.pasos.avanzar() > 2,
        "el sensor sigue marcando pasos crudos",
    )
    comprobar(
        not any("Pasos completados" in frase for frase in locutor.frases),
        "no da por completos los pasos dados desviado",
    )
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "el tramo sigue abierto",
    )

    # Endereza (gira al lado contrario hasta la recta) y entonces si
    # vuelven a contar.  Ademas ahora el sistema avisa cuando la
    # correccion ya esta hecha (antes los pasos volvian en silencio).
    # La liberacion usa histérisis (liberacion_desvio_factor): hay
    # que bajar mas abajo del umbral para que diga "ya enderezaste",
    # por eso hacen falta unas cuantas muestras mas que el umbral.
    locutor.frases.clear()
    mpu.gz = 60.0
    for _ in range(16):
        paso(aplicacion, detector, mpu)
    comprobar(
        any("enderezaste" in frase for frase in locutor.frases),
        "avisa cuando ya corrigio el desvio",
    )
    comprobar(
        any("vuelven a contar" in frase for frase in locutor.frases),
        "recuerda que los pasos vuelven a contar",
    )
    comprobar(
        not any("mitad del giro" in frase for frase in locutor.frases),
        "no da avisos a mitad del giro de correccion",
    )
    comprobar(
        not any("Ya casi enderezas" in frase for frase in locutor.frases),
        "no avisa ya casi enderezas mientras corrige",
    )
    mpu.gz = 0.0
    preparar_distancia_puerta(aplicacion, 75.0)
    # El tramo completo Sala -> Baño: un ciclo de 4 muestras (2,5 Hz)
    # vale un paso; 41 pasos (130 % de 31) caben en 240 muestras.
    for i in range(240):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is Estado.CONFIRMANDO_LLEGADA:
            break
    comprobar(
        any("Detente" in frase for frase in locutor.frases),
        "el sistema detiene la marcha al completar el tramo",
    )
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "completa el tramo y pide confirmar la llegada",
    )
    detector.senal = Senal("Baño", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ESPERANDO_DESTINO,
        "la senal del bano confirma la llegada",
    )


def test_lados_de_puerta() -> None:
    """La cara de la puerta decide si la persona esta dentro o fuera."""
    print("\n[11] Lados de la puerta: interior vs pasillo")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # 11.1 Cara del pasillo: identifica la puerta, pero NO la zona.
    aplicacion.estado = Estado.BUSCAR_SENAL
    detector.senal = Senal("Baño", LADO_PASILLO)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.BUSCAR_SENAL,
        "el lado del pasillo no confirma la zona",
    )
    comprobar(aplicacion.ubicacion is None, "sin ubicacion por la cara del pasillo")
    comprobar(
        any("pasillo" in frase for frase in locutor.frases),
        "avisa que se esta al otro lado de la puerta",
    )

    # 11.2 Sigue viendo la misma puerta: el aviso no se repite cada frame.
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        not any("pasillo" in frase for frase in locutor.frases),
        "el aviso de la puerta respeta el enfriamiento",
    )

    # 11.3 Cara interior: si confirma la zona.
    detector.senal = Senal("Baño", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ESPERANDO_DESTINO,
        "la cara interior si confirma la zona",
    )
    comprobar(aplicacion.ubicacion == "Baño", "ubicacion = Baño")
    comprobar(aplicacion.lado_puerta == LADO_INTERIOR, "lado de puerta registrado")

    # 11.4 Encarada: la senal de OTRA zona no sirve como referencia.
    detector.senal = Senal("Sala", LADO_INTERIOR)
    aplicacion.estado = Estado.ENCARAR_SENAL
    aplicacion._ruta_pendiente = [Instruccion(Accion.CAMINAR, 3)]
    aplicacion._instruccion_encarar_dada = True  # ya se explico antes
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "no arranca con la senal de otra zona",
    )
    comprobar(aplicacion._ruta_pendiente is not None, "la ruta sigue pendiente")
    comprobar(
        any("Sala" in frase for frase in locutor.frases),
        "explica que esa senal no es de su zona",
    )

    # 11.5 Senal propia pero del lado del pasillo: que cruce la puerta.
    detector.senal = Senal("Baño", LADO_PASILLO)
    aplicacion._ultima_advertencia.clear()  # enfriamiento del aviso 11.1
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "del lado del pasillo no arranca",
    )
    comprobar(
        any("pasillo" in frase for frase in locutor.frases),
        "pide cruzar hacia dentro de la puerta",
    )

    # 11.6 Cara interior de su zona: ancla el rumbo y arranca.
    detector.senal = Senal("Baño", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.NAVEGANDO, "con la cara interior si arranca")
    comprobar(aplicacion.rumbo.grados == 0.0, "rumbo anclado en la senal")

    # 11.7 Pedido de destino del lado del pasillo: que cruce primero.
    aplicacion.estado = Estado.ESPERANDO_DESTINO
    aplicacion.lado_puerta = LADO_PASILLO
    locutor.frases.clear()
    aplicacion._asignar_destino("Sala")
    comprobar(
        any("pasillo" in frase for frase in locutor.frases),
        "del lado del pasillo no planifica la ruta",
    )
    comprobar(
        aplicacion.estado is Estado.ESPERANDO_DESTINO,
        "no navega hasta cruzar la puerta",
    )


def test_relocalizacion_y_devolucion() -> None:
    """Navegando, las senales corrigen la ubicacion real de la persona."""
    print("\n[12] Re-localizacion, llegada verificada y devolucion")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # 12.1 Los pasos medidos NO anuncian llegada: falta ver la senal.
    aplicacion.ubicacion = "Sala"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.destino = "Baño"
    aplicacion._iniciar_navegacion(
        [Instruccion(Accion.CAMINAR, 2), Instruccion(Accion.LLEGADA)]
    )
    preparar_distancia_puerta(aplicacion, 75.0)
    detector.senal = None
    mpu.gz = 0.0
    for i in range(40):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is not Estado.NAVEGANDO:
            break
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "los pasos no bastan: pasa a confirmar la llegada",
    )
    comprobar(
        not any("Has llegado" in frase for frase in locutor.frases),
        "sin la senal no anuncia una llegada falsa",
    )

    # 12.2 La señal del destino centrada confirma sin exigir 70-80 cm.
    detector.senal = Senal("Baño", LADO_INTERIOR)
    preparar_distancia_puerta(aplicacion, 150.0)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ESPERANDO_DESTINO
        and "Has llegado a Baño." in locutor.frases,
        "la señal centrada a 150 cm confirma sin exigir una distancia exacta",
    )
    comprobar(aplicacion.ubicacion == "Baño", "ubicacion actualizada al llegar")
    aplicacion.config = dataclasses.replace(
        aplicacion.config, distancia_alerta_cm=100.0
    )

    # 12.2b Durante la navegación, una señal vista desde lejos tampoco
    # debe confirmar mientras haya un obstáculo o esté vigente su espera.
    locutor.frases.clear()
    detector.senal = Senal("Baño", LADO_INTERIOR)
    detector.obstaculos = (ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),)
    aplicacion.ubicacion = "Sala"
    aplicacion.destino = "Baño"
    aplicacion._iniciar_navegacion(
        [Instruccion(Accion.CAMINAR, 5), Instruccion(Accion.LLEGADA)]
    )
    preparar_distancia_puerta(aplicacion, 150.0)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO
        and not any("Has llegado" in frase for frase in locutor.frases),
        "un obstáculo frontal bloquea la confirmación de cámara",
    )
    detector.obstaculos = ()
    aplicacion._ultima_alerta_obstaculo_en = time.monotonic()
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO
        and not any("Has llegado" in frase for frase in locutor.frases),
        "espera tras el aviso de obstáculo antes de confirmar la señal",
    )
    aplicacion._ultima_alerta_obstaculo_en = (
        time.monotonic() - aplicacion.config.espera_llegada_tras_obstaculo_s - 1
    )
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ESPERANDO_DESTINO
        and any("Has llegado a Baño." in frase for frase in locutor.frases),
        "la señal centrada confirma después de terminar la espera",
    )

    # 12.2c Si el ultrasónico indica proximidad insegura, la cámara no basta.
    locutor.frases.clear()
    aplicacion.ubicacion = "Sala"
    aplicacion.destino = "Baño"
    aplicacion._iniciar_navegacion(
        [Instruccion(Accion.CAMINAR, 5), Instruccion(Accion.LLEGADA)]
    )
    detector.senal = None
    preparar_distancia_puerta(aplicacion, 150.0)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO
        and not any("Has llegado" in frase for frase in locutor.frases),
        "arranca el siguiente tramo sin anunciar una llegada falsa",
    )
    detector.senal = Senal("Baño", LADO_INTERIOR)
    preparar_distancia_puerta(aplicacion, 30.0)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO
        and not any("Has llegado" in frase for frase in locutor.frases),
        "la proximidad insegura no permite confirmar la señal",
    )

    # 12.3 Se paso de largo: ve la senal de otra zona y lo corrige.
    aplicacion.estado = Estado.CONFIRMANDO_LLEGADA
    aplicacion.destino = "Baño"
    aplicacion._inicio_llegada = time.monotonic()
    detector.senal = Senal("Sala", LADO_INTERIOR)
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        any("pasaste de largo" in frase for frase in locutor.frases),
        "detecta que se paso de largo",
    )
    comprobar(aplicacion.ubicacion == "Sala", "corrige la ubicacion a la senal vista")
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "vuelve a preguntar destino")

    # 12.4 Se devuelve en medio del tramo: replanifica con la verdad.
    detector.senal = None
    aplicacion.ubicacion = "Sala"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.destino = "Baño"
    aplicacion._iniciar_navegacion([Instruccion(Accion.CAMINAR, 6)])
    for i in range(16):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
    comprobar(aplicacion._pasos_acreditados > 0, "acredita algunos pasos rectos")
    detector.senal = Senal("Sala", LADO_INTERIOR)  # miro hacia atras: sigue en Sala
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        any("saliste de la ruta" in frase for frase in locutor.frases),
        "avisa de que se salio de la ruta",
    )
    comprobar(aplicacion.estado is Estado.NAVEGANDO, "sigue navegando con plan nuevo")
    comprobar(aplicacion._pasos_acreditados == 0, "el plan nuevo reinicia los pasos")

    # 12.5 Ordenes de voz para devolverse o recalcular la ruta.
    comprobar(
        reconocer("quiero devolverme").tipo is TipoComando.DEVOLVERME,
        "reconoce 'devolverme'",
    )
    comprobar(
        reconocer("otra ruta").tipo is TipoComando.OTRA_RUTA,
        "reconoce 'otra ruta'",
    )
    comprobar(
        reconocer("vuelve a la sala").tipo is TipoComando.DESTINO,
        "'vuelve a la sala' sigue siendo un destino",
    )

    # Con ruta en curso hacia Baño, "devolverme" apunta a la zona
    # anterior del camino (Cuarto) y pide encarar antes de arrancar.
    detector.senal = None
    aplicacion.ubicacion = "Sala"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.destino = "Baño"
    aplicacion.estado = Estado.NAVEGANDO
    aplicacion._camino = ["Cuarto", "Sala", "Baño"]
    aplicacion._indice_zona = 1
    locutor.frases.clear()
    aplicacion._ejecutar_comando("quiero devolverme")
    comprobar(aplicacion.destino == "Cuarto", "'devolverme' cambia el destino")
    comprobar(aplicacion.estado is Estado.ENCARAR_SENAL, "pide encarar antes de volver")

    # "otra ruta" recalcula hacia el mismo destino desde la ubicacion
    # real (que las senales van corrigiendo).
    aplicacion.estado = Estado.NAVEGANDO
    aplicacion.destino = "Baño"
    aplicacion.ubicacion = "Sala"
    aplicacion.lado_puerta = LADO_INTERIOR
    locutor.frases.clear()
    aplicacion._ejecutar_comando("otra ruta")
    comprobar(aplicacion.estado is Estado.ENCARAR_SENAL, "'otra ruta' recalcula la ruta")
    comprobar(aplicacion.destino == "Baño", "mantiene el destino original")


def test_encarada_por_sentido() -> None:
    """Las imagenes estan a la derecha: al volver el giro se invierte."""
    print("\n[13] Encarada: el giro depende del sentido de la ruta")
    planificador = PlanificadorRutas()

    # TODAS las combinaciones posibles de la casa (4 zonas en linea =
    # 12 pares de ruta).  Las imagenes estan fijas de un lado del
    # pasillo (``GIRO_SENAL_NORMAL``): todo viaje que AVANZA hacia el
    # Bano encara a ese lado y todo viaje que REGRESA hacia el Inicio
    # (mas lejano -> mas cercano) encara al opuesto.  El lado en si es
    # dato de la casa, no la logica que se comprueba: se lee de la
    # constante para que la prueba siga valida si se mueven las
    # senales.
    orden = ["Inicio", "Cuarto", "Sala", "Baño"]
    malos: list[str] = []
    for origen in orden:
        for destino in orden:
            if origen == destino:
                continue
            esperado = (
                GIRO_SENAL_NORMAL
                if orden.index(destino) > orden.index(origen)
                else GIRO_SENAL_INVERSO
            )
            real = planificador.lado_encarada(origen, destino)
            if real != esperado:
                malos.append(f"{origen}->{destino}: era {real}, queria {esperado}")
    comprobar(
        not malos,
        "las 12 combinaciones encaran al lado correcto"
        + (f" -> {malos}" if malos else ""),
    )
    comprobar(
        planificador.lado_encarada("Sala", "Sala") == GIRO_SENAL_NORMAL,
        "sin tramo que recorrer se queda en el giro normal",
    )

    # Y la frase que se le dice a la persona usa ese lado.
    movimiento.time = RelojFalso()
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()
    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=planificador,
        ventana=VentanaFalsa(),
    )

    # Sentido normal: Inicio -> Cuarto.
    aplicacion.ubicacion = "Inicio"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.estado = Estado.ESPERANDO_DESTINO
    microfono.cambiar_modo(ModoMicrofono.DESTINO)
    microfono.eventos.put(EventoVoz(ModoMicrofono.DESTINO, "quiero ir al cuarto"))
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "pide encarar antes de arrancar",
    )
    comprobar(
        any(
            f"Gira despacio a la {GIRO_SENAL_NORMAL}" in frase
            for frase in locutor.frases
        ),
        f"de Inicio a Cuarto pide girar a la {GIRO_SENAL_NORMAL}",
    )

    # Sentido inverso: Bano -> Sala.
    aplicacion.ubicacion = "Baño"
    aplicacion.estado = Estado.ESPERANDO_DESTINO
    locutor.frases.clear()
    microfono.cambiar_modo(ModoMicrofono.DESTINO)
    microfono.eventos.put(EventoVoz(ModoMicrofono.DESTINO, "quiero ir a la sala"))
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "en el viaje de vuelta tambien encara antes de arrancar",
    )
    comprobar(
        any(
            f"Gira despacio a la {GIRO_SENAL_INVERSO}" in frase
            for frase in locutor.frases
        ),
        f"del Bano a la Sala pide girar a la {GIRO_SENAL_INVERSO}",
    )
    comprobar(
        not any(
            f"Gira despacio a la {GIRO_SENAL_NORMAL}" in frase
            for frase in locutor.frases
        ),
        "ya no da la indicacion del sentido normal",
    )


def test_obstaculos_en_medio() -> None:
    """Los objetos se anuncian nombrados y con la posicion en la que estan."""
    print("\n[14] Obstaculos: en medio del camino y a los lados")
    # Los anuncios de obstaculos viven SOLO en la navegacion; los
    # lados se prueban aqui con la voz de lados encendida y el
    # filtro de fragmento central apagado (por defecto quedan mudos
    # y solo cuenta el central, ver [24] y [25]).
    config = dataclasses.replace(
        Configuracion(), anunciar_lados=True, mirar_solo_centro_en_marcha=False
    )

    def donde(caja: tuple[int, int, int, int]) -> str:
        return posicion_en_frame(
            caja,
            ancho=config.ancho,
            minimo=config.obstaculo_centro_min,
            maximo=config.obstaculo_centro_max,
        )

    comprobar(
        donde((300, 80, 340, 240)) == EN_MEDIO_DEL_CAMINO,
        "un objeto centrado esta en medio del camino",
    )
    comprobar(
        donde((10, 80, 90, 240)) == "izquierda",
        "el de la izquierda se queda a la izquierda",
    )
    comprobar(
        donde((560, 80, 630, 240)) == "derecha",
        "el de la derecha se queda a la derecha",
    )
    # La caja COMPLETA tiene que caber en el fragmento central: un
    # objeto ancho que se sale de un lado (aunque su centro caiga
    # dentro) NO es "en medio del camino" y no debe frenar.
    comprobar(
        donde((150, 80, 350, 240)) == "izquierda",
        "un objeto que se sale por la izquierda NO es del centro",
    )
    comprobar(
        donde((300, 80, 500, 240)) == "derecha",
        "un objeto que se sale por la derecha NO es del centro",
    )
    comprobar(
        donde((100, 80, 550, 240)) == EN_MEDIO_DEL_CAMINO,
        "uno frontal y ancho que ocupa el central entero si es del centro",
    )

    movimiento.time = RelojFalso()
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )
    aplicacion.estado = Estado.NAVEGANDO

    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("maceta", "izquierda"),
    )
    anunciar(aplicacion, detector, aplicacion.sensor.distancia_cm)
    comprobar(
        any(
            "en medio del camino" in frase and "silla" in frase
            for frase in locutor.frases
        ),
        "nombra el objeto que esta en medio del camino",
    )
    comprobar(
        any("a unos 1.5 metros" in frase for frase in locutor.frases),
        "con el eco cerca, la frase lleva la distancia del ultrasonido",
    )
    comprobar(
        not any("maceta, a unos" in frase for frase in locutor.frases),
        "a los lados no se pega la distancia del eco (es de enfrente)",
    )
    comprobar(
        any(
            "a tu izquierda" in frase and "maceta" in frase
            for frase in locutor.frases
        ),
        "nombra el lateral con su lado",
    )

    # Los lados informan SIN frenar: una sola frase aunque haya
    # objetos de los dos lados y sin el "Alto" del medio (no se
    # cruzan en la ruta, no hay por que cortar el paso).
    locutor.frases.clear()
    detector.obstaculos = (
        ObstaculoVisto("silla", "izquierda"),
        ObstaculoVisto("mesa", "derecha"),
    )
    anunciar(aplicacion, detector, aplicacion.sensor.distancia_cm)
    comprobar(
        len(locutor.frases) == 1,
        "los de los dos lados van en una sola frase",
    )
    comprobar(
        not any("Alto" in frase for frase in locutor.frases),
        "los lados no dicen Alto: reconocen pero no frenan",
    )
    comprobar(
        not any("en medio del camino" in frase for frase in locutor.frases),
        "sin objeto centrado no dice que hay algo en medio",
    )

    # Varias en el centro: una sola frase con todos los nombres.
    locutor.frases.clear()
    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("mesa", EN_MEDIO_DEL_CAMINO),
    )
    anunciar(aplicacion, detector, aplicacion.sensor.distancia_cm)
    comprobar(
        any(
            "en medio del camino" in frase and "silla y mesa" in frase
            for frase in locutor.frases
        ),
        "agrupa los objetos del medio en una sola frase",
    )
    comprobar(
        len(locutor.frases) == 1,
        "los del medio no se anuncian uno por uno",
    )


def test_obstaculos_sincronizados() -> None:
    """Camara y ultrasonido hablan del mismo obstaculo, no por separado."""
    print("\n[16] Obstaculos: camara y ultrasonido sincronizados")
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()

    # 16.1 Camara ve el objeto y el eco esta lejos: se nombra SIN
    # distancia (el eco todavia no pitia; seria mentir).
    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(distancia_cm=350.0),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )
    # Los anuncios de obstaculos viven SOLO en la navegacion.
    aplicacion.estado = Estado.NAVEGANDO
    detector.obstaculos = (ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),)
    anunciar(aplicacion, detector, 350.0)
    comprobar(
        any(
            "en medio del camino hay un obstáculo: silla" in frase
            and "metros" not in frase
            for frase in locutor.frases
        ),
        "con el eco lejos no inventa distancia",
    )

    # 16.2 Entra en el rango del eco con la camara viendo el objeto:
    # UNA sola frase con nombre y distancia (los pitidos quedan
    # explicados) y NO se repite la frase generica de los pitidos.
    locutor.frases.clear()
    aplicacion._cerca_antes = False
    aplicacion._ultimo_anuncio_obstaculo = 0.0
    anunciar(aplicacion, detector, 120.0)
    comprobar(
        any("silla, a unos 1.2 metros" in frase for frase in locutor.frases),
        "cerca: nombre y distancia en la misma frase",
    )
    comprobar(
        not any("pitidos" in frase for frase in locutor.frases),
        "no explica los pitidos si la camara ya nombró el obstaculo",
    )

    # 16.3 Solo ultrasonido (algo que la camara no nombra): al entrar
    # en rango se explica UNA vez que los pitidos significan peligro.
    locutor.frases.clear()
    detector.obstaculos = ()
    aplicacion._cerca_antes = False
    aplicacion._ultimo_anuncio_obstaculo = 0.0
    anunciar(aplicacion, detector, 80.0)
    comprobar(
        any("Hay algo enfrente" in frase for frase in locutor.frases),
        "sin objeto a la vista explica lo que significan los pitidos",
    )
    locutor.frases.clear()
    anunciar(aplicacion, detector, 75.0)
    comprobar(
        len(locutor.frases) == 0,
        "siguiendo en rango no repite la explicacion cada vuelta",
    )

    # 16.4 Se aleja del obstaculo: se rearmar para el proximo acercamiento.
    anunciar(aplicacion, detector, 350.0)
    aplicacion._ultima_advertencia.clear()  # olvida el enfriamiento de 16.3
    locutor.frases.clear()
    anunciar(aplicacion, detector, 60.0)
    comprobar(
        any("Hay algo enfrente" in frase for frase in locutor.frases),
        "al volver a entrar en rango vuelve a explicar los pitidos",
    )


def test_encarada_intermedia_de_tramo() -> None:
    """Al terminar un tramo falta encarar la zona alcanzada.

    La ruta Inicio -> Sala tiene dos tramos: despues de los pasos del
    primero la persona esta en el Cuarto mirando hacia donde vino, y
    las senales van del lado derecho del pasillo.  Antes del giro del
    segundo tramo hay que pedir girar a la derecha hasta ver la senal
    de esa zona (antes se daba el giro de 90 grados directamente y la
    persona se quedaba mirando al otro lado).
    """
    print("\n[15] Encarada intermedia al terminar un tramo")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # La persona esta en Inicio viendo su senal y pide la Sala: la
    # ruta cruza dos tramos (Inicio -> Cuarto -> Sala).
    aplicacion.ubicacion = "Inicio"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.estado = Estado.ESPERANDO_DESTINO
    microfono.cambiar_modo(ModoMicrofono.DESTINO)
    detector.senal = Senal("Inicio", LADO_INTERIOR)
    microfono.eventos.put(EventoVoz(ModoMicrofono.DESTINO, "quiero ir a la sala"))
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "con la senal enfrente arranca la ruta",
    )

    # --- Giro y pasos del primer tramo (Inicio -> Cuarto) -----------
    detector.senal = None  # la persona giro: la senal queda atras
    locutor.frases.clear()
    mpu.gz = 100.0  # giro izquierda del tramo
    for _ in range(15):
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._instruccion_actual is not None
        and aplicacion._instruccion_actual.es_pasos,
        "giro del primer tramo completado, sigue caminar",
    )

    mpu.gz = 0.0
    pasos = TRAMOS_DEMO[("Inicio", "Cuarto")].pasos
    for i in range(pasos * 8):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is not Estado.NAVEGANDO:
            break
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "al completar los pasos pide encarar la zona alcanzada",
    )
    comprobar(aplicacion.ubicacion == "Cuarto", "ubica a la persona en el Cuarto")
    comprobar(
        aplicacion._ruta_pendiente is None,
        "no hay plan nuevo: la ruta en curso sigue intacta",
    )

    # Sin verla aun, pide girar al lado donde estan las senales en el
    # sentido de la ruta (``GIRO_SENAL_NORMAL``) y NO el giro de 90.
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        any(
            f"Gira despacio a la {GIRO_SENAL_NORMAL}" in frase
            for frase in locutor.frases
        ),
        f"pide girar a la {GIRO_SENAL_NORMAL} para ver la senal del Cuarto",
    )
    comprobar(
        not any("90 grados" in frase for frase in locutor.frases),
        "todavia no da el giro del siguiente tramo",
    )

    # --- Aparece la senal del Cuarto: ancla y retoma la ruta --------
    detector.senal = Senal("Cuarto", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "con la senal del Cuarto en vista retoma la ruta",
    )
    comprobar(
        any("frente a ti" in frase for frase in locutor.frases),
        "confirma que la senal esta enfrente",
    )
    comprobar(aplicacion.rumbo.grados == 0.0, "rumbo anclado en el Cuarto")

    # --- Giro y pasos del segundo tramo (Cuarto -> Sala) ------------
    detector.senal = None
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        any("Gira a la izquierda 90 grados" in frase for frase in locutor.frases),
        "ya si anuncia el giro del segundo tramo",
    )
    comprobar(
        aplicacion._instruccion_actual is not None
        and aplicacion._instruccion_actual.es_giro,
        "el giro del Cuarto queda en curso",
    )

    mpu.gz = 100.0
    for _ in range(15):
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._instruccion_actual is not None
        and aplicacion._instruccion_actual.es_pasos,
        "giro del segundo tramo completado",
    )

    mpu.gz = 0.0
    preparar_distancia_puerta(aplicacion, 75.0)
    pasos = TRAMOS_DEMO[("Cuarto", "Sala")].pasos
    for i in range(pasos * 8):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is not Estado.NAVEGANDO:
            break
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "terminado el ultimo tramo pasa a confirmar la llegada",
    )

    # La senal del destino confirma: viaje completo Inicio -> Sala.
    detector.senal = Senal("Sala", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "navegacion terminada")
    comprobar(aplicacion.ubicacion == "Sala", "ubicacion final: Sala")
    comprobar("Has llegado a Sala." in locutor.frases, "anuncio de llegada")


def test_encarada_automatica() -> None:
    """La senal recien confirmada no se vuelve a pedir.

    En casa la busqueda acaba de decir "Estas en Inicio": si al pedir
    el destino se vuelve a pedir encarar, la persona gira buscando una
    senal que no esta en el sitio (las de las demas zonas estan a pasos
    de distancia) y el sistema se queda pidiendo girar sin salida.
    """
    print("\n[21] Encarada automatica tras confirmar la zona")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # La busqueda confirma la senal interior de Inicio.
    aplicacion.estado = Estado.BUSCAR_SENAL
    detector.senal = Senal("Inicio", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ESPERANDO_DESTINO,
        "la busqueda confirma la zona",
    )

    # Pide el destino sin moverse (baja la senal para hablar): la
    # ruta arranca sin volver a pedir encarar.
    detector.senal = None
    microfono.cambiar_modo(ModoMicrofono.DESTINO)
    microfono.eventos.put(EventoVoz(ModoMicrofono.DESTINO, "quiero ir al cuarto"))
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "arranca la ruta sin pedir encarar",
    )
    comprobar(
        not any("encárate" in frase for frase in locutor.frases),
        "no vuelve a pedir la senal de la zona",
    )
    comprobar(aplicacion.rumbo.grados == 0.0, "el rumbo queda anclado en la senal")

    # Si entretanto giro bastante, la senal ya no esta enfrente: si
    # que hay que encarar antes de arrancar.
    aplicacion.ubicacion = "Inicio"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.estado = Estado.ESPERANDO_DESTINO
    microfono.cambiar_modo(ModoMicrofono.DESTINO)
    locutor.frases.clear()
    mpu.gz = -100.0  # giro a la derecha mientras esperaba la respuesta
    for _ in range(4):  # ~40 grados: pasa la tolerancia de 20
        paso(aplicacion, detector, mpu)
    microfono.eventos.put(EventoVoz(ModoMicrofono.DESTINO, "quiero ir al cuarto"))
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.ENCARAR_SENAL,
        "giro bastante: si pide encarar",
    )
    comprobar(
        any("encárate" in frase for frase in locutor.frases),
        "explica que hay que poner la senal enfrente",
    )


def test_pasos_con_media_cambiada() -> None:
    """Una subida de postura no deja de contar los pasos.

    Con la linea base vieja (un reposo congelado) la marcha inflaba la
    referencia y los pasos reales se hundian bajo el umbral: "a veces
    no cuenta".  Aqui toda la magnitud sube 0.10 g de golpe (la
    persona se agacha o carga algo): el pasa-banda no tiene entrada en
    continua, asi que esa subida se va sola y la oscilacion del paso
    sigue saliendo igual.
    """
    print("\n[17] Pasos: una subida de postura no corta la cuenta")
    config = Configuracion()
    pasos = movimiento.ContadorPasos(config)
    mpu = MPUFalso()

    # Marcha con la cintura a la altura normal: valles 1.00, picos
    # 1.25.  Periodo de 50 muestras a dt = 0,01 s = 2 Hz (en banda).
    for i in range(500):
        mpu.az = 1.25 if i % 50 < 25 else 1.00
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(pasos.pasos >= 5, "con la postura inicial cuenta los pasos")

    # Toda la magnitud sube 0.10 g de golpe (valles 1.10, picos
    # 1.35): el continua lo bloquea el filtro y la oscilacion de
    # 0.25 g sigue cruzando el umbral adaptativo igual que antes.
    previos = pasos.pasos
    for i in range(500):
        mpu.az = 1.35 if i % 50 < 25 else 1.10
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        pasos.pasos - previos >= 5,
        "subida la magnitud 0.10 g de golpe sigue contando",
    )


def test_rumbo_ignora_balanceo() -> None:
    """El vaiven del torso al caminar NO mueve el rumbo; un giro si."""
    print("\n[18] Rumbo: el balanceo al caminar no desvia la ruta")
    config = Configuracion()
    rumbo = Rumbo()
    giro = movimiento.MedidorGiro(config, rumbo=rumbo)
    mpu = MPUFalso()
    dt = 0.01  # 100 muestras por segundo, como el muestreador real

    # Balanceo tipico de la cintura: +20 a -20 dps a 2 Hz.  Con el
    # sensor plano la velocidad de giro a la derecha es -gz, por eso el
    # gz del ensayo lleva el signo opuesto.
    for i in range(int(8.0 / dt)):
        mpu.gz = -20.0 * math.sin(2.0 * math.pi * 2.0 * i * dt)
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        abs(rumbo.grados) < 5.0,
        "8 s de balanceo a 2 Hz: el rumbo casi no se mueve",
    )

    # Un giro de verdad, en cambio, se integra enseguida (el filtro
    # sostenido se llena en decimas de segundo y despues pasa todo).
    mpu.gz = -100.0
    for _ in range(100):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(rumbo.grados >= 60.0, "un giro sostenido de 1 s si se mide")

    rumbo.anclar()
    rumbo.girar(457.0)
    comprobar(
        abs(rumbo.grados - 97.0) < 1e-9,
        "el rumbo se normaliza y nunca acumula mas de una vuelta",
    )
    comprobar(
        abs(rumbo.diferencia(-179.0, 179.0) - 2.0) < 1e-9,
        "la diferencia angular cruza correctamente el limite de 180 grados",
    )


def test_sesgo_corregido_en_marcha() -> None:
    """El sesgo aprendido con la envolvente frena la deriva del rumbo.

    Un sesgo real de 6 dps (mala calibracion inicial, calentamiento)
    esta POR ENCIMA del umbral de giro: si no se corrige, el rumbo
    deriva como si la persona girara a la derecha para siempre y salen
    avisos de desvio falsos.  La regla vieja ("aprende de cualquier
    muestra en reposo") se re-aprendia a si misma con el sesgo
    equivocado y no salia de ahi; el punto medio de la envolvente si.
    """
    print("\n[19] Sesgo: aprendido en marcha, el rumbo deja de derivar")
    config = Configuracion()
    rumbo = Rumbo()
    giro = movimiento.MedidorGiro(config, rumbo=rumbo)
    mpu = MPUFalso()
    dt = 0.01
    sesgo_real_dps = 6.0

    def marchar(segundos: float) -> None:
        """Marcha recta con balanceo de 2 Hz encima del sesgo real."""
        for i in range(int(segundos / dt)):
            velocidad = sesgo_real_dps + 20.0 * math.sin(
                2.0 * math.pi * 2.0 * i * dt
            )
            mpu.gz = -velocidad
            giro.en_muestra(mpu.leer(), dt)

    # 20 s de marcha: el sesgo aprendido se acerca al real y la deriva
    # del rumbo queda acotada (no crece sin limite como sin corregir).
    marchar(20.0)
    comprobar(
        abs(giro._sesgo - sesgo_real_dps) <= 1.5,
        f"el sesgo aprendido ({giro._sesgo:.1f} dps) sigue al real (6 dps)",
    )
    derivada = rumbo.grados
    comprobar(
        abs(derivada) < 60.0,
        "mientras se recalibraba la deriva del rumbo queda limitada",
    )

    # Corregido el sesgo, seguir caminando recto ya no mueve el rumbo:
    # lo que importa es que la deriviva SE DETENGA, no el valor acumulado
    # del transitorio (ese se borra en la siguiente senal anclada).
    marchar(5.0)
    comprobar(
        abs(rumbo.grados - derivada) < 3.0,
        "con el sesgo corregido, 5 s mas de marcha casi no deriva",
    )


def test_rumbo_resiste_aceleracion_de_pasos() -> None:
    """La aceleracion lateral de cada paso no debe inclinar la vertical."""
    print("\n[19b] Rumbo: aceleracion lateral correlacionada con balanceo")
    config = dataclasses.replace(
        Configuracion(),
        offset_giroscopio=0.0,
        constante_sesgo_s=1e9,
    )
    rumbo = Rumbo()
    giro = movimiento.MedidorGiro(config, rumbo=rumbo)
    dt = 0.01

    # Sensor inclinado: el balanceo ocurre sobre X, ortogonal a la
    # gravedad. La aceleracion lateral correlacionada hacia X falseaba
    # la proyeccion instantanea como si fuera giro sobre la vertical.
    for i in range(int(20.0 / dt)):
        balanceo = math.sin(2.0 * math.pi * 1.5 * i * dt)
        lectura = LecturaMPU(
            0.3 * balanceo,
            0.93,
            0.2,
            40.0 * balanceo,
            0.0,
            0.0,
        )
        giro.en_muestra(lectura, dt)
    comprobar(
        abs(rumbo.grados) < 5.0,
        f"20 s de balanceo inclinado no acumulan rumbo ({rumbo.grados:.1f}°)",
    )


def test_senal_centrada_prevalece_sobre_deriva_inercial() -> None:
    """Una señal esperada centrada evita una corrección falsa de rumbo."""
    print("\n[19c] Cámara: señal de cierre centrada corrige el rumbo inercial")
    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=LocutorFalso(),
        microfono=MicrofonoFalso(),
        detector=DetectorFalso(),
        ventana=VentanaFalsa(),
    )
    aplicacion._camino = ["Sala", "Cuarto"]
    aplicacion._indice_zona = 0
    aplicacion._zona_cierre_tramo = "Cuarto"
    aplicacion._rumbo_al_caminar = 0.0
    aplicacion.rumbo.girar(45.0)
    aplicacion.pasos._pasos = 1
    aplicacion._corrigiendo = True
    aplicacion._inicio_desvio = time.monotonic() - 3.0
    resultado = ResultadoVision(
        senal=Senal("Cuarto", LADO_INTERIOR),
        detecciones=(
            Deteccion(
                "sheep",
                0.9,
                (250, 100, 390, 300),
                True,
                False,
            ),
        ),
    )

    comprobar(
        aplicacion._deteccion_senal_cierre_centrada(resultado),
        "reconoce centrada la señal esperada del siguiente tramo",
    )
    comprobar(
        aplicacion._creditar_pasos(resultado) == 1,
        "acredita el paso hacia la señal aunque el MPU indique desvío",
    )
    aplicacion._vigilar_desvio(resultado)
    comprobar(
        not any("desviando" in frase for frase in aplicacion.locutor.frases)
        and not aplicacion._corrigiendo,
        "no ordena girar alejándose de la señal que está enfrente",
    )

    aplicacion.pasos._pasos = 2
    aplicacion._rumbo_al_caminar = 0.0
    con_obstaculo = ResultadoVision(
        senal=resultado.senal,
        detecciones=resultado.detecciones,
        obstaculos=(ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),),
    )
    comprobar(
        not aplicacion._deteccion_senal_cierre_centrada(con_obstaculo)
        and aplicacion._creditar_pasos(con_obstaculo) == 1,
        "un obstáculo frontal impide usar la cámara para acreditar avance",
    )


def test_desvio_requiere_confirmacion() -> None:
    """Ignora desvíos pequeños y confirma los grandes de forma sostenida.

    La confirmacion usa el reloj REAL de la aplicacion (guia.aplicacion),
    asi que este ensayo duerme un poco de verdad: asi queda comprobado
    el tiempo de espera tal cual funciona en la Raspberry Pi.
    """
    print("\n[20] Desvio: confirmacion temporal y histéresis de liberacion")
    movimiento.time = RelojFalso()  # reloj acelerado para el muestreo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    # Tramo a pie en curso hacia el bano.
    aplicacion.ubicacion = "Sala"
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion.destino = "Baño"
    aplicacion._iniciar_navegacion([Instruccion(Accion.CAMINAR, 50)])
    detector.senal = None

    # Unos pasos rectos fijan la referencia de la linea recta (el
    # acelerometro oscila: tienen que llegar pasos crudos para que la
    # referencia se bloquee con el primer paso).  Ciclo de 4 muestras
    # a dt = 0,1 s = 2,5 Hz, dentro de la banda del paso.
    mpu.gz = 0.0
    for i in range(4):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
        paso(aplicacion, detector, mpu)

    comprobar(
        aplicacion.config.desvio_maximo_grados == 35.0
        and aplicacion.config.confirmacion_desvio_s == 2.5,
        "usa umbral de 35 grados y confirmación de 2,5 s",
    )
    aplicacion.rumbo.girar(30.0)
    aplicacion._vigilar_desvio()
    comprobar(
        aplicacion._inicio_desvio is None
        and not any("desviando" in frase for frase in locutor.frases),
        "ignora un cambio pequeño de 30 grados",
    )

    aplicacion.rumbo.girar(6.0)
    aplicacion._vigilar_desvio()
    comprobar(
        aplicacion._inicio_desvio is not None,
        "el desvio cruza el umbral y empieza la espera de confirmacion",
    )
    comprobar(
        not any("desviando" in frase for frase in locutor.frases),
        "sin cumplirse la confirmacion todavia no avisa",
    )

    # Sigue desviado: al pasar la configurada (2.5 s), ahi si sale el aviso.
    time.sleep(aplicacion.config.confirmacion_desvio_s + 0.1)
    aplicacion._vigilar_desvio()
    comprobar(
        any("desviando" in frase for frase in locutor.frases),
        "confirmado el desvio, avisa con los grados y como corregir",
    )
    comprobar(
        aplicacion._corrigiendo,
        "a partir de ahi queda en modo correccion",
    )

    # En la zona de histéresis (entre liberacion * umbral y el umbral)
    # no anuncia ni que sigue desviado ni que ya enderezó.
    locutor.frases.clear()
    aplicacion.rumbo.girar(-10.0)
    aplicacion._vigilar_desvio()
    comprobar(
        not any("enderezaste" in frase for frase in locutor.frases),
        "dentro de la zona de histéresis no anuncia que enderezo",
    )

    # Baja del umbral de liberacion: ahi si avisa que ya enderezo y
    # que los pasos vuelven a contar.
    aplicacion.rumbo.girar(-6.0)
    aplicacion._vigilar_desvio()
    comprobar(
        any("enderezaste" in frase for frase in locutor.frases),
        "bajando del umbral de liberacion si anuncia que enderezo",
    )
    comprobar(
        any("vuelven a contar" in frase for frase in locutor.frases),
        "recuerda que los pasos vuelven a contar",
    )


def test_obstaculos_un_aviso_por_encuentro() -> None:
    """Un obstaculo se anuncia UNA vez por encuentro, no cada rato.

    Antes el anuncio se repetia cada ``enfriamiento_objeto_s`` mientras
    el objeto siguiera a la vista, y como ``_hablar`` bloquea el
    bucle, la voz repetida tapaba las indicaciones de la ruta.
    """
    print("\n[22] Obstaculos: un aviso por encuentro")
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    config = Configuracion()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(distancia_cm=350.0),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )
    # Los anuncios de obstaculos viven SOLO en la navegacion.
    aplicacion.estado = Estado.NAVEGANDO

    # El reloj real no llega a cumplir los enfriamientos de la
    # prueba: para simular "el frame estuvo limpio el tiempo
    # suficiente" se patea el contador de limpieza, y para probar el
    # anuncio en si el helper ``anunciar`` adelanta el silencio de la
    # voz (la prioridad de la guia se prueba en [24]).
    def frame_limpio() -> None:
        aplicacion._medio_libre_desde = (
            time.monotonic() - config.enfriamiento_objeto_s - 1.0
        )

    # 22.1 Sigue a la vista varias veces: NO se repite (eso era lo
    # que tapaba las indicaciones).
    detector.obstaculos = (ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),)
    for _ in range(3):
        anunciar(aplicacion, detector, 350.0)
    comprobar(
        sum("en medio del camino" in frase for frase in locutor.frases) == 1,
        "con el objeto siempre a la vista anuncia una sola vez",
    )

    # 22.2 Se fue el tiempo suficiente y volvio: ahi si se vuelve a
    # anunciar.
    detector.obstaculos = ()
    anunciar(aplicacion, detector, 350.0)   # empieza a contar limpio
    frame_limpio()                          # ...y ya paso el enfriamiento
    anunciar(aplicacion, detector, 350.0)   # re-armado
    locutor.frases.clear()
    detector.obstaculos = (ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),)
    anunciar(aplicacion, detector, 350.0)
    comprobar(
        any("en medio del camino" in frase for frase in locutor.frases),
        "cuando desaparece y vuelve se anuncia de nuevo",
    )

    # 22.3 Aparece un objeto NUEVO al lado del ya nombrado: se
    # anuncia enseguida, sin esperar a que se vaya el otro.
    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("mesa", EN_MEDIO_DEL_CAMINO),
    )
    locutor.frases.clear()
    anunciar(aplicacion, detector, 350.0)
    comprobar(
        any("mesa" in frase for frase in locutor.frases),
        "un obstaculo nuevo se anuncia sin esperar el enfriamiento",
    )
    comprobar(
        len(locutor.frases) == 1,
        "el nuevo entra en la misma frase, sin repetir la anterior",
    )

    # 22.4 Un frame limpio CORTO (un parpadeo de la camara) no
    # re-arma: hay que estar sin verlo el enfriamiento entero.
    detector.obstaculos = ()
    anunciar(aplicacion, detector, 350.0)
    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("mesa", EN_MEDIO_DEL_CAMINO),
    )
    anunciar(aplicacion, detector, 350.0)
    comprobar(
        len(locutor.frases) == 1,
        "un parpadeo de la camara no repite el anuncio",
    )


def test_pasos_sin_ritmo_se_descartan() -> None:
    """Una pausa no cuenta un movimiento suelto ni pierde la reanudacion.

    El primer pico despues de una pausa se guarda pendiente: si aparece
    otro pico de marcha, se acreditan ambos; si no, no se cuenta como
    paso. Asi se conserva el primer paso al reanudar tras esquivar un
    obstaculo sin adelantar el final del tramo por mover el pie parado.
    """
    print("\n[23] Pasos: el paso sin ritmo de marcha se descarta")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    config = Configuracion()
    pasos = movimiento.ContadorPasos(config)
    mpu = MPUFalso()

    def marchar(muestras: int) -> None:
        # Pico y reposo cada 50 muestras a dt = 0,01 s: 2 Hz, una
        # cadencia de 0,5 s, de marcha normal.
        for i in range(muestras):
            mpu.az = 1.6 if i % 50 < 25 else 1.0
            pasos.en_muestra(mpu.leer(), dt=0.01)

    def un_paso() -> None:
        # Media oscilacion de 0,25 s: tiene la forma (y la energia en
        # banda) de un paso real, asi que el contador la VE; lo que
        # decide despues es el ritmo, no la forma del pico.
        for i in range(50):
            mpu.az = 1.6 if i < 25 else 1.0
            pasos.en_muestra(mpu.leer(), dt=0.01)

    # En reposo la magnitud esta quieta: sin oscilacion no hay picos
    # que detectar (el pasa-banda no tiene entrada en continua).
    mpu.az = 1.0
    for _ in range(150):
        pasos.en_muestra(mpu.leer(), dt=0.01)

    # Marcha normal: los pasos cuentan.
    marchar(300)
    contados = pasos.pasos
    comprobar(
        contados >= 5,
        f"al caminar con ritmo cuenta los pasos ({contados})",
    )

    # Se para (a escuchar un aviso, a esquivar algo): 3 s sin marcha.
    mpu.az = 1.0
    for _ in range(300):
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(pasos.pasos == contados, "en la parada no inventa pasos")

    # Un pico aislado queda pendiente: no se acredita sin confirmar
    # que la marcha realmente se reanudo.
    un_paso()
    comprobar(
        pasos.pasos == contados,
        "un movimiento aislado tras la pausa queda pendiente, sin contar",
    )

    # Sigue la pausa: el candidato aislado no debe contaminar la nueva
    # marcha. Al detectar el segundo paso de la reanudacion, se acreditan
    # tanto el primero como el segundo.
    mpu.az = 1.0
    for _ in range(300):
        pasos.en_muestra(mpu.leer(), dt=0.01)
    marchar(150)
    comprobar(
        pasos.pasos >= contados + 2,
        "al reanudar, confirma y acredita el primer paso junto al segundo",
    )

    # El primer paso de un tramo nuevo siempre cuenta, aunque desde
    # el ultimo paso del tramo anterior haya pasado mucho.
    pasos.reiniciar()
    un_paso()
    comprobar(
        pasos.pasos == 1,
        "el primer paso de un tramo nuevo siempre cuenta",
    )


def test_obstaculos_esperan_su_turno() -> None:
    """La voz de la guia manda: el obstaculo espera su silencio.

    El asistente tiene UN solo canal de voz: si los avisos de objetos
    hablaban antes que las instrucciones, la ruta se atrasaba y hasta
    parecia cortada.  Ademas solo se anuncia lo que esta en la
    DIRECCION que se recorre: "persona" y los lados no frenan el paso.
    """
    print("\n[24] Obstaculos: esperan su turno (prioridad de la voz)")
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    config = Configuracion()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(distancia_cm=350.0),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )
    # Los anuncios de obstaculos viven SOLO en la navegacion.
    aplicacion.estado = Estado.NAVEGANDO

    # Objeto a la vista en varios fragmentos del frame.
    detector.obstaculos = (
        ObstaculoVisto("persona", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("maceta", "izquierda"),
    )

    # 24.1 La guia acabo de hablar (dijo los pasos): el aviso espera
    # su turno, aunque el objeto este a la vista en varios frames.
    aplicacion._ultima_voz_en = time.monotonic()
    for _ in range(3):
        aplicacion._anunciar_obstaculos(detector.analizar(None), 350.0)
    comprobar(
        len(locutor.frases) == 0,
        "con la voz recien usada el obstaculo no interrumpe",
    )

    # 24.2 Pasado el silencio: UNA sola frase y solo con lo que esta
    # en la direccion que se recorre (sin "persona", sin lados).
    aplicacion._ultima_voz_en = (
        time.monotonic() - config.silencio_para_obstaculos_s - 1.0
    )
    aplicacion._anunciar_obstaculos(detector.analizar(None), 350.0)
    comprobar(
        len(locutor.frases) == 1,
        "pasado el silencio anuncia una sola vez",
    )
    frase = locutor.frases[0]
    comprobar(
        "silla" in frase and "en medio del camino" in frase,
        "nombra lo que esta en medio del camino",
    )
    comprobar(
        "persona" not in frase,
        "persona no se anuncia: no bloquea el paso",
    )
    comprobar(
        "maceta" not in frase and "izquierda" not in frase,
        "los lados no se anuncian: estan fuera de la direccion que se recorre",
    )

    # 24.3 Sigue a la vista: no se repite (un aviso por encuentro).
    aplicacion._ultima_voz_en = (
        time.monotonic() - config.silencio_para_obstaculos_s - 1.0
    )
    aplicacion._anunciar_obstaculos(detector.analizar(None), 350.0)
    comprobar(
        len(locutor.frases) == 1,
        "mientras siga a la vista no repite el aviso",
    )

    # 24.4 Solo "persona" en el centro: no se dice nada ni con la
    # voz callada.
    locutor.frases.clear()
    aplicacion._medio_anunciado = ()
    detector.obstaculos = (ObstaculoVisto("persona", EN_MEDIO_DEL_CAMINO),)
    aplicacion._ultima_voz_en = (
        time.monotonic() - config.silencio_para_obstaculos_s - 1.0
    )
    aplicacion._anunciar_obstaculos(detector.analizar(None), 350.0)
    comprobar(
        len(locutor.frases) == 0,
        "solo 'persona' no anuncia nada",
    )


def test_fragmentos_de_la_imagen() -> None:
    """Los obstaculos solo existen en la navegacion y en el centro.

    Dando pasos solo se tienen en cuenta los objetos del fragmento
    central (los de los lados generaban confusion); girando para
    buscar la senal (o encarando) la camara NO anuncia obstaculo
    alguno: la persona gira sobre su propio eje.  La senal
    orientadora no se recorta: se sigue leyendo con los 3
    fragmentos.
    """
    print("\n[25] Obstaculos: solo en navegacion y en el centro")
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    # Con la voz de lados encendida se ve CLARO el efecto del
    # filtro: si el fragmento lateral contara en marcha, hablaria.
    config = dataclasses.replace(Configuracion(), anunciar_lados=True)
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(distancia_cm=350.0),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    comprobar(
        abs(config.obstaculo_centro_min - 1.0 / 3.0) < 1e-9
        and abs(config.obstaculo_centro_max - 2.0 / 3.0) < 1e-9,
        "el fragmento central es exactamente un tercio del ancho",
    )

    # 25.1 Dando pasos (NAVEGANDO): el del fragmento central se
    # anuncia; los de los lados no se tienen en cuenta.
    aplicacion.estado = Estado.NAVEGANDO
    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("maceta", "izquierda"),
    )
    anunciar(aplicacion, detector, 350.0)
    comprobar(
        any("silla" in frase for frase in locutor.frases),
        "dando pasos el fragmento central si se anuncia",
    )
    comprobar(
        not any("maceta" in frase for frase in locutor.frases),
        "dando pasos el fragmento izquierdo no se tiene en cuenta",
    )

    locutor.frases.clear()
    detector.obstaculos = (ObstaculoVisto("maceta", "derecha"),)
    anunciar(aplicacion, detector, 350.0)
    comprobar(
        len(locutor.frases) == 0,
        "dando pasos un objeto solo a la derecha no dice nada",
    )

    # 25.2 Buscando la senal o girando para encararla: la camara NO
    # anuncia obstaculo alguno (la persona gira sobre su propio eje,
    # no camina).  La senal si se sigue leyendo con los 3 fragmentos:
    # eso nunca se recorta.
    for estado in (Estado.BUSCAR_SENAL, Estado.GIRO_INICIAL, Estado.ENCARAR_SENAL):
        aplicacion.estado = estado
        locutor.frases.clear()
        detector.obstaculos = (
            ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
            ObstaculoVisto("maceta", "izquierda"),
        )
        anunciar(aplicacion, detector, 350.0)
        comprobar(
            len(locutor.frases) == 0,
            f"en {estado.name} no se anuncian obstaculos (no esta caminando)",
        )


def test_giro_compensado() -> None:
    """El corte del giro se adelanta: no hay desfase de mas.

    Al llegar al objetivo suena la frase final y luego la instruccion
    siguiente (ambas bloquean el bucle): mientras eso pasa la persona
    SIGUE GIRANDO, y eso dejaba el giro ~10 grados pasado del objetivo
    (90 cortaba en ~100). Ahora el corte va ``grados_compensacion_giro``
    antes (90 - 12 = 78) y "Puedes parar" sale PRIMERO en la frase,
    que es la orden que hay que oir antes.
    """
    print("\n[26] Giro: el corte se adelanta (sin desfase de mas)")
    config = Configuracion()
    mpu = MPUFalso()
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )
    microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)
    aplicacion.giro.reiniciar()
    mpu.gz = -100.0  # giro a derecha sostenido (~5 grados por paso)

    # 15 pasos de 0.05 s = ~75 grados: TODAVIA no corta (el corte
    # compensado va en 90 - 12 = 78).
    for _ in range(15):
        paso(aplicacion, detector, mpu, dt=0.05)
    comprobar(
        aplicacion.estado is Estado.GIRO_INICIAL,
        "a ~75 grados el giro sigue en curso (el corte es en 78)",
    )

    # Un paso mas cruza el corte: corta ANTES de los 90 grados.
    paso(aplicacion, detector, mpu, dt=0.05)
    comprobar(
        aplicacion.estado is Estado.BUSCAR_SENAL,
        "al cruzar el corte compensado termina el giro",
    )
    grados = aplicacion.giro.avanzar(derecha=True)
    comprobar(
        grados < 90.0,
        f"el corte ocurre antes de los 90 (corto en {grados:.1f})",
    )
    comprobar(
        "Puedes parar. Ya giraste 90 grados." in locutor.frases,
        "'Puedes parar' es la frase del corte, primero la orden",
    )
    comprobar(
        locutor.frases.index("Puedes parar. Ya giraste 90 grados.")
        < locutor.frases.index("Buscando la señal durante un minuto."),
        "la orden de parar sale antes que la instruccion siguiente",
    )


def test_llegada_por_ventana() -> None:
    """El tramo se cierra dentro de la ventana [70 %, 130 %].

    Los pasos son solo la estimacion: quien VERIFICA que el tramo
    termino es la camara con la senal interior de la zona de cierre
    (la siguiente del camino). El aviso de frenado puede adelantarse
    según la cadencia, pero la señal antes del 70 % no cierra el tramo;
    entre el 70 % y el 130 % sí lo cierra;
    pasados los 130 % sin senal se avisa y el flujo sigue como
    siempre.  Ademas ver la senal de cierre (la puerta enfrente, que
    puede aparecer en frames seguidos) NO es una desviacion: no debe
    salir "Te saliste de la ruta".
    """
    print("\n[27] Llegada por ventana: la camara cierra el tramo")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    aplicacion = Aplicacion(
        config=Configuracion(),
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=PlanificadorRutas(),
        ventana=VentanaFalsa(),
    )

    def caminar_hasta(pasos: int) -> None:
        """Da pasos rectos hasta acreditar ``pasos`` (o cambiar de estado)."""
        for i in range(400):
            mpu.az = 1.6 if i % 4 < 2 else 1.0
            paso(aplicacion, detector, mpu)
            if (
                aplicacion._pasos_acreditados >= pasos
                or aplicacion.estado is not Estado.NAVEGANDO
            ):
                return

    def empezar_tramo() -> None:
        aplicacion.ubicacion = "Inicio"
        aplicacion.lado_puerta = LADO_INTERIOR
        aplicacion.destino = "Sala"
        aplicacion._iniciar_navegacion(
            [Instruccion(Accion.CAMINAR, 10), Instruccion(Accion.LLEGADA)]
        )
        detector.senal = None
        mpu.gz = 0.0

    # Tramo de 10 pasos: la zona de cierre es la siguiente del camino.
    empezar_tramo()
    paso(aplicacion, detector, mpu)  # toma la instruccion CAMINAR
    comprobar(
        aplicacion._zona_cierre_tramo == "Cuarto",
        "la zona de cierre es la siguiente del camino",
    )

    # --- 27.1 Antes del 70 % la senal vista NO cierra el tramo -----
    caminar_hasta(5)  # 50 % de 10
    comprobar(aplicacion._pasos_acreditados == 5, "camina hasta el 50 % del tramo")
    detector.senal = Senal("Cuarto", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "al 50 % la senal de la zona de cierre no cierra el tramo",
    )
    comprobar(aplicacion._tramos_completados == 0, "el tramo sigue abierto")
    comprobar(
        not any("saliste de la ruta" in frase for frase in locutor.frases),
        "ver la senal de cierre no se lee como una desviacion",
    )

    # --- 27.2 Aviso anticipado según cadencia, sin cambiar el cierre --
    detector.senal = None
    locutor.frases.clear()
    aplicacion._distancia_tramo = 4.6
    aplicacion.pasos._cadencia_s = 0.4
    comprobar(
        0.54 <= aplicacion._umbral_aviso_llegada() < 0.70,
        "limita el aviso a un adelanto corto calculado por cadencia y frenado",
    )
    caminar_hasta(5)
    comprobar(
        not aplicacion._aviso_ventana_dado,
        "no adelanta el aviso más de lo configurado",
    )
    caminar_hasta(6)
    comprobar(
        aplicacion._aviso_ventana_dado,
        "estima reacción y frenado para avisar antes del 70 %",
    )
    comprobar(
        aplicacion.estado is Estado.NAVEGANDO,
        "el aviso anticipado no cierra el tramo",
    )
    locutor.frases.clear()
    caminar_hasta(9)
    comprobar(
        not any("Reduce el paso" in frase for frase in locutor.frases),
        "no emite locución de reducción de paso",
    )

    # --- 27.3 Entre el 70 % y el 130 % la camara CIERRA el tramo ---
    detector.senal = Senal("Cuarto", LADO_INTERIOR)
    preparar_distancia_puerta(aplicacion, 75.0)
    comprobar(
        aplicacion._senal_en_rango_de_llegada(
            detector.analizar(None), detector.senal
        ),
        "la señal centrada y la distancia normal cumplen proximidad",
    )
    resultado_senal = detector.analizar(None)
    aplicacion._distancia_puerta_estable_cm = 90.0
    comprobar(
        aplicacion._senal_en_rango_de_llegada(
            resultado_senal, detector.senal
        ),
        "acepta la señal centrada a más de 80 cm sin pedir acercamiento exacto",
    )
    aplicacion._distancia_puerta_estable_cm = None
    comprobar(
        aplicacion._senal_en_rango_de_llegada(
            resultado_senal, detector.senal
        ),
        "la cámara puede confirmar aunque el ultrasonido no tenga eco estable",
    )
    aplicacion._distancia_puerta_estable_cm = 44.0
    comprobar(
        not aplicacion._senal_en_rango_de_llegada(
            resultado_senal, detector.senal
        ),
        "mantiene el límite de seguridad cuando la persona está demasiado cerca",
    )
    aplicacion._distancia_puerta_estable_cm = 75.0
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._tramos_completados == 1,
        "la senal de la camara cierra el tramo dentro de la ventana",
    )
    comprobar(
        not any("saliste de la ruta" in frase for frase in locutor.frases),
        "la segunda vez que se ve la senal tampoco replanifica",
    )
    comprobar(
        any("Ya veo la señal" in frase for frase in locutor.frases),
        "anuncia que vio la senal y puede parar",
    )
    detector.senal = None
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "con el tramo cerrado pasa a confirmar la llegada del destino",
    )
    comprobar(
        not any("Has llegado" in frase for frase in locutor.frases),
        "sin la senal del destino no anuncia llegada",
    )

    # --- 27.4 Pasado el 130 % sin senal: aviso y flujo de siempre --
    empezar_tramo()
    aplicacion.config = dataclasses.replace(
        aplicacion.config, confirmacion_desvio_s=0.0
    )
    locutor.frases.clear()
    caminar_hasta(12)
    # Alimenta el último paso justo en la frontera, independientemente
    # de la cadencia sintética del contador en esta prueba.
    aplicacion.pasos._pasos = 13
    aplicacion._pasos_acreditados = 13
    aplicacion._pasos_previos = 13
    aplicacion.rumbo.girar(50.0)
    aplicacion._en_navegando(ResultadoVision())
    comprobar(
        any(
            "posiblemente has llegado" in frase.lower()
            for frase in locutor.frases
        ),
        "al completar los pasos previstos da una sola instrucción clara",
    )
    aplicacion._en_navegando(ResultadoVision())
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "el tramo cerrado pasa a confirmar la llegada con la camara",
    )
    comprobar(
        not any("Has llegado" in frase for frase in locutor.frases),
        "sin la senal del destino no anuncia llegada falsa",
    )
    comprobar(
        not any(
            "desviando" in frase or "Pasos completados" in frase
            for frase in locutor.frases
        ),
        "al cerrar el tramo no añade un aviso de desvío contradictorio",
    )


def test_zancada_adaptativa() -> None:
    """La zancada se mide en los tramos que cierra la camara.

    Los pasos del plan salen de ``tramo.pasos`` mientras nadie haya
    medido la zancada (es el conteo con el que se midio la casa).  En
    cuanto la camara cierra UN tramo dentro de la ventana, la zancada
    se mide (distancia del mapa / pasos contados) y a partir de ahi el
    plan recalcula ``round(distancia / zancada)``: el numero de pasos
    anunciado se acerca al real de la persona.  La medida se guarda
    como EMA (un tramo raro no descuadra el plan) y solo sirve el
    cierre VERIFICADO: el del 130 % sin senal no mide nada.
    """
    print("\n[28] Zancada adaptativa: medida en los tramos confirmados")
    config = Configuracion()

    # --- 28.1 El planificador usa la zancada solo si se la pasan ----
    tramos = {
        ("A", "B"): TramoRuta(pasos=10, giros=("izquierda",), distancia_m=5.0),
    }
    planificador = PlanificadorRutas(tramos)

    sin = planificador.planificar("A", "B")
    comprobar(
        sin is not None
        and sin[1].es_pasos
        and sin[1].cantidad == 10
        and sin[1].distancia_m == 5.0,
        "sin zancada el plan usa los pasos del mapa (y arrastra la distancia)",
    )

    con = planificador.planificar("A", "B", zancada_m=0.5)
    comprobar(
        con is not None and con[1].cantidad == 10,
        "con zancada calcula distancia / zancada (5.0 / 0.5 = 10)",
    )

    vuelta = planificador.planificar("B", "A", zancada_m=0.4)
    comprobar(
        vuelta is not None and vuelta[1].cantidad == 13,
        "5.0 / 0.4 = 12.5 redondea a 13 pasos (hacia arriba)",
    )
    comprobar(
        vuelta is not None and vuelta[1].distancia_m == 5.0,
        "el tramo al reves conserva la distancia medida",
    )
    comprobar(
        all(tramo.distancia_m > 0 for tramo in TRAMOS_DEMO.values()),
        "los tramos del mapa llevan su distancia medida",
    )

    # --- 28.2 La EMA mezcla la ultima medida con lo acumulado ------
    zancada = Zancada(
        inicial=config.zancada_m,
        alfa=config.zancada_ema_alfa,
        minimo=config.zancada_min_m,
        maximo=config.zancada_max_m,
    )
    comprobar(not zancada.aprendida, "sin ningun tramo medido la zancada no se ha usado")
    comprobar(
        zancada.medir(5.0, 10) is not None,
        "un tramo de 5 m en 10 pasos si se puede medir",
    )
    esperado = (
        config.zancada_ema_alfa * 0.5
        + (1.0 - config.zancada_ema_alfa) * config.zancada_m
    )
    comprobar(
        abs(zancada.metros - esperado) < 1e-9,
        "la EMA mezcla la medida (0.50) con el valor anterior",
    )
    comprobar(zancada.aprendida, "una medida valida marca la zancada como aprendida")

    zancada.medir(6.1, 100)  # 0.061 m/paso: imposible, se recorta
    comprobar(
        zancada.metros >= config.zancada_min_m,
        "una medida baja se recorta al minimo",
    )
    zancada.medir(19.7, 1)  # 19.7 m de un paso: imposible, se recorta
    comprobar(
        zancada.metros <= config.zancada_max_m,
        "una medida alta se recorta al maximo",
    )
    comprobar(
        zancada.medir(5.0, 0) is None and zancada.medir(0.0, 5) is None,
        "sin pasos o sin distancia no hay medida",
    )

    # --- 28.3 La aplicacion: el plan cambia tras un cierre real ----
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo
    detector = DetectorFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()
    mpu = MPUFalso()

    class PlanificadorEspia(PlanificadorRutas):
        """Guarda con que zancada se pidio cada plan."""

        def __init__(self, tramos=None):
            super().__init__(tramos)
            self.zancadas: list = []
            self.ultimo_plan = None

        def planificar(self, origen, destino, zancada_m=None):
            self.zancadas.append(zancada_m)
            self.ultimo_plan = super().planificar(origen, destino, zancada_m)
            return self.ultimo_plan

    planificador = PlanificadorEspia()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=mpu,
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=microfono,
        detector=detector,
        planificador=planificador,
        ventana=VentanaFalsa(),
    )
    aplicacion.ubicacion = "Inicio"
    aplicacion.lado_puerta = LADO_INTERIOR

    # Mientras no haya medida, el plan manda los pasos del mapa.
    aplicacion._asignar_destino("Sala")
    comprobar(
        planificador.zancadas[-1] is None,
        "sin zancada aprendida el plan no la usa",
    )
    camina = [i for i in planificador.ultimo_plan if i.es_pasos][0]
    comprobar(
        camina.cantidad == TRAMOS_DEMO[("Inicio", "Cuarto")].pasos,
        "el plan usa los pasos del mapa (11) mientras no haya medida",
    )

    def empezar_tramo(distancia: float, pasos: int) -> None:
        aplicacion.destino = "Sala"
        aplicacion.lado_puerta = LADO_INTERIOR
        aplicacion._iniciar_navegacion(
            [Instruccion(Accion.CAMINAR, pasos, distancia), Instruccion(Accion.LLEGADA)]
        )
        detector.senal = None
        mpu.gz = 0.0
        paso(aplicacion, detector, mpu)  # toma la instruccion CAMINAR

    def caminar_hasta(pasos: int) -> None:
        for i in range(400):
            mpu.az = 1.6 if i % 4 < 2 else 1.0
            paso(aplicacion, detector, mpu)
            if (
                aplicacion._pasos_acreditados >= pasos
                or aplicacion.estado is not Estado.NAVEGANDO
            ):
                return

    # --- 28.4 El cierre por pasos de mas NO mide nada --------------
    empezar_tramo(distancia=6.1, pasos=10)
    caminar_hasta(14)  # 130 % sin senal: cierra igual, pero sin verificar
    comprobar(
        aplicacion.estado is Estado.CONFIRMANDO_LLEGADA,
        "el tramo de mas se cierra por el flujo de siempre",
    )
    comprobar(
        not aplicacion.zancada.aprendida,
        "el cierre sin la senal no mide la zancada (no se sabe donde acabo)",
    )

    # --- 28.5 Con la camara la zancada si se mide ------------------
    empezar_tramo(distancia=6.1, pasos=10)
    caminar_hasta(8)  # 80 %: dentro de la ventana
    detector.senal = Senal("Cuarto", LADO_INTERIOR)
    preparar_distancia_puerta(aplicacion, 75.0)
    paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._tramos_completados == 1,
        "la camara cierra el tramo dentro de la ventana",
    )
    comprobar(aplicacion.zancada.aprendida, "ese cierre mide la zancada")
    esperado = (
        config.zancada_ema_alfa * (6.1 / 8)
        + (1.0 - config.zancada_ema_alfa) * config.zancada_m
    )
    comprobar(
        abs(aplicacion.zancada.metros - esperado) < 1e-9,
        "la medida del tramo entra en la EMA (6.1 m / 8 pasos)",
    )

    # --- 28.6 El plan siguiente usa la zancada medida --------------
    detector.senal = None
    aplicacion.ubicacion = "Cuarto"  # ahi dejo la senal de cierre
    aplicacion.lado_puerta = LADO_INTERIOR
    aplicacion._asignar_destino("Baño")
    comprobar(
        isinstance(planificador.zancadas[-1], float),
        "con zancada aprendida el plan si la usa",
    )
    ultimos = [i for i in planificador.ultimo_plan if i.es_pasos]
    larga = ultimos[-1]
    zancada_medida = planificador.zancadas[-1]
    distancia_sala_bano = TRAMOS_DEMO[("Sala", "Baño")].distancia_m
    comprobar(
        larga.cantidad == int(distancia_sala_bano / zancada_medida + 0.5),
        "el tramo largo pasa a calcular distancia / zancada",
    )
    comprobar(
        larga.cantidad != TRAMOS_DEMO[("Sala", "Baño")].pasos,
        f"y ya no es el conteo viejo del mapa ({TRAMOS_DEMO[('Sala', 'Baño')].pasos} pasos)",
    )


def test_detector_de_pasos() -> None:
    """La banda del paso acepta marcha lenta, rapida y suave.

    El detector no compara contra una media que se escapa de la
    postura: filtra en banda (0,5 - 5 Hz), pone el umbral en la mitad
    de los picos REALES de la marcha y solo descarta picos demasiado
    cercanos al ritmo medido. Con senales sinteticas se comprueba que las tres
    marchas tipicas cuentan (un paso cada 1 s, cada 0,25 s y un
    balanceo suave de 0,12 g), que el ruido fuera de banda no cuenta,
    que el umbral sube con la marcha vigorosa, que los cambios de ritmo
    no pierden pasos y que una pausa larga no cuenta un movimiento suelto.
    """
    print("\n[29] Pasos: banda del paso, umbral adaptativo y ritmo rebasado")
    config = Configuracion()

    def marcha(periodo: int, muestras: int):
        """Contador nuevo con ``muestras`` a dt = 0,01 s y ciclo de ``periodo``.

        El ciclo es medio periodo en alto (el pico del paso) y medio
        en bajo (el reposo), como la oscilacion de la acelerometro
        al apoyar un pie.
        """
        pasos = movimiento.ContadorPasos(config)
        mpu = MPUFalso()
        for i in range(muestras):
            mpu.az = 1.6 if i % periodo < periodo // 2 else 1.0
            pasos.en_muestra(mpu.leer(), dt=0.01)
        return pasos

    # --- 29.1 Marcha lenta: un paso cada segundo -------------------
    # 100 muestras de periodo = 1 Hz, comodamente por encima del
    # minimo de banda (0,5 Hz) y con el intervalo por debajo del
    # piso de "sin marcha" (1,5 s).
    lenta = marcha(100, 700)
    comprobar(lenta.pasos >= 5, f"la marcha lenta (1 s/paso) cuenta ({lenta.pasos})")

    # --- 29.2 Marcha rapida: un paso cada 0,36 s ------------------
    # 36 muestras de periodo = 2,78 Hz: aun dentro de la banda (5 Hz) y
    # por encima del periodo refractario (0,34 s).
    rapida = marcha(36, 360)
    comprobar(rapida.pasos >= 8, f"la marcha rapida (0,36 s/paso) cuenta ({rapida.pasos})")
    comprobar(
        rapida._umbral(0.0) > config.umbral_paso_min_g,
        "el umbral sube con los picos reales de la marcha vigorosa",
    )

    # --- 29.3 Marcha suave: balanceo de 0,12 g --------------------
    # Seno continuo (no un cuadrado): es el vaiven suave de la
    # cintura.  Su pico filtrado ronda el umbral minimo, pero lo
    # cuenta sin tocar la configuracion.
    suave = movimiento.ContadorPasos(config)
    mpu = MPUFalso()
    for i in range(500):  # 2 Hz, amplitud 0,12 g
        mpu.az = 1.0 + 0.12 * math.sin(2.0 * math.pi * i * 0.01 / 0.5)
        suave.en_muestra(mpu.leer(), dt=0.01)
    comprobar(suave.pasos >= 8, f"la marcha suave (0,12 g) cuenta ({suave.pasos})")

    # --- 29.3b El filtro tolera jitter real de muestreo ------------
    # La lectura I2C hace que los intervalos reales no sean idénticos.
    # Alternar 11,8/12,2 ms simula la tasa de ~84 Hz de la Raspberry Pi.
    variable = movimiento.ContadorPasos(config)
    mpu = MPUFalso()
    tiempo = 0.0
    indice = 0
    while tiempo < 8.0:
        dt = 0.0118 if indice % 2 == 0 else 0.0122
        tiempo += dt
        mpu.az = 1.0 + 0.18 * math.sin(2.0 * math.pi * 2.0 * tiempo)
        variable.en_muestra(mpu.leer(), dt=dt)
        indice += 1
    comprobar(
        variable.pasos >= 10,
        f"la marcha a 2 Hz con jitter de muestreo cuenta ({variable.pasos})",
    )

    # --- 29.4 Ruido fuera de banda no cuenta -----------------------
    # Tres muestras de 20 Hz (periodo de 5): muy por encima de los
    # 5 Hz de la banda, el pasa-banda las atenua y no hay cruce.
    fuera = movimiento.ContadorPasos(config)
    mpu = MPUFalso()
    for i in range(300):
        mpu.az = 1.6 if i % 5 < 1 else 1.0
        fuera.en_muestra(mpu.leer(), dt=0.01)
    comprobar(fuera.pasos == 0, "el temblor a 20 Hz (fuera de banda) no cuenta")

    # --- 29.5 La cadencia admite cambios de ritmo -----------------
    pasos = movimiento.ContadorPasos(config)
    mpu = MPUFalso()
    for i in range(350):  # marcha a 0,5 s: el ritmo queda medido
        mpu.az = 1.6 if i % 50 < 25 else 1.0
        pasos.en_muestra(mpu.leer(), dt=0.01)
    contados = pasos.pasos
    comprobar(contados >= 5, f"la marcha a 0,5 s cuenta ({contados})")

    for i in range(500):  # cambio a paso lento: 1 s por paso
        mpu.az = 1.6 if i % 100 < 50 else 1.0
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        pasos.pasos >= contados + 4,
        f"el cambio a marcha lenta no pierde pasos ({pasos.pasos - contados})",
    )

    contados = pasos.pasos
    for i in range(240):  # acelera a 0,4 s por paso
        mpu.az = 1.6 if i % 40 < 20 else 1.0
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        pasos.pasos >= contados + 5,
        f"el cambio a marcha rapida no pierde pasos ({pasos.pasos - contados})",
    )

    # Una pausa larga sigue descartando el primer movimiento suelto.
    contados = pasos.pasos
    mpu.az = 1.0
    for _ in range(200):
        pasos.en_muestra(mpu.leer(), dt=0.01)
    for i in range(100):
        mpu.az = 1.6 if i < 25 else 1.0
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        pasos.pasos == contados,
        "un movimiento suelto tras una pausa larga no cuenta",
    )


def test_evaluacion_offline_pasos() -> None:
    """El evaluador reproduce las muestras del MPU registradas en CSV."""
    print("\n[30] Registro CSV y evaluación de pasos offline")
    config = Configuracion()
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "caminata.csv"
        with ruta.open("w", newline="", encoding="utf-8") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(
                ("t", "ax", "ay", "az", "gx", "gy", "gz", "distancia_ultrasonido")
            )
            for i in range(600):
                az = 1.6 if i % 100 < 50 else 1.0
                escritor.writerow(
                    (i * 0.01, 0.0, 0.0, az, 0.0, 0.0, 0.0, "")
                )

        resultado = evaluar_csv(ruta, pasos_reales=6, config=config)
    comprobar(
        resultado.pasos_detectados > 0,
        "el evaluador detecta pasos reproduciendo aceleraciones registradas",
    )
    comprobar(
        resultado.pasos_reales == 6
        and resultado.error_porcentual
        == abs(resultado.pasos_detectados - 6) / 6 * 100.0,
        "reporta los pasos reales y el error porcentual absoluto",
    )


def test_registro_sensores_con_aplicacion() -> None:
    """El registro en vivo conserva el formato que usa el evaluador."""
    print("\n[30.1] Registro en vivo: MPU y ultrasonido en CSV")
    sensor = SensorFalso(distancia_cm=None)
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "subcarpeta" / "caminata.csv"
        mpu = MPUFalso()
        aplicacion = Aplicacion(
            config=Configuracion(),
            camara=CamaraFalsa(),
            mpu=mpu,
            sensor=sensor,
            alarma=AlarmaFalsa(),
            locutor=LocutorFalso(),
            microfono=MicrofonoFalso(),
            detector=DetectorFalso(),
            ventana=VentanaFalsa(),
            registro_caminata=ruta,
        )
        aplicacion.muestreador.muestrear(
            LecturaMPU(0.1, -0.2, 0.95, 1.0, 2.0, -3.0), 0.01
        )
        sensor.distancia_cm = 73.5
        aplicacion.muestreador.muestrear(
            LecturaMPU(0.1, -0.2, 0.95, 1.0, 2.0, -3.0), 0.01
        )
        aplicacion.cerrar()

        with ruta.open(newline="", encoding="utf-8") as archivo:
            filas = list(csv.DictReader(archivo))
    comprobar(
        len(filas) == 2
        and tuple(filas[0]) == (
            "t",
            "ax",
            "ay",
            "az",
            "gx",
            "gy",
            "gz",
            "distancia_ultrasonido",
        ),
        "guarda todas las columnas esperadas por evaluar_pasos.py",
    )
    comprobar(
        float(filas[0]["az"]) == 0.95
        and filas[0]["distancia_ultrasonido"] == ""
        and filas[1]["distancia_ultrasonido"] == "73.5",
        "registra el MPU y la distancia vigente sin abrir otro sensor",
    )


def test_distancia_visual_y_mediana() -> None:
    """La distancia por tamaño se calcula y suaviza por clase."""
    print("\n[31] Distancia visual: tamaño de caja y mediana de frames")
    deteccion = Deteccion(
        "cow", 0.9, (20, 10, 80, 110), True, False
    )
    comprobar(
        deteccion.alto_px == 100
        and deteccion.ancho_px == 60
        and deteccion.centro_x_px == 50,
        "la caja expone alto, ancho y centro horizontal en píxeles",
    )
    comprobar(
        distancia_por_altura(500.0, 0.2, 100) == 1.0,
        "distancia pinhole d=f·H/h da 1 m",
    )
    comprobar(
        distancia_por_altura(0.0, 0.2, 100) is None,
        "no devuelve distancias si la focal no está calibrada",
    )
    suavizador = MedianaDistanciasVisuales(frames=3, perdidas=1)
    medidas = [suavizador.suavizar("cow", valor) for valor in (1.0, 2.0, 9.0)]
    comprobar(
        medidas == [1.0, 1.5, 2.0],
        "la salida visual usa la mediana de los últimos tres frames",
    )
    suavizador.cerrar_frame(set())
    suavizador.cerrar_frame(set())
    comprobar(
        suavizador.suavizar("cow", 4.0) == 4.0,
        "un historial visual obsoleto no contamina una detección nueva",
    )


def test_odometro_ultrasonico_estable() -> None:
    """El odómetro solo acepta medianas con varias lecturas estables."""
    print("\n[32] Odómetro ultrasónico: estabilidad y rechazo de saltos")
    config = dataclasses.replace(
        Configuracion(),
        muestras_ultrasonido_odometro=3,
        salto_maximo_ultrasonido_cm=20.0,
    )
    odometro = OdometroUltrasonico(config)
    comprobar(odometro.observar(200.0) is None, "espera las primeras lecturas")
    comprobar(odometro.observar(198.0) is None, "acumula la ventana estable")
    comprobar(odometro.observar(196.0) == 198.0, "entrega la mediana de 3 medidas")
    comprobar(
        odometro.observar(350.0) is None,
        "rechaza un salto brusco sin contaminar la ventana",
    )
    comprobar(
        odometro.observar(194.0) == 196.0,
        "continúa con lecturas válidas tras el salto",
    )
    odometro.reiniciar()
    for distancia in (200.0, 198.0, 201.0):
        odometro.observar(distancia)
    comprobar(
        odometro.observar(75.0) is None,
        "un cambio de rango brusco no se acepta con una sola lectura",
    )
    comprobar(
        odometro.observar(77.0) is None,
        "confirma el nuevo rango con lecturas repetidas",
    )
    comprobar(
        odometro.observar(74.0) == 75.0,
        "la mediana se recupera al cambiar de distancia real",
    )
    odometro.reiniciar()
    comprobar(
        odometro.observar(None) is None,
        "la ausencia de eco reinicia la ventana estable",
    )
    mapa = PlanificadorRutas(
        {
            ("A", "B"): TramoRuta(
                pasos=4,
                giros=("izquierda",),
                distancia_m=2.0,
                distancia_frontal_fin_cm=40.0,
                puertas_en_tramo=2,
            )
        }
    )
    ida = mapa.planificar("A", "B")
    vuelta = mapa.planificar("B", "A")
    comprobar(
        ida is not None
        and vuelta is not None
        and ida[1].distancia_frontal_fin_cm == 40.0
        and vuelta[1].distancia_frontal_fin_cm == 40.0
        and ida[1].puertas_en_tramo == vuelta[1].puertas_en_tramo == 2,
        "las referencias opcionales acompañan el tramo en ambos sentidos",
    )
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(200.0),
        alarma=AlarmaFalsa(),
        locutor=LocutorFalso(),
        microfono=MicrofonoFalso(),
        detector=DetectorFalso(),
        planificador=mapa,
        ventana=VentanaFalsa(),
    )
    aplicacion._distancia_tramo = 5.0
    aplicacion._instruccion_actual = ida[1]
    for _ in range(3):
        aplicacion.sensor.secuencia_medicion += 1
        aplicacion._actualizar_odometro_ultrasonico(ida[1])
    comprobar(
        aplicacion._distancia_frontal_inicio_cm == 200.0
        and aplicacion._avance_ultrasonico_m == 0.0,
        "fija el origen solo tras tres lecturas frontales estables",
    )
    aplicacion.sensor.distancia_cm = 180.0
    for _ in range(2):
        aplicacion.sensor.secuencia_medicion += 1
        aplicacion._actualizar_odometro_ultrasonico(ida[1])
    comprobar(
        aplicacion._avance_ultrasonico_m == 0.2,
        "usa d_inicio - d_actual como avance tras estabilizar la lectura",
    )


def test_estimador_posicion_tramo() -> None:
    """El filtro combina pasos y mediciones y propaga incertidumbre."""
    print("\n[33] Estimador 1D de posición e incertidumbre")
    config = Configuracion()
    estimador = EstimadorPosicionTramo(config)
    estimador.reiniciar(5.0)
    inicial = estimador.incertidumbre_m
    estimador.predecir(4, 0.5)
    comprobar(
        estimador.posicion_m == 2.0
        and estimador.incertidumbre_m > inicial,
        "predice metros por paso y aumenta la incertidumbre",
    )
    varianza_antes = estimador.varianza_m2
    comprobar(
        estimador.corregir(3.0, config.varianza_medicion_senal_m2)
        and estimador.posicion_m > 2.0
        and estimador.varianza_m2 < varianza_antes,
        "la medición visual corrige posición y reduce incertidumbre",
    )
    estimador.corregir(100.0, config.varianza_medicion_ultrasonido_m2)
    comprobar(
        estimador.posicion_m == 5.0,
        "limita la posición estimada al largo del tramo",
    )
    comprobar(
        not estimador.corregir(2.0, 0.0),
        "rechaza mediciones con varianza inválida",
    )
    locutor = LocutorFalso()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=MicrofonoFalso(),
        detector=DetectorFalso(),
        ventana=VentanaFalsa(),
    )
    aplicacion._distancia_tramo = 5.0
    aplicacion._zona_cierre_tramo = "Cuarto"
    aplicacion.estimador_posicion.reiniciar(5.0)
    aplicacion.estimador_posicion.predecir(6, 0.5)
    aplicacion._valorar_llegada_del_tramo(ResultadoVision(), dados=0, objetivo=10)
    comprobar(
        any("Reduce el paso" in frase for frase in locutor.frases)
        and aplicacion._tramos_completados == 0,
        "la incertidumbre puede iniciar búsqueda, pero no cerrar el tramo",
    )

    senal_cierre = Senal("Cuarto", LADO_INTERIOR)
    resultado = ResultadoVision(
        senal=senal_cierre,
        detecciones=(
            Deteccion("sheep", 0.9, (250, 100, 390, 300), True, False, 1.0),
        ),
    )
    aplicacion._actualizar_posicion_tramo(
        resultado,
        Instruccion(Accion.CAMINAR, 10, distancia_m=5.0),
        dados=0,
    )
    preparar_distancia_puerta(aplicacion, 75.0)
    aplicacion._valorar_llegada_del_tramo(resultado, dados=0, objetivo=10)
    comprobar(
        aplicacion._tramos_completados == 1
        and not any("Has llegado" in frase for frase in locutor.frases),
        "la medición visual corrige posición y la señal cierra sin falsear llegada",
    )


def test_deteccion_y_seguimiento_de_puertas() -> None:
    """Las puertas guían y corrigen avance, pero no identifican zonas."""
    print("\n[34] Puertas: detección, guía única y conteo por posición")

    class CajaFalsaVision:
        def __init__(self, clase: int, confianza: float, caja: tuple[int, ...]):
            self.cls = [clase]
            self.conf = [confianza]
            self.xyxy = [caja]

    class ModeloFalsoVision:
        names = {0: "door", 1: "sheep", 2: "cow"}

        def __init__(self) -> None:
            self.cajas = []

        def __call__(self, frame, **kwargs):
            return [types.SimpleNamespace(boxes=self.cajas)]

    config = dataclasses.replace(
        Configuracion(),
        focal_px=1000.0,
        distancia_mediana_frames=3,
        detecciones_senal_requeridas=1,
        frames_puerta_perdida=1,
    )
    modelo = ModeloFalsoVision()
    detector = DetectorVisual.__new__(DetectorVisual)
    detector._config = config
    detector._senales = SENALES_ORIENTADORAS
    detector._objetos = OBJETOS_PERMITIDOS
    detector._puertas = PUERTAS
    detector._distancias = MedianaDistanciasVisuales(
        config.distancia_mediana_frames,
        config.distancia_mediana_perdidas,
    )
    detector._confirmador = ConfirmadorSenal(config)
    detector._modelo = modelo

    resultado = None
    for alto in (400, 200, 100):
        modelo.cajas = [
            CajaFalsaVision(0, 0.9, (200, 0, 440, alto)),
            CajaFalsaVision(1, 0.7, (250, 20, 300, 40)),
            CajaFalsaVision(2, 0.99, (450, 100, 500, 200)),
        ]
        resultado = detector.analizar(object())
    comprobar(
        resultado is not None
        and len(resultado.puertas) == 1
        and resultado.puertas[0].centro_x_px == 320
        and resultado.puertas[0].alto_px == 100
        and resultado.puertas[0].ancho_px == 240,
        "devuelve caja, centro, alto y ancho de la puerta",
    )
    comprobar(
        resultado is not None
        and abs(resultado.puertas[0].distancia_m - 10.0) < 1e-9,
        "suaviza distancia a puerta con mediana de frames",
    )
    comprobar(
        resultado is not None
        and resultado.senal == Senal("Cuarto", LADO_INTERIOR),
        "prioriza el animal dentro de la puerta sobre otra señal exterior",
    )
    modelo.cajas = [CajaFalsaVision(0, 0.9, (10, 0, 150, 460))]
    lateral = detector.analizar(object())
    comprobar(
        lateral.puertas[0].posicion == "izquierda" and lateral.senal is None,
        "clasifica una puerta lateral en el mismo fragmento de imagen",
    )

    contador = ContadorPuertas(config)
    comprobar(
        contador.actualizar(((320.0, True),)) == 1,
        "cuenta una puerta centrada al cruzar el fragmento central",
    )
    comprobar(
        contador.actualizar(((320.0, True),)) == 1,
        "frames repetidos de la misma puerta no duplican el conteo",
    )
    contador.reiniciar()
    comprobar(
        contador.actualizar(((100.0, False),)) == 0,
        "una puerta que permanece a un lado no se cuenta como pasada",
    )

    app_conteo = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=LocutorFalso(),
        microfono=MicrofonoFalso(),
        detector=DetectorFalso(),
        ventana=VentanaFalsa(),
    )
    app_conteo._distancia_tramo = 6.0
    app_conteo._zona_cierre_tramo = "Cuarto"
    app_conteo.estimador_posicion.reiniciar(6.0)
    instruccion = Instruccion(
        Accion.CAMINAR, 10, distancia_m=6.0, puertas_en_tramo=1
    )
    puerta_central = PuertaVisto(
        "puerta", (200, 0, 440, 460), 320.0, 460, 240, "centro", None
    )
    resultado_puerta = ResultadoVision(puertas=(puerta_central,))
    app_conteo._actualizar_posicion_tramo(resultado_puerta, instruccion, 0)
    posicion_tras_puerta = app_conteo.estimador_posicion.posicion_m
    app_conteo._actualizar_posicion_tramo(resultado_puerta, instruccion, 0)
    comprobar(
        app_conteo._contador_puertas.pasadas == 1
        and app_conteo.estimador_posicion.posicion_m == posicion_tras_puerta,
        "el conteo de puerta corrige avance una vez sin acumular duplicados",
    )
    app_conteo._valorar_llegada_del_tramo(
        resultado_puerta, dados=8, objetivo=10
    )
    comprobar(
        app_conteo._tramos_completados == 0
        and not any(
            "Has llegado" in frase for frase in app_conteo.locutor.frases
        ),
        "ni una puerta centrada confirma el tramo o la llegada",
    )

    locutor = LocutorFalso()
    aplicacion = Aplicacion(
        config=config,
        camara=CamaraFalsa(),
        mpu=MPUFalso(),
        sensor=SensorFalso(),
        alarma=AlarmaFalsa(),
        locutor=locutor,
        microfono=MicrofonoFalso(),
        detector=DetectorFalso(),
        ventana=VentanaFalsa(),
    )
    aplicacion._guiar_con_puerta(lateral)
    aplicacion._guiar_con_puerta(lateral)
    comprobar(
        len(locutor.frases) == 1
        and "izquierda" in locutor.frases[0],
        "anuncia una sola vez la posición de puerta y guía el giro",
    )
    aplicacion._guiar_con_puerta(ResultadoVision())
    aplicacion._guiar_con_puerta(ResultadoVision())
    aplicacion._ultima_voz_en = (
        time.monotonic() - config.silencio_para_obstaculos_s - 1
    )
    aplicacion._guiar_con_puerta(lateral)
    comprobar(
        len(locutor.frases) == 2,
        "vuelve a guiar solo después de que la puerta desaparece",
    )


def main() -> int:
    # Salida legible con acentos en Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("PRUEBA DEL ASISTENTE DE ORIENTACION")
    print("=" * 60)
    try:
        test_fps_camara()
        test_comandos_de_voz()
        test_planificacion_de_rutas()
        test_flujo_completo()
        test_destino_sin_ubicacion()
        test_giro_integra_velocidad_angular()
        test_giro_al_lado_equivocado_se_descuenta()
        test_deteccion_de_caida()
        test_proximidad_se_acelera()
        test_confirmacion_de_senal_tolerante()
        test_pasos_sin_fantasmas()
        test_encarada_y_desvio()
        test_lados_de_puerta()
        test_relocalizacion_y_devolucion()
        test_encarada_por_sentido()
        test_obstaculos_en_medio()
        test_encarada_intermedia_de_tramo()
        test_encarada_automatica()
        test_obstaculos_sincronizados()
        test_pasos_con_media_cambiada()
        test_rumbo_ignora_balanceo()
        test_sesgo_corregido_en_marcha()
        test_rumbo_resiste_aceleracion_de_pasos()
        test_desvio_requiere_confirmacion()
        test_obstaculos_un_aviso_por_encuentro()
        test_pasos_sin_ritmo_se_descartan()
        test_obstaculos_esperan_su_turno()
        test_fragmentos_de_la_imagen()
        test_giro_compensado()
        test_llegada_por_ventana()
        test_zancada_adaptativa()
        test_detector_de_pasos()
        test_evaluacion_offline_pasos()
        test_registro_sensores_con_aplicacion()
        test_distancia_visual_y_mediana()
        test_odometro_ultrasonico_estable()
        test_estimador_posicion_tramo()
        test_deteccion_y_seguimiento_de_puertas()
    except AssertionError as error:
        print(f"\nFALLO: {error}")
        return 1

    print("\n" + "=" * 60)
    print("TODAS LAS PRUEBAS PASARON")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
