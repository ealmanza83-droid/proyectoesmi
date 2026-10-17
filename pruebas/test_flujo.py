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
from guia.vision.detector import ConfirmadorSenal, ResultadoVision  # noqa: E402


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
    mpu.gz = 0.0
    for i in range(80):
        mpu.az = 1.6 if i % 2 else 1.0
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
        giro.avanzar(derecha=False) == 0.0,
        "el giro a la derecha no suma en el contador izquierdo",
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
        confirmador.observar("cow") == "Inicio",
        "confirma con parpadeos intercalados",
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
        test_confirmacion_de_senal_tolerante()
        test_pasos_sin_fantasmas()
    except AssertionError as error:
        print(f"\nFALLO: {error}")
        return 1

    print("\n" + "=" * 60)
    print("TODAS LAS PRUEBAS PASARON")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
