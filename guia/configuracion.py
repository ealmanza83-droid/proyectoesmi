"""Configuracion central del sistema de orientacion.

Todos los valores ajustables viven aqui: rutas de los modelos,
umbrales de los sensores y tiempos de espera.  De esta forma no hay
numeros "magicos" repartidos por el codigo y cambiar un umbral es una
sola linea.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Raiz del proyecto: funciona igual en la Raspberry Pi que en el PC.
RAIZ_DEL_PROYECTO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Configuracion:
    """Ajustes generales.  Son inmutables durante la ejecucion."""

    # ------------------------------------------------------------------
    # Vision (camara + YOLO)
    # ------------------------------------------------------------------
    ancho: int = 640
    alto: int = 480
    confianza_minima: float = 0.60
    detecciones_senal_requeridas: int = 3
    perdidas_senal_toleradas: int = 2
    enfriamiento_objeto_s: float = 4.0
    tiempo_busqueda_s: float = 60.0
    # Franja central del frame que se considera "en medio del camino".
    # La camara mira hacia adelante: un obstaculo cuyo centro cae
    # entre estos limites (fraccion del ancho del frame) puede
    # cruzarse en la ruta y se anuncia con mas enfasis; los de los
    # lados se nombra con su lado.
    obstaculo_centro_min: float = 0.30
    obstaculo_centro_max: float = 0.70
    # Franja mas estrecha para guiarse con la senal de la ruta: solo
    # si la imagen esta casi al centro se dice "camina hacia ella".
    # Una senal a un lado es una puerta que se pasa de largo, no un
    # punto al que caminar (ir hacia ella seria chocar con la pared).
    guia_senal_centro_min: float = 0.40
    guia_senal_centro_max: float = 0.60
    ruta_modelo: Path = RAIZ_DEL_PROYECTO / "mi_deteccion" / "yolo11n_ncnn_model"

    # ------------------------------------------------------------------
    # Audio (Piper + Vosk)
    # ------------------------------------------------------------------
    ruta_voz: Path = RAIZ_DEL_PROYECTO / "piper_voices" / "es_MX-claude-high.onnx"
    ruta_vosk: Path = RAIZ_DEL_PROYECTO / "vosk" / "vosk-model-small-es-0.42"
    carpeta_audio: Path = Path("/tmp/audio_orientacion")
    microfono_id: int | None = None
    ventana_comando_s: float = 8.0

    # ------------------------------------------------------------------
    # MPU6050 (giroscopio + acelerometro)
    # ------------------------------------------------------------------
    # El giroscopio da velocidad angular (grados/segundo): para saber
    # cuantos grados gira la persona hay que integrarla a frecuencia
    # fija.  Por eso el sensor se muestrea en su propio hilo.
    bus_i2c: int = 1
    direccion_mpu: int = 0x68
    registro_despertar: int = 0x6B
    registro_datos: int = 0x3B
    registro_filtro: int = 0x1A
    intervalo_muestreo_mpu_s: float = 0.01  # 100 muestras por segundo
    offset_giroscopio: float = 0.445992     # sesgo inicial (calibracion)
    velocidad_minima_giro_dps: float = 5.0  # por debajo no es un giro (temblor)
    velocidad_reposo_dps: float = 5.0  # ventana "plana" = senal quieta
    constante_sesgo_s: float = 5.0          # lentitud del aprendizaje del sesgo
    # Ventana (en segundos) de la que se toman el maximo y el minimo
    # de la velocidad angular para estimar el sesgo durante la marcha.
    # Tiene que cubrir al menos un ciclo de balanceo del paso (1 a 3 Hz)
    # y ser mas corta que el cierre del filtro sostenido tras un giro
    # (~1.9 s para 90 dps), para que los extremos del giro anterior no
    # contaminen el centro de oscilacion.
    ventana_sesgo_s: float = 1.0
    # Valvula de recalibracion: si la senal esta plana con el giro
    # sostenido abierto mas tiempo que esto, el sesgo se recalibra
    # igualmente (para no bloquearse con un sesgo ya desviado de mas).
    # Un giro de 90 grados a la minima velocidad que abre el filtro
    # (5 dps) dura menos de 18 s, asi que a un giro normal no le da
    # tiempo a dispararla.
    recalibracion_sesgo_s: float = 20.0
    # La velocidad se proyecta sobre la vertical (montaje libre) y el
    # signo lo da la fisica: +1 = derecha bien desde el principio.
    # Si el giro sigue yendo al contador equivocado, cambia a -1.
    signo_derecha: int = 1
    # La velocidad que va al RUMBO (orientacion de la ruta) solo se
    # integra cuando es sostenida: el vaiven de la cintura al caminar
    # cambia de signo a cada paso y su media con esta constante queda
    # en cero, mientras que un giro de verdad (y tambien la deriva
    # lenta, que no llega al umbral) pasa el filtro.
    tau_sostenida_giro_s: float = 0.5
    # Amplitud minima de la oscilacion de aceleracion que vale como
    # paso.  El sensor va en la cintura: al caminar la magnitud OSCILA
    # alrededor de una media que cambia con la postura, y el umbral
    # se mide contra esa media (ver ContadorPasos), no contra un
    # reposo congelado que la marcha iba inflando (con eso los pasos
    # reales se quedaban bajo el umbral y no se contaban).
    # Si faltan pasos en casa, baja un poco; si cuenta pasos al
    # balancear, sube (pruebas/caracterizar_mpu6050.py mide el ruido
    # en reposo para elegirlo).
    delta_paso_g: float = 0.09
    # Media local (pasa-largas) de la magnitud: sigue la postura en
    # segundos pero no la oscilacion del paso (2 a 6 Hz).
    tau_media_paso_s: float = 0.7
    # Ritmo real de la persona: zancada de 0.46 m a 1.85 m/s da un paso
    # cada ~0.25 s.  El intervalo minimo entre picos tiene que quedar
    # por DEBAJO de ese lapso: con el valor viejo (0.35 s) el contador
    # descartaba casi la mitad de los pasos reales y los tramos largos
    # (p. ej. 43 pasos) nunca se completaban.
    intervalo_paso_s: float = 0.18

    # ------------------------------------------------------------------
    # Deteccion de caidas (acelerometro)
    # ------------------------------------------------------------------
    umbral_caida_g: float = 2.5         # impacto: magnitud muy lejos de 1 g
    umbral_cambio_caida_g: float = 1.5  # salto brusco respecto a un instante antes
    ventana_cambio_caida_s: float = 0.3 # margen de tiempo del "brusco"
    muestras_caida_requeridas: int = 2  # muestras seguidas (mata glitches I2C)
    gracia_caida_s: float = 8.0         # no mirar caidas mientras se manipula al arrancar
    enfriamiento_caida_s: float = 15.0  # una sola alerta por caida
    duracion_alarma_caida_s: float = 60.0  # beep constante maximo tras caer

    # ------------------------------------------------------------------
    # Movimiento (giro inicial, media vuelta y pasos)
    # ------------------------------------------------------------------
    grados_giro_inicial: int = 90
    grados_media_vuelta: int = 180
    pasos_objetivo: int = 5
    # Marcha de la persona (medidas reales): de aqui salen los pasos de
    # cada tramo de la ruta (distancia / zancada, ver dominio/rutas.py)
    # y el ritmo que soporta el contador de pasos (intervalo_paso_s).
    zancada_m: float = 0.46       # metros que avanza con un paso
    velocidad_m_s: float = 1.85   # velocidad de desplazamiento (m/s)

    # ------------------------------------------------------------------
    # Navegacion asistida (rumbo y linea recta)
    # ------------------------------------------------------------------
    # El rumbo integra el giroscopio desde la ultima senal vista.  Si
    # mientras la persona camina acumula mas giro que este umbral, va
    # curvando la ruta y se le avisa que enderece.
    desvio_maximo_grados: float = 25.0
    # El sensor esta en la cintura: un giro repentino del torso o un
    # balanceo fuerte no son un cambio de ruta.  El aviso solo sale si
    # el desvio SE SOSTIENE durante este tiempo desde que cruzo el
    # umbral (y si baja del umbral, la espera empieza de cero).
    confirmacion_desvio_s: float = 1.0
    # Histesis al liberar: tras el aviso hay que bajar hasta este
    # fraccion del umbral para decir "ya enderezaste".  Sin esto, en
    # el limite del umbral el sistema parpadearia entre "te estas
    # desviando" y "ya enderezaste".
    liberacion_desvio_factor: float = 0.6
    enfriamiento_desvio_s: float = 12.0
    # Tras los pasos medidos la llegada NO se da por hecha: hay que
    # ver la senal interior del destino.  Este es el tiempo maximo
    # antes de volver a avisar ("gira despacio hasta encontrarla"),
    # sin anunciar jamas una llegada falsa.
    tiempo_llegada_s: float = 15.0

    # ------------------------------------------------------------------
    # HC-SR04 (alerta de proximidad)
    # ------------------------------------------------------------------
    # Numero de chip lgpio de /dev/gpiochip*.  El numero CAMBIA con las
    # actualizaciones del kernel: en esta Raspberry Pi 5 el que funciona
    # es el 15, el mismo con el que se verifico el sensor con el script
    # pruebas/prueba_ultrasonido.py (ver ``ls /dev/gpiochip*``).
    chip_lgpio: int | None = 15
    # Pines BCM del HC-SR04: los del cableado real del sensor, tal como
    # se verifico con gpiozero (TRIG en GPIO18 = pin fisico 12, ECHO en
    # GPIO23 = pin fisico 16).  No hay conflictos: el MPU6050 usa I2C
    # (GPIO2/3), la camara la interfaz CSI y el audio va por ALSA
    # (aplay), no por I2S.
    # OJO: ECHO habla a 5V y el GPIO solo admite 3.3V: va con divisor
    # de voltaje siempre.
    pin_echo: int = 23
    pin_trigger: int = 18
    distancia_maxima_m: float = 4.0
    distancia_alerta_cm: float = 200.0

    def preparar(self) -> None:
        """Crea las carpetas que el sistema necesita para funcionar."""
        self.carpeta_audio.mkdir(parents=True, exist_ok=True)
