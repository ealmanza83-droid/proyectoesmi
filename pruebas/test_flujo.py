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

import importlib
import queue
import sys
import threading
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
from guia.dominio.estados import Estado  # noqa: E402
from guia.dominio.rutas import Accion, PlanificadorRutas  # noqa: E402
from guia.hardware.mpu6050 import LecturaMPU  # noqa: E402
from guia.hardware.ultrasonido import intervalo_de_beep  # noqa: E402
from guia.navegacion import movimiento as movimiento  # noqa: E402
from guia.navegacion.caida import DetectorCaida  # noqa: E402
from guia.vision.detector import ResultadoVision  # noqa: E402


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
    distancia_cm = 150.0
    distancia_alerta_cm = 200.0


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
        self.senal: str | None = None
        self.obstaculos: tuple[str, ...] = ()

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
    aplicacion._anunciar_obstaculos(resultado)
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


def test_planificacion_de_rutas() -> None:
    print("\n[2] Planificacion de rutas")
    planificador = PlanificadorRutas()

    simple = planificador.planificar("Inicio", "Sala")
    comprobar(simple is not None and len(simple) == 3, "Inicio -> Sala: 3 instrucciones")
    comprobar(simple[0].accion is Accion.GIRAR_DERECHA, "primero gira a la derecha")
    comprobar(simple[1].accion is Accion.CAMINAR and simple[1].cantidad == 10, "luego 10 pasos")
    comprobar(simple[2].accion is Accion.LLEGADA, "cierra con la llegada")

    larga = planificador.planificar("Inicio", "Baño")
    comprobar(larga is not None and len(larga) == 5, "Inicio -> Baño cruza dos tramos")
    comprobar(planificador.planificar("Sala", "Cocina") is None, "sin ruta -> None")
    comprobar(planificador.planificar(None, "Sala") is None, "ubicacion desconocida -> None")


def test_flujo_completo() -> None:
    print("\n[3] Flujo completo: giro, senal, destino y navegacion")
    movimiento.time = RelojFalso()  # reloj acelerado para el ensayo

    detector = DetectorFalso()
    mpu = MPUFalso()
    locutor = LocutorFalso()
    microfono = MicrofonoFalso()

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

    # --- 3.1 Giro inicial de 90 grados -----------------------------
    microfono.cambiar_modo(ModoMicrofono.DESPERTADOR)  # lo hace ejecutar()
    aplicacion.giro.reiniciar()
    mpu.gz = 100.0  # giro a derecha sostenido
    for _ in range(15):
        paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.BUSCAR_SENAL, "giro de 90 grados completado")
    comprobar(
        microfono.modo is ModoMicrofono.DESPERTADOR,
        "el microfono vuelve a escuchar el despertador",
    )

    # --- 3.2 Aparece la senal orientadora --------------------------
    detector.senal = "Sala"
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "senal detectada")
    comprobar(aplicacion.ubicacion == "Sala", "ubicacion actualizada a Sala")
    comprobar(microfono.modo is ModoMicrofono.DESTINO, "pide el destino por voz")

    # --- 3.3 La persona pide un destino ----------------------------
    microfono.eventos.put(
        EventoVoz(ModoMicrofono.DESTINO, "quiero ir al baño")
    )
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.estado is Estado.NAVEGANDO, "ruta hacia Baño iniciada")
    comprobar(aplicacion.destino == "Baño", "destino registrado")
    comprobar(microfono.modo is ModoMicrofono.DESPERTADOR, "sigue escuchando ordenes")

    # --- 3.4 Giro de la ruta (izquierda 90 grados) -----------------
    mpu.gz = -100.0
    for _ in range(15):
        paso(aplicacion, detector, mpu)
    comprobar(
        aplicacion._instruccion_actual is not None
        and aplicacion._instruccion_actual.es_pasos,
        "giro de la ruta completado, sigue caminar",
    )

    # --- 3.5 Camina los 6 pasos del tramo --------------------------
    mpu.gz = 0.0
    mpu.az = 1.5  # picos de aceleracion al caminar
    for _ in range(60):
        paso(aplicacion, detector, mpu)
        if aplicacion.estado is Estado.ESPERANDO_DESTINO:
            break

    # --- 3.6 Llegada ------------------------------------------------
    comprobar(aplicacion.estado is Estado.ESPERANDO_DESTINO, "navegacion terminada")
    comprobar(aplicacion.ubicacion == "Baño", "ubicacion final: Baño")
    comprobar(aplicacion.destino is None, "destino limpio tras la llegada")
    comprobar("Has llegado a Baño." in locutor.frases, "anuncio de llegada")
    comprobar("¿A dónde quieres ir?" in locutor.frases, "vuelve a preguntar destino")

    # --- 3.7 Caida: alarma constante y orden para apagarla ---------
    mpu.az = 4.0  # golpe brusco contra el suelo
    paso(aplicacion, detector, mpu)
    comprobar(aplicacion.alarma.alarma_activa, "la caida enciende la alarma continua")
    comprobar(
        any("Caida detectada" in frase for frase in locutor.frases),
        "anuncia la caida por voz",
    )

    microfono.eventos.put(EventoVoz(ModoMicrofono.DESPERTADOR, "Raspi silencia"))
    paso(aplicacion, detector, mpu)
    comprobar(not aplicacion.alarma.alarma_activa, "la orden 'silencia' apaga la alarma")


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


def test_giro_integra_velocidad_angular() -> None:
    """El giroscopio da grados/segundo: hay que integrarlos con el tiempo."""
    print("\n[5] Giro: integracion de la velocidad angular")
    config = Configuracion()
    giro = movimiento.MedidorGiro(config)
    mpu = MPUFalso()
    dt = config.intervalo_muestreo_mpu_s  # 0.01 s (100 Hz)

    # Giro sostenido de 90 dps durante 1.1 s -> mas de 90 grados.
    mpu.gz = 90.0
    for _ in range(110):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=True) >= 90.0,
        "90 dps durante 1.1 s superan los 90 grados",
    )
    comprobar(
        giro.avanzar(derecha=False) == 0.0,
        "el giro a la derecha no suma en el contador izquierdo",
    )

    # En reposo (gz ~ offset) no se acumula angulo.
    giro.reiniciar()
    mpu.gz = config.offset_giroscopio
    for _ in range(500):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(giro.avanzar(derecha=True) == 0.0, "en reposo no se acumulan grados")

    # Giro lento pero continuo: 10 dps durante 12 s -> 120 grados.
    giro.reiniciar()
    mpu.gz = config.offset_giroscopio + 10.0
    for _ in range(1200):
        giro.en_muestra(mpu.leer(), dt)
    comprobar(
        giro.avanzar(derecha=True) >= 100.0,
        "un giro lento (10 dps) tambien completa el objetivo",
    )


def test_deteccion_de_caida() -> None:
    print("\n[6] Caida: cambio brusco del acelerometro")
    config = Configuracion()
    detector_caida = DetectorCaida(config)
    mpu = MPUFalso()

    # Persona quieta: nada de alertas.
    for _ in range(50):
        detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(not detector_caida.hay_caida, "en reposo no hay caida")

    # Cambio brusco (golpe contra el suelo).
    mpu.az = 3.5
    detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(detector_caida.hay_caida, "un golpe brusco dispara la caida")

    # Tras avisar, el enfriamiento evita repetir la alerta.
    detector_caida.reconocer()
    comprobar(not detector_caida.hay_caida, "al avisar se limpia el aviso")
    detector_caida.en_muestra(mpu.leer(), dt=0.01)
    comprobar(
        not detector_caida.hay_caida,
        "el enfriamiento evita alarmas repetidas por el mismo golpe",
    )


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
        test_deteccion_de_caida()
        test_proximidad_se_acelera()
    except AssertionError as error:
        print(f"\nFALLO: {error}")
        return 1

    print("\n" + "=" * 60)
    print("TODAS LAS PRUEBAS PASARON")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
