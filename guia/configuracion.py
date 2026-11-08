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
    fps_camara: float = 15.0
    confianza_minima: float = 0.60
    detecciones_senal_requeridas: int = 3
    perdidas_senal_toleradas: int = 2
    enfriamiento_objeto_s: float = 4.0
    tiempo_busqueda_s: float = 60.0
    # La imagen se divide en TRES fragmentos iguales (izquierda,
    # centro, derecha).  Estos dos limites marcan donde empieza y
    # termina el fragmento central (fraccion del ancho del frame); con
    # los valores por defecto es exactamente un tercio: de 1/3 a 2/3.
    # Un obstaculo cuyo centro cae ahi se considera EN MEDIO del
    # camino: la camara mira hacia adelante, asi que ese fragmento es
    # por donde pasa la persona.
    obstaculo_centro_min: float = 1.0 / 3.0
    obstaculo_centro_max: float = 2.0 / 3.0
    # Durante la NAVEGACION (dando pasos) solo se tienen en cuenta
    # los objetos del fragmento central: los de los otros dos
    # fragmentos se descartan por completo, porque avisar de lo que
    # esta a un lado solo genera confusion (no estan en la direccion
    # que se recorre).  Fuera de la navegacion (giro inicial, busqueda
    # y encarada de la senal, media vuelta...) la camara NO anuncia
    # obstaculo alguno: la persona gira sobre su propio eje.  La
    # senal orientadora NUNCA se recorta: se sigue leyendo con los 3
    # fragmentos en todo momento.
    mirar_solo_centro_en_marcha: bool = True
    # Franja mas estrecha para guiarse con la senal de la ruta: solo
    # si la imagen esta casi al centro se dice "camina hacia ella".
    # Una senal a un lado es una puerta que se pasa de largo, no un
    # punto al que caminar (ir hacia ella seria chocar con la pared).
    guia_senal_centro_min: float = 0.40
    guia_senal_centro_max: float = 0.60
    # Distancia visual (pinhole): d = focal_px * altura_real / alto_px.
    # Define la altura real de la imagen impresa y calibra focal_px con
    # pruebas/calibrar_focal.py; cero desactiva mediciones no calibradas.
    altura_senal_m: float = 0.20
    focal_px: float = 0.0
    # No calcular distancias a partir de cajas de señal más pequeñas:
    # a esa escala el error de unos pocos píxeles distorsiona mucho d.
    alto_minimo_senal_px: int = 20
    # Puertas son una guía auxiliar opcional; las señales interiores
    # identifican la ubicación y bastan para cerrar los tramos.
    usar_puertas: bool = False
    distancia_mediana_frames: int = 5
    distancia_mediana_perdidas: int = 3
    altura_puerta_m: float = 2.0
    ancho_puerta_m: float = 0.9
    salto_maximo_puerta_px: float = 160.0
    frames_puerta_perdida: int = 4
    muestras_ultrasonido_puerta: int = 3
    salto_maximo_ultrasonido_puerta_cm: float = 60.0
    retencion_eco_ultrasonido_s: float = 0.35
    # Una lectura estable por debajo de este límite impide confirmar
    # y pide alejarse un poco de la puerta.
    distancia_puerta_min_cm: float = 45.0
    # Sin señal visible, se usa solo para decidir cuándo dejar de
    # acercarse a una puerta y empezar a buscarla con la cámara.
    distancia_puerta_max_cm: float = 140.0
    # Distancia de referencia para el aviso de acercamiento cuando la
    # cámara aún no reconoce la señal y la persona ya giró hacia ella.
    distancia_maxima_senal_cm: float = 80.0
    espera_llegada_tras_obstaculo_s: float = 4.0
    ruta_modelo: Path = RAIZ_DEL_PROYECTO / "mi_deteccion" / "yolo11n_ncnn_model"

    # ------------------------------------------------------------------
    # Voz: que manda y que espera
    # ------------------------------------------------------------------
    # La voz es UN solo canal y la navegacion tiene prioridad: los
    # obstaculos solo se anuncian cuando la guia lleva este tiempo sin
    # hablar.  Asi una indicacion (giro, pasos, llegada, encarada) jamas
    # se atrasa ni se pisa por un aviso de objeto: el aviso espera su
    # turno.  El silencio se cuenta desde que TERMINA la frase anterior.
    silencio_para_obstaculos_s: float = 4.0
    # Que si se anuncia por voz: solo objetos estaticos que pueden
    # estar EN MEDIO del camino.  "persona" queda fuera: se mueve, no
    # bloquea el paso y anunciarla daba un aviso cada vez que alguien
    # pasaba (los pitidos del ultrasonido ya avisan de algo enfrente).
    obstaculos_en_voz: tuple[str, ...] = ("silla", "mesa", "maceta")
    # Los lados del camino se RECONOCEN pero no se anuncian: no estan
    # en la direccion que se recorre y llenaban la voz de avisos que
    # hacian detenerse sin motivo.  En ``True`` vuelven a decirse (una
    # sola frase, sin "Alto", sin frenar el paso).
    anunciar_lados: bool = False

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
    offset_giroscopio: float = 0.379        # sesgo inicial de respaldo (eje Z)
    # Sesgos triaxiales medidos del MPU6050: permiten proyectar la rotacion
    # sobre la gravedad sin derivas artificiales aunque el sensor este a 90 grados.
    offset_giroscopio_x: float = -0.506     # sesgo en reposo eje X (dps)
    offset_giroscopio_y: float = 4.372      # sesgo en reposo eje Y (dps, crucial al montar a 90°)
    offset_giroscopio_z: float = 0.379      # sesgo en reposo eje Z (dps)
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
    # Suaviza el vector de aceleracion antes de usarlo como vertical.
    # El balanceo e impactos de cada paso no deben inclinar el eje de
    # proyeccion del giroscopio.
    tau_gravedad_giro_s: float = 0.5
    # ------------------------------------------------------------------
    # Pasos: banda del paso y umbral adaptativo (ver ContadorPasos)
    # ------------------------------------------------------------------
    # La oscilacion de la aceleracion al caminar esta entre estas dos
    # frecuencias (1,5 a 3 Hz andando; por debajo esta el balanceo y la
    # postura que cambia, por encima el temblor).  Todo lo que quede
    # fuera de banda lo anula solo el pasa-banda de ContadorPasos, asi
    # que no hace falta perseguir una media local.
    frecuencia_paso_min_hz: float = 0.5
    frecuencia_paso_max_hz: float = 5.0
    # Las lecturas I2C tienen jitter; no redisenar el filtro por cambios
    # pequeños de fs, porque eso hace inestable el detector de pasos.
    variacion_recalculo_filtro_paso_pct: float = 0.05
    # Piso del umbral: por debajo de esto no hay pico que valga como
    # paso (ruido del sensor).  Se mide sobre la senal YA FILTRADA.
    # Si faltan pasos en casa, bajalo; si cuenta pasos al balancear,
    # subelo (pruebas/caracterizar_mpu6050.py mide el ruido en reposo).
    umbral_paso_min_g: float = 0.07
    # Umbral ADAPTATIVO: max(umbral_paso_min_g, factor x media de los
    # ultimos picos CONTADOS).  El 0.45 deja el umbral a la mitad de lo
    # que de verdad produce la marcha de esa persona: si camina suave
    # el umbral baja solo, y si va vigoroso sube (el ruido se queda
    # fuera).  Sin esto un umbral fijo o bien perdia los pasos suaves
    # o bien contaba el balanceo.
    factor_umbral_paso: float = 0.45
    # Periodo refractario: intervalo minimo entre dos pasos contados
    # (contra el doble conteo de un mismo pico).  El ritmo real de la
    # persona es un paso cada ~0,25 a 0,5 s, asi que este valor tiene
    # que quedar por DEBAJO de eso: con 0.22 s se elimina el rebote
    # secundario del talon sin cortar la marcha rapida.
    intervalo_paso_s: float = 0.22
    # Piso duro del intervalo MAXIMO: mas que esto desde el ultimo paso
    # Si el intervalo supera este maximo, el pico queda pendiente y
    # solo se acredita junto con el siguiente pico de marcha. Evita
    # contar movimientos aislados al esquivar, sin perder el primer
    # paso al reanudar. En 0 se desactiva.
    intervalo_paso_max_s: float = 1.8
    # Cadencia: solo se descarta un pico demasiado cercano al anterior
    # respecto al ritmo medido (probable doble conteo). Las variaciones
    # mas lentas o mas rapidas son pasos validos; las pausas largas se
    # filtran con intervalo_paso_max_s. La tolerancia deja pasar
    # intervalos hasta un 62 % mas cortos que la mediana.
    cadencia_tolerancia: float = 0.62
    cadencia_intervalos_minimos: int = 3
    # Tras N descartes seguidos de picos demasiado cercanos se olvida
    # la cadencia y el siguiente pico arranca un ritmo nuevo.
    rechazos_rebasar: int = 3

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
    # Compensacion del desfase al girar: al llegar al objetivo suena
    # la frase final ("Puedes parar.") y luego la instruccion
    # siguiente; mientras las dos se dicen (y hasta que la persona
    # reacciona) SIGUE GIRANDO, y eso dejaba el giro ~10 grados
    # pasado del objetivo.  Por eso el corte se adelanta estos
    # grados: el giro total aterriza en el objetivo.
    # Subelo si aun se pasa; bajalo si se queda corto.
    grados_compensacion_giro: float = 12.0
    pasos_objetivo: int = 5
    # Marcha de la persona (medidas reales): de aqui salen los pasos de
    # cada tramo de la ruta (distancia / zancada, ver dominio/rutas.py)
    # y el ritmo que soporta el contador de pasos (intervalo_paso_s).
    # Es la SEMILLA: hasta que la camara no cierra ningun tramo, el
    # plan usa los pasos del mapa; a partir de ahi manda la zancada
    # medida (ver zancada_ema_alfa).
    zancada_m: float = 0.60       # 30,2 m / 50 pasos medidos
    # Ajusta cada paso por la amplitud del pico de aceleración. Desactivado
    # hasta que reduzca el error en recorridos reales con ojos vendados.
    usar_zancada_por_pico: bool = False
    velocidad_m_s: float = 1.85   # velocidad de desplazamiento (m/s)
    # Zancada ADAPTATIVA (ver dominio/zancada.py): en cada tramo que
    # la camara cierra se mide "distancia del mapa / pasos contados"
    # y se mezcla con la anterior como media movil exponencial:
    #     zancada = alfa * medida + (1 - alfa) * zancada_anterior
    # ``alfa`` es el peso de la ultima medida: 0.30 deja que tres o
    # cuatro tramos midan la zancada de la persona sin que un tramo
    # raro (contado al reves) descuadre el plan.
    zancada_ema_alfa: float = 0.30
    # Recorte de la medida: nadie camina a 0,10 m ni a 2,0 m por paso.
    # Un tramo contado mal se acota aqui y no arrastra la EMA.
    zancada_min_m: float = 0.30
    zancada_max_m: float = 0.90

    # ------------------------------------------------------------------
    # Llegada por VENTANA (no por "N pasos exactos")
    # ------------------------------------------------------------------
    # Los pasos son un metodo de lazo abierto: la zancada cambia entre
    # personas y ritmos, y el error se acumula.  Por eso un tramo ya no
    # se da por terminado exactamente en "N pasos", sino en una
    # VENTANA alrededor de los pasos esperados:
    #
    # * antes de ``ventana_llegada_inicio`` se anticipa el aviso de
    #   frenado según la cadencia y la distancia estimada de parada;
    #   este aviso nunca cierra el tramo;
    # * la cámara ya lee la senal en cada frame; el cierre visual sigue
    #   limitado al umbral de ``ventana_llegada_inicio``);
    # * si la camara confirma la senal esperada entre el 70 % y el
    #   ``ventana_llegada_fin`` (130 %), el tramo se da por completado
    #   (re-localizacion verificada: nunca se anuncia una llegada sin
    #   verla);
    # * si se pasa del 130 % sin ver la senal, los tramos intermedios
    #   siguen el flujo existente; en el tramo final, una lectura
    #   ultrasónica fuera del rango seguro mantiene la aproximación
    #   lenta. La señal reconocida no necesita estar centrada para
    #   cerrar el tramo.
    ventana_llegada_inicio: float = 0.70
    ventana_llegada_fin: float = 1.30
    # Adelanta el aviso de frenado según la velocidad estimada por la
    # cadencia reciente y una distancia de reacción + desaceleración.
    # El adelanto se limita para no pedir que reduzca el paso demasiado
    # pronto; solo afecta al aviso, nunca al umbral de cierre visual.
    periodo_paso_llegada_s: float = 0.6
    tiempo_reaccion_llegada_s: float = 0.8
    desaceleracion_llegada_m_s2: float = 1.2
    anticipacion_llegada_max_fraccion: float = 0.15

    # ------------------------------------------------------------------
    # Navegacion asistida (rumbo y linea recta)
    # ------------------------------------------------------------------
    # El rumbo integra el giroscopio desde la ultima senal vista.  Si
    # mientras la persona camina acumula mas giro que este umbral, va
    # curvando la ruta y se le avisa que enderece.
    desvio_maximo_grados: float = 35.0
    # Un paso aporta su proyeccion sobre la direccion del tramo hasta
    # este angulo; por encima se considera que no hubo avance longitudinal.
    desvio_credito_max_grados: float = 75.0
    # Un tramo solo actualiza la EMA de zancada si su rumbo se mantuvo
    # dentro de este máximo durante toda la caminata.
    desvio_aprendizaje_max_grados: float = 20.0
    # El sensor esta en la cintura: un giro repentino del torso o un
    # balanceo fuerte no son un cambio de ruta.  El aviso solo sale si
    # el desvio SE SOSTIENE durante este tiempo desde que cruzo el
    # umbral (y si baja del umbral, la espera empieza de cero).
    confirmacion_desvio_s: float = 1.5
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
    # Encarada automatica: si la persona acaba de confirmar la senal
    # de SU zona (la busqueda la vio, o se mantuvo a la vista
    # esperando el destino) y no ha girado desde entonces, ya la
    # tiene enfrente: la ruta arranca sin pedir encarar otra vez.
    # En casa, pedirla de nuevo deja a la persona girando sin
    # salida: las senales de las demas zonas estan a pasos de
    # distancia y no pueden aparecer en el mismo sitio.
    # * ``encarada_validez_s``: cuanto tiempo vale esa confirmacion
    #   (la persona pide el destino sin moverse; si tarda mas,
    #   vuelve a pedir la encarada como siempre).
    # * ``encarada_max_giro_grados``: giro maximo desde el anclaje
    #   para darla por encarada; por encima, se pidio encarar.
    encarada_validez_s: float = 30.0
    encarada_max_giro_grados: float = 20.0

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
    # Rango en el que empiezan los pitidos y el aviso de proximidad
    # (el obstaculo FRONTAL, el unico que mide el HC-SR04).  A 200 cm
    # se quedaba corto: lo frontal se avisaba tarde mientras la
    # camara ya nombraba lo lateral.  Con 300 cm da tiempo de
    # reaccion sin llenar de pitidos todo el pasillo; el intervalo se
    # re-escala solo (ver ``intervalo_de_beep``).
    distancia_alerta_cm: float = 300.0
    # Odómetro opcional de pasillo: exige varias lecturas estables y
    # rechaza saltos bruscos antes de usarlas para corregir posición.
    muestras_ultrasonido_odometro: int = 5
    salto_maximo_ultrasonido_cm: float = 60.0
    sigma_inicial_posicion_m: float = 0.5
    varianza_proceso_paso_m2: float = 0.0025
    # Error relativo de la zancada: la incertidumbre crece también con
    # la distancia predicha, no solo con el número de pasos.
    error_relativo_zancada: float = 0.15
    # Multiplica el ruido de avance durante un esquive/corrección activa.
    inflar_incertidumbre_esquive: float = 2.0
    varianza_medicion_senal_m2: float = 0.16
    varianza_medicion_ultrasonido_m2: float = 0.09
    varianza_medicion_puerta_m2: float = 0.64
    # Margen de confianza usado para anticipar la búsqueda de la señal.
    sigmas_inicio_busqueda: float = 1.0
    # Margen de confianza para ensanchar la ventana de cierre del tramo.
    sigmas_cierre: float = 2.0

    def preparar(self) -> None:
        """Crea las carpetas que el sistema necesita para funcionar."""
        self.carpeta_audio.mkdir(parents=True, exist_ok=True)
