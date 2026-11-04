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
import threading
import time
import types
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
_instalar_si_falta("ultralytics", YOLO=_CualquierCosa)
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
from guia.dominio.catalogo import LADO_INTERIOR, LADO_PASILLO, Senal  # noqa: E402
from guia.dominio.estados import Estado  # noqa: E402
from guia.dominio.rutas import Accion, Instruccion, PlanificadorRutas, TRAMOS_DEMO  # noqa: E402
from guia.hardware.mpu6050 import LecturaMPU  # noqa: E402
from guia.hardware.ultrasonido import intervalo_de_beep  # noqa: E402
from guia.navegacion import movimiento as movimiento  # noqa: E402
from guia.navegacion.caida import DetectorCaida  # noqa: E402
from guia.navegacion.rumbo import Rumbo  # noqa: E402
from guia.vision.detector import (  # noqa: E402
    EN_MEDIO_DEL_CAMINO,
    ConfirmadorSenal,
    ObstaculoVisto,
    ResultadoVision,
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
        # Por defecto en rango de alerta (200 cm): los avisos de
        # obstaculo se prueban con el eco presente.
        self.distancia_cm = distancia_cm
        self.distancia_alerta_cm = 200.0


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

    def analizar(self, frame) -> ResultadoVision:
        return ResultadoVision(senal=self.senal, obstaculos=self.obstaculos)

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

    Como en el sistema real, primero se muestrea el MPU6050 y luego se
    atiende voz, caidas, obstaculos y el estado actual.
    """
    frame = aplicacion.camara.capturar()
    resultado = detector.analizar(frame)
    aplicacion.muestreador.muestrear(mpu.leer(), dt)
    aplicacion._procesar_voz()
    aplicacion._vigilar_caida()
    aplicacion._anunciar_obstaculos(resultado, aplicacion.sensor.distancia_cm)
    aplicacion._manejadores[aplicacion.estado](aplicacion, resultado)


def comprobar(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)
    print(f"  OK  {mensaje}")


# ----------------------------------------------------------------------
# Pruebas
# ----------------------------------------------------------------------
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
        "Ya giraste 90 grados. Puedes parar." in locutor.frases,
        "avisa cuando parar el giro de 90",
    )
    comprobar(
        any("Vas por la mitad" in frase for frase in locutor.frases),
        "da avance del giro mientras gira",
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
    # Al caminar la magnitud *oscila* (pico, reposo, pico...): el
    # contador la detecta por cruce de la linea base, no por nivel.
    # El tramo completo Sala -> Baño: cada pico vale un paso, asi
    # que hacen falta ~2 vueltas por paso.
    mpu.gz = 0.0
    for i in range(110):
        mpu.az = 1.6 if i % 2 else 1.0
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

    # El acelerometro sin calibrar se sostiene en 1.15 g: quieto no
    # debe contar un paso cada 0.35 segundos para siempre.
    mpu.az = 1.15
    for _ in range(400):  # 4 segundos de reposo fingido
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        pasos.pasos == 0,
        "con la magnitud alta sostenida no cuenta pasos fantasma",
    )

    # Al caminar la magnitud oscila (pico y reposo): ahi si cuenta.
    pasos.reiniciar()
    for i in range(400):
        mpu.az = 1.6 if i % 4 < 2 else 1.0
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
    locutor.frases.clear()
    mpu.gz = -60.0
    for i in range(8):
        mpu.az = 1.6 if i % 2 else 1.0
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
        mpu.az = 1.6 if i % 2 else 1.0
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
        any("mitad del giro" in frase for frase in locutor.frases),
        "da avances mientras corrige el rumbo",
    )
    mpu.gz = 0.0
    # El tramo completo Sala -> Baño (dos vueltas por paso).
    for i in range(110):
        mpu.az = 1.6 if i % 2 else 1.0
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is Estado.CONFIRMANDO_LLEGADA:
            break
    comprobar(
        any("Pasos completados" in frase for frase in locutor.frases),
        "tras enderezar los pasos vuelven a contar",
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
    detector.senal = None
    mpu.gz = 0.0
    for i in range(40):
        mpu.az = 1.6 if i % 2 else 1.0
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

    # 12.2 La senal del destino a la vista: ahi si llegada verificada.
    detector.senal = Senal("Baño", LADO_INTERIOR)
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "la senal confirma la llegada")
    comprobar(aplicacion.ubicacion == "Baño", "ubicacion actualizada al llegar")
    comprobar("Has llegado a Baño." in locutor.frases, "anuncio de llegada verificada")

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
    for i in range(10):
        mpu.az = 1.6 if i % 2 else 1.0
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
    # 12 pares de ruta).  Las imagenes estan del lado derecho del
    # pasillo al recorrerla de Inicio al Bano: todo viaje que avanza
    # hacia el Bano encara a la DERECHA y todo viaje que regresa
    # hacia el Inicio (mas lejano -> mas cercano) encara a la
    # IZQUIERDA.
    orden = ["Inicio", "Cuarto", "Sala", "Baño"]
    malos: list[str] = []
    for origen in orden:
        for destino in orden:
            if origen == destino:
                continue
            esperado = (
                "derecha"
                if orden.index(destino) > orden.index(origen)
                else "izquierda"
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
        planificador.lado_encarada("Sala", "Sala") == "derecha",
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
        any("Gira despacio a la derecha" in frase for frase in locutor.frases),
        "de Inicio a Cuarto pide girar a la derecha",
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
        any("Gira despacio a la izquierda" in frase for frase in locutor.frases),
        "del Bano a la Sala pide girar a la izquierda",
    )
    comprobar(
        not any("Gira despacio a la derecha" in frase for frase in locutor.frases),
        "ya no da la indicacion del sentido normal",
    )


def test_obstaculos_en_medio() -> None:
    """Los objetos se anuncian nombrados y con la posicion en la que estan."""
    print("\n[14] Obstaculos: en medio del camino y a los lados")
    config = Configuracion()

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

    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("maceta", "izquierda"),
    )
    aplicacion._anunciar_obstaculos(
        detector.analizar(None), aplicacion.sensor.distancia_cm
    )
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

    # Varias en el centro: una sola frase con todos los nombres.
    locutor.frases.clear()
    detector.obstaculos = (
        ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),
        ObstaculoVisto("mesa", EN_MEDIO_DEL_CAMINO),
    )
    aplicacion._anunciar_obstaculos(
        detector.analizar(None), aplicacion.sensor.distancia_cm
    )
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
    detector.obstaculos = (ObstaculoVisto("silla", EN_MEDIO_DEL_CAMINO),)
    aplicacion._anunciar_obstaculos(detector.analizar(None), 350.0)
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
    aplicacion._anunciar_obstaculos(detector.analizar(None), 120.0)
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
    aplicacion._anunciar_obstaculos(detector.analizar(None), 80.0)
    comprobar(
        any("Hay algo enfrente" in frase for frase in locutor.frases),
        "sin objeto a la vista explica lo que significan los pitidos",
    )
    locutor.frases.clear()
    aplicacion._anunciar_obstaculos(detector.analizar(None), 75.0)
    comprobar(
        len(locutor.frases) == 0,
        "siguiendo en rango no repite la explicacion cada vuelta",
    )

    # 16.4 Se aleja del obstaculo: se rearmar para el proximo acercamiento.
    aplicacion._anunciar_obstaculos(detector.analizar(None), 350.0)
    aplicacion._ultima_advertencia.clear()  # olvida el enfriamiento de 16.3
    locutor.frases.clear()
    aplicacion._anunciar_obstaculos(detector.analizar(None), 60.0)
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
        mpu.az = 1.6 if i % 2 else 1.0
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

    # Sin verla aun, pide girar a la derecha (las senales estan del
    # lado derecho en el sentido de la ruta) y NO el giro de 90.
    locutor.frases.clear()
    paso(aplicacion, detector, mpu)
    comprobar(
        any("Gira despacio a la derecha" in frase for frase in locutor.frases),
        "pide girar a la derecha para ver la senal del Cuarto",
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
    pasos = TRAMOS_DEMO[("Cuarto", "Sala")].pasos
    for i in range(pasos * 8):
        mpu.az = 1.6 if i % 2 else 1.0
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
    """La media local sigue un cambio de postura sin dejar de contar.

    Con la linea base vieja (un reposo congelado) la marcha inflaba la
    referencia y los pasos reales se hundian bajo el umbral: "a veces
    no cuenta".  Aqui toda la magnitud sube 0.10 g de golpe (la
    persona se agacha o carga algo) y el contador tiene que seguir.
    """
    print("\n[17] Pasos: la media local sigue la postura que cambia")
    config = Configuracion()
    pasos = movimiento.ContadorPasos(config)
    mpu = MPUFalso()

    # Marcha con la cintura a la altura normal: valles 1.00, picos 1.25.
    for i in range(200):
        mpu.az = 1.25 if i % 4 < 2 else 1.00
        pasos.en_muestra(mpu.leer(), dt=0.01)
    comprobar(pasos.pasos >= 5, "con la postura inicial cuenta los pasos")

    # Toda la magnitud sube 0.10 g (valles 1.10, picos 1.35): la media
    # local la persigue en fraccion de segundo y la oscilacion de 0.25 g
    # sigue cruzando el umbral de delta_paso_g.
    previos = pasos.pasos
    for i in range(200):
        mpu.az = 1.35 if i % 4 < 2 else 1.10
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


def test_desvio_requiere_confirmacion() -> None:
    """El aviso de desvio espera la confirmacion y libera con histéresis.

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
        config=Configuracion(),  # confirmacion_desvio_s = 1.0 (defecto)
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
    # referencia se bloquee con el primer paso).
    mpu.gz = 0.0
    for i in range(4):
        mpu.az = 1.6 if i % 2 else 1.0
        paso(aplicacion, detector, mpu)

    # Se va curvando a la derecha: el umbral se cruza a las pocas
    # muestras, pero el aviso NO sale hasta que el desvio se sostenga
    # ``confirmacion_desvio_s`` segundos de reloj real.
    mpu.gz = -60.0
    for i in range(7):
        mpu.az = 1.6 if i % 2 else 1.0
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._inicio_desvio is not None,
        "el desvio cruza el umbral y empieza la espera de confirmacion",
    )
    comprobar(
        not any("desviando" in frase for frase in locutor.frases),
        "sin cumplirse la confirmacion todavia no avisa",
    )

    # Sigue desviado: al pasar el segundo, ahi si sale el aviso.
    time.sleep(aplicacion.config.confirmacion_desvio_s + 0.1)
    mpu.az = 1.6
    paso(aplicacion, detector, mpu)
    comprobar(
        any("desviando" in frase for frase in locutor.frases),
        "confirmado el desvio, avisa con los grados y como corregir",
    )
    comprobar(
        aplicacion._corrigiendo,
        "a partir de ahi queda en modo correccion",
    )

    # Endereza girando al contrario.  En la zona de histéresis (entre
    # liberacion_desvio_factor * umbral y el umbral) NO anuncia nada:
    # sin eso parpadearia entre "te desviando" y "ya enderezaste".
    locutor.frases.clear()
    mpu.gz = 60.0
    for _ in range(4):
        mpu.az = 1.6
        paso(aplicacion, detector, mpu)
    comprobar(
        not any("enderezaste" in frase for frase in locutor.frases),
        "dentro de la zona de histéresis no anuncia que enderezo",
    )

    # Baja del umbral de liberacion: ahi si avisa que ya enderezo y
    # que los pasos vuelven a contar.
    for _ in range(4):
        mpu.az = 1.6
        paso(aplicacion, detector, mpu)
    comprobar(
        any("enderezaste" in frase for frase in locutor.frases),
        "bajando del umbral de liberacion si anuncia que enderezo",
    )
    comprobar(
        any("vuelven a contar" in frase for frase in locutor.frases),
        "recuerda que los pasos vuelven a contar",
    )


def main() -> int:
    # Salida legible con acentos en Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 60)
    print("PRUEBA DEL ASISTENTE DE ORIENTACION")
    print("=" * 60)
    try:
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
        test_desvio_requiere_confirmacion()
    except AssertionError as error:
        print(f"\nFALLO: {error}")
        return 1

    print("\n" + "=" * 60)
    print("TODAS LAS PRUEBAS PASARON")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
