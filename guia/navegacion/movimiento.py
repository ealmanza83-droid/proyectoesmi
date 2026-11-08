"""Contadores de movimiento basados en el MPU6050.

El giroscopio no mide angulos: mide *velocidad angular* (grados por
segundo).  Para saber cuantos grados ha girado la persona hace falta
integrar esa velocidad a una frecuencia fija.  Por eso
:class:`MuestreadorMPU` lee el sensor en su propio hilo (100 veces por
segundo) y le pasa cada lectura a los oyentes:

* :class:`MedidorGiro` integra la velocidad angular en grados.
* :class:`ContadorPasos` detecta la oscilacion de la aceleracion al
  caminar, filtrada en banda (0,5 a 5 Hz) y con cadencia validada.

Asi un giro lento, una pausa del bucle principal o la carga de la
camara ya no hacen perder la cuenta de grados.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from ..configuracion import Configuracion
from ..hardware.mpu6050 import LecturaMPU, MPU6050
from .rumbo import Rumbo

# Tope del dt integrado: protege contra paradas extremas del hilo (por
# ejemplo un cuelgue del bus I2C).  Es generoso a proposito: si la
# persona giro mientras el hilo estuvo atrasado, esos grados son
# reales y hay que integrarlos (con un tope corto se perdia la mitad
# del angulo cuando la CPU saturada retrasaba el muestreo).
DT_MAXIMO_S = 1.0

# Segundos de marcha antes de avisar por consola la tasa real de
# muestreo (deberia rondar las 100 muestras por segundo).
TASA_MPU_AVISO_S = 5.0

# Por debajo de esta magnitud la aceleracion no sirve para saber donde
# esta arriba (caida libre, muestras rotas): se usa el eje Z por defecto.
MAGNITUD_MINIMA_VERTICAAL_G = 0.5


class MuestreadorMPU(threading.Thread):
    """Lee el MPU6050 a frecuencia fija y reparte cada lectura."""

    def __init__(self, mpu: MPU6050, config: Configuracion, oyentes):
        super().__init__(name="muestreador-mpu", daemon=True)
        self._mpu = mpu
        self._intervalo = config.intervalo_muestreo_mpu_s
        self._oyentes = tuple(oyentes)
        self._detener = threading.Event()

    def muestrear(self, lectura: LecturaMPU, dt: float) -> None:
        """Reparte una lectura entre los oyentes (tambien en pruebas)."""
        for oyente in self._oyentes:
            oyente(lectura, dt)

    def run(self) -> None:
        anterior = time.monotonic()
        inicio = anterior
        muestras = 0
        avisada_tasa = False

        while not self._detener.is_set():
            ahora = time.monotonic()
            dt = min(ahora - anterior, DT_MAXIMO_S)
            anterior = ahora
            muestras += 1

            try:
                self.muestrear(self._mpu.leer(), dt)
            except Exception as error:
                print("ERROR MPU6050:", error)

            if not avisada_tasa and ahora - inicio >= TASA_MPU_AVISO_S:
                avisada_tasa = True
                tasa = muestras / (ahora - inicio)
                print(f"MPU: {tasa:.0f} muestras/seg")

            self._detener.wait(self._intervalo)

    def detener(self, espera_s: float = 2.0) -> None:
        """Senala la salida y espera a que el hilo termine."""
        self._detener.set()
        if self.is_alive():
            self.join(espera_s)


class MedidorGiro:
    """Integra la velocidad angular hasta alcanzar un objetivo.

    La rotacion vertical no se toma de un eje fijo del sensor (gz), sino
    que se *proyecta* el vector giroscopio sobre la direccion de la
    gravedad medida por el acelerometro.  Asi funciona igual si el
    modulo esta horizontal, vertical o girado: la componente que cuenta
    es siempre la del eje hacia arriba, y el signo lo da la regla de la
    mano derecha (derecha = negativo, por eso velocidad_derecha es el
    opuesto de la proyeccion).
    """

    def __init__(self, config: Configuracion, rumbo: Rumbo | None = None):
        self._config = config
        self._rumbo = rumbo
        self._angulo_derecha = 0.0
        self._angulo_izquierda = 0.0
        # Sesgos triaxiales: restan el offset de cada eje antes de la proyeccion
        # gravitatoria, garantizando deriva nula en cualquier angulo (plano o 90°).
        self._offset_x = getattr(config, "offset_giroscopio_x", 0.0)
        self._offset_y = getattr(config, "offset_giroscopio_y", 0.0)
        self._offset_z = getattr(
            config, "offset_giroscopio_z", config.offset_giroscopio
        )
        self._sesgo = 0.0
        self._velocidad_derecha = 0.0
        self._gravedad_filtrada: tuple[float, float, float] | None = None
        # Media (pasa-largas) de la direccion de giro: decide si hay
        # un giro SOSTENIDO que llevar al rumbo (ver en_muestra).
        self._sostenida = 0.0
        # Ventana deslizante de la velocidad angular (tiempo, valor):
        # de ahi salen el maximo y el minimo de la oscilacion, cuyo
        # punto medio estima el sesgo (ver en_muestra).
        self._ventana_giro: deque[tuple[float, float]] = deque()
        self._tiempo_ventana = 0.0
        # Segundos que la senal lleva plana con el giro sostenido abierto
        # (valvula de recalibracion del sesgo, ver en_muestra).
        self._plano_girando_s = 0.0

    def _velocidad_de_giro(self, lectura: LecturaMPU, dt: float) -> float:
        """Velocidad de giro sobre la vertical, positiva hacia la derecha."""
        modulo = lectura.magnitud_aceleracion
        if modulo >= MAGNITUD_MINIMA_VERTICAAL_G:
            if self._gravedad_filtrada is None:
                self._gravedad_filtrada = (lectura.ax, lectura.ay, lectura.az)
            else:
                tau = max(self._config.tau_gravedad_giro_s, 1e-6)
                alfa = 1.0 - math.exp(-dt / tau)
                self._gravedad_filtrada = tuple(
                    anterior + (actual - anterior) * alfa
                    for anterior, actual in zip(
                        self._gravedad_filtrada,
                        (lectura.ax, lectura.ay, lectura.az),
                    )
                )

        gx_neto = lectura.gx - self._offset_x
        gy_neto = lectura.gy - self._offset_y
        gz_neto = lectura.gz - self._offset_z

        if self._gravedad_filtrada is not None:
            ax, ay, az = self._gravedad_filtrada
            norma = math.sqrt(ax * ax + ay * ay + az * az)
            if norma >= MAGNITUD_MINIMA_VERTICAAL_G:
                proyectada = (
                    ax * gx_neto + ay * gy_neto + az * gz_neto
                ) / norma
                return -proyectada

        # Hasta obtener un vector de gravedad fiable no se conoce el
        # montaje: conservar la convencion anterior como respaldo.
        return -gz_neto

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Integra una lectura del sensor (la llama el muestreador).

        En reposo el giroscopio deberia marcar cero: lo que lee es su
        propio sesgo, que se aprende despacio para no depender de un
        offset fijo que cambia con la temperatura.  El aprendizaje tiene
        dos fuentes seguras y una prohibida:

        * PROHIBIDO mientras ``_sostenida`` marque un giro de verdad:
          durante un giro la rotacion de la persona NO es sesgo.
        * Con la senal PLANA (la ventana de extremos casi cerrada), la
          propia lectura.
        * Con la OSCILACION del paso, el punto medio entre el maximo y
          el minimo de la ventana: el centro del vaiven no se deja
          arrastrar por el cruce por cero como hacia la regla vieja de
          "muestra en reposo" (ese cruce re-aprendia el sesgo
          equivocado -> deriva -> falsos desvíos al girar a la
          izquierda).

        Solo se integra cuando la persona gira de verdad (por encima de
        la velocidad minima), asi el temblor de la mano no cuenta como
        giro.
        """
        dt = min(dt, DT_MAXIMO_S)
        velocidad = self._velocidad_de_giro(lectura, dt)
        self._velocidad_derecha = velocidad

        # --- Ventana deslizante: maximo y minimo de la oscilacion -------
        # Guarda ``ventana_sesgo_s`` de muestras y toma sus extremos.
        #
        # Elegir EXTREMOS de una ventana (y no una envolvente ataque/
        # release) es lo que hace el estimador independiente de la FASE:
        # los extremos son valores constantes mientras dure la marcha,
        # de modo que da igual en que punto del ciclo de balanceo entre
        # la muestra (o este el filtro sostenido cerrado).  Con una
        # envolvente clasica el punto medio oscilaba con el paso y, al
        # aprender solo en las muestras en que el filtro sostenido
        # estaba por debajo del umbral, se muestreaba siempre ese mismo
        # punto del ciclo: el sesgo salia corrido (aprendia la mitad del
        # vaiven como si fuera sesgo y el rumbo seguia derivando).
        #
        # La ventana tiene que cubrir al menos UN ciclo de balanceo del
        # paso (1 a 3 Hz) para que max y min sean los extremos reales,
        # y ser mas corta que el cierre del filtro sostenido tras un
        # giro (90 dps tardan ~1.9 s en bajar del umbral), para que
        # los extremos del giro anterior no contaminen la marcha.
        self._tiempo_ventana += dt
        self._ventana_giro.append((self._tiempo_ventana, velocidad))
        while (
            self._ventana_giro
            and self._tiempo_ventana - self._ventana_giro[0][0]
            > self._config.ventana_sesgo_s
        ):
            self._ventana_giro.popleft()

        # La primera muestra solo sirve para ARRANCAR la ventana: si el
        # modulo arranca en mitad de un giro, esa muestra la dejaria
        # "plana" a plena velocidad y aprenderia la rotacion como si
        # fuera sesgo.  Se inicializa y no se aprende nada.
        primera_muestra = len(self._ventana_giro) == 1
        maximo = max(valor for _, valor in self._ventana_giro)
        minimo = min(valor for _, valor in self._ventana_giro)
        amplitud_dps = maximo - minimo

        # Clasificacion de la situacion actual del giroscopio:
        #
        # * ``girando``: el filtro sostenido ve un giro de verdad.  Mientras
        #   esto dure NO se recalibra nada: aprender durante un giro
        #   confundiria la rotacion de la persona con el sesgo del sensor
        #   y el giro entero se daria por no hecho.
        # * ``plano``: la ventana casi no se abre: la senal esta quieta
        #   (reposo) o gira a velocidad constante (los extremos se juntan).
        # * ``oscilando``: la ventana esta bien abierta Y la senal esta
        #   DENTRO de ella: es la oscilacion del paso, no un salto.
        #   Estricta (el extremo actual es la propia senal): un giro que
        #   arranca tiene la senal pegada al maximo y no cuenta.
        reposo = self._config.velocidad_reposo_dps
        umbral = self._config.velocidad_minima_giro_dps
        girando = abs(self._sostenida) >= umbral
        plano = amplitud_dps <= reposo
        dentro_ventana = minimo < velocidad < maximo
        oscilando = amplitud_dps >= 2.0 * reposo and dentro_ventana

        # Valvula de seguridad contra el bloqueo del sesgo: si la senal
        # esta plana con el giro sostenido abierto DURANTE UN RATO, o es
        # un giro lentisimo que se alarga (se recupera solo al terminar)
        # o, sobre todo, un sesgo ya tan desviado que el propio error
        # mantiene el filtro sostenido abierto (entonces el sistema no
        # podria recalibrarse jamas y derivaria para siempre).  Un giro
        # normal pedido por la ruta (90 grados) termina mucho antes.
        if plano and girando:
            self._plano_girando_s += dt
        else:
            self._plano_girando_s = 0.0
        valvula = (
            plano
            and girando
            and self._plano_girando_s >= self._config.recalibracion_sesgo_s
        )

        # Aprendizaje del sesgo: con la senal plana se toma la lectura
        # actual (es el sesgo puro); con la oscilacion del paso se toma
        # el PUNTO MEDIO de los extremos de la ventana (el centro del
        # vaiven de los ultimos segundos).  Aprender de "cualquier
        # muestra en reposo" era el error viejo: con el MPU6050 en la
        # cintura la persona nunca esta quieta del todo, el sesgo se
        # aprendia del lado equivocado de la oscilacion y se re-aprendia
        # a si mismo (deriva -> falsos desvíos al girar a la izquierda).
        if (plano or oscilando) and (not girando or valvula) and not primera_muestra:
            objetivo = (
                0.5 * (maximo + minimo) if oscilando else velocidad
            )
            alfa_sesgo = min(dt / self._config.constante_sesgo_s, 1.0) if dt > 0 else 1.0
            self._sesgo += (objetivo - self._sesgo) * alfa_sesgo

        direccion = (velocidad - self._sesgo) * self._config.signo_derecha

        if direccion >= umbral:
            self._angulo_derecha += abs(direccion) * dt
        elif direccion <= -umbral:
            self._angulo_izquierda += abs(direccion) * dt

        # El rumbo (orientacion continua) recibe solo el giro
        # SOSTENIDO: con el sensor en la cintura, el balanceo al
        # caminar cambia de signo a cada paso y su media (con
        # tau_sostenida_giro_s) queda por debajo del umbral, mientras
        # que un giro de verdad mantiene la direccion el tiempo
        # suficiente para pasar el filtro.  Asi el torso que se
        # retuerce al caminar no desvia la ruta.  La decision la toma
        # la media, pero se integra la direccion instantaneous (no la
        # media), para no perder los primeros grados de cada giro.
        if dt > 0:
            alfa = min(dt / self._config.tau_sostenida_giro_s, 1.0)
            self._sostenida += (direccion - self._sostenida) * alfa
        if self._rumbo is not None and abs(self._sostenida) >= umbral:
            self._rumbo.girar(direccion * dt)

    @property
    def velocidad_derecha(self) -> float:
        """Ultima velocidad de giro medida (positiva = derecha)."""
        return self._velocidad_derecha

    def avanzar(self, derecha: bool) -> float:
        """Grados NETOS en el sentido pedido desde ``reiniciar()``.

        El giro hecho al lado CONTRARIO se resta: si la instruccion es
        "gira 90 a la izquierda" y antes se giraron 30 a la derecha,
        hacen falta 120 a la izquierda para acabar mirando donde toca.
        Con la regla vieja (mirar solo el acumulado del sentido pedido)
        esos 30 se ignoraban y el giro se daba por completo con la
        persona 30 grados desviada.  Asi el valor que se compara con el
        objetivo es siempre la rotacion REAL pendiente, que negativa
        significa "vas al lado contrario".
        """
        neto = self._angulo_derecha - self._angulo_izquierda
        return neto if derecha else -neto

    def reiniciar(self) -> None:
        """Pone ambos acumulados en cero y olvida el giro anterior."""
        self._angulo_derecha = 0.0
        self._angulo_izquierda = 0.0
        self._sostenida = 0.0
        self._gravedad_filtrada = None
        # La valvula de recalibracion cronometra un giro sostenido: cada
        # instruccion empieza con su propio cronometro.
        self._plano_girando_s = 0.0


class FiltroPaso:
    """Pasa-banda biquad (RBJ) para aislar la frecuencia del paso.

    La oscilacion de la aceleracion al caminar esta entre ``0,5`` y
    ``5`` Hz; todo lo demas (la gravedad, la postura que cambia, el
    balanceo lento, el temblor) queda fuera de banda y se anula solo:
    el filtro no tiene entrada en continua, asi que no hace falta
    perseguir una media local.

    Diseno del "cookbook" de Robert Bristow-Johnson (pasa-banda de
    ganancia pico 0 dB), sin scipy: solo ``sin``, ``cos`` y ``sinh``.
    Los coeficientes dependen de la frecuencia de muestreo, asi que se
    se recalculan si la frecuencia de muestreo cambia de forma
    significativa. El jitter normal de I2C no debe reiniciar el filtro
    ni alterar sus coeficientes muestra a muestra.

    Ojo: el pasa-banda tiene ceros en DC y en Nyquist, asi que una
    oscilacion justo a la mitad de la frecuencia de muestreo no
    aparece (por eso las pruebas usan periodos de 4 muestras a 10 Hz
    = 2,5 Hz, y no de 2).
    """

    def __init__(
        self,
        frecuencia_baja_hz: float,
        frecuencia_alta_hz: float,
        variacion_recalculo_pct: float,
    ) -> None:
        self._frecuencia_baja = frecuencia_baja_hz
        self._frecuencia_alta = frecuencia_alta_hz
        self._variacion_recalculo_pct = variacion_recalculo_pct
        self._fs: float | None = None
        self._b0 = self._b2 = 0.0
        self._a1 = self._a2 = 0.0
        self._x1 = self._x2 = 0.0
        self._y1 = self._y2 = 0.0
        self._sembrado = False

    def _disenar(self, fs: float) -> None:
        """Fija los coeficientes para la frecuencia de muestreo ``fs``."""
        # Guardas contra un dt enorme (fs por debajo del banda): el
        # extremo alto nunca puede llegar a Nyquist.
        alta = min(self._frecuencia_alta, 0.45 * fs)
        baja = min(self._frecuencia_baja, 0.5 * alta)
        if baja <= 0.0 or alta <= baja:
            baja = 0.1 * alta

        centro = math.sqrt(baja * alta)
        ancho = math.log2(alta / baja)
        w0 = 2.0 * math.pi * centro / fs
        # alpha = sin(w0)/(2Q) con Q = 1/(2*sinh(ln2/2*BW))  ==>
        alpha = math.sin(w0) * math.sinh(math.log(2.0) * 0.5 * ancho)

        b0, b1, b2 = alpha, 0.0, -alpha
        a0, a1, a2 = 1.0 + alpha, -2.0 * math.cos(w0), 1.0 - alpha
        self._b0 = b0 / a0
        self._b2 = b2 / a0
        self._a1 = a1 / a0
        self._a2 = a2 / a0

    def pasar(self, muestra: float, fs: float) -> float:
        """Devuelve la muestra filtrada (sin continua ni ruido de paso)."""
        if fs <= 0:
            return 0.0
        variacion_fs = (
            1.0
            if self._fs is None
            else abs(fs - self._fs) / self._fs
        )
        if self._fs is None or variacion_fs >= self._variacion_recalculo_pct:
            self._disenar(fs)
            self._fs = fs

        if not self._sembrado:
            # Arranque en continuo: la entrada actual ya es el estado
            # estacionario (el pasa-banda responde 0 a una continua),
            # asi que un sensor quieto en 1,15 g no genera transitorio
            # ni ningun pico fantasma.
            self._x1 = self._x2 = muestra
            self._y1 = self._y2 = 0.0
            self._sembrado = True
            return 0.0

        salida = (
            self._b0 * muestra
            + self._b2 * self._x2
            - self._a1 * self._y1
            - self._a2 * self._y2
        )
        self._x2, self._x1 = self._x1, muestra
        self._y2, self._y1 = self._y1, salida
        return salida


class ContadorPasos:
    """Detecta pasos por la oscilacion de la aceleracion al caminar.

    El sensor va montado en la cintura: al caminar la magnitud de la
    aceleracion OSCILA alrededor de una media que cambia con la
    postura (apoyar un pie, girar el torso, cargar algo).  Por eso la
    senal pasa antes por un **pasa-banda 0,5 - 5 Hz**
    (:class:`FiltroPaso`): solo queda la frecuencia del paso, y la
    gravedad, la postura y el balanceo lento se van solos.  Sin
    continua no hay media que perseguir, asi que tampoco hay pasos
    fantasma por una linea base inflada ("a veces no cuenta").

    Un pico es un cruce del **umbral adaptativo**
    ``max(umbral_paso_min_g, factor_umbral_paso * media_picos)``,
    con histéresis (hay que bajar a la mitad para rearmar) y un
    periodo refractario (``intervalo_paso_s``) contra el doble
    conteo.  Los picos CONTADOS alimentan la media de picos, de modo
    que el umbral se mantiene en la mitad de lo que de verdad produce
    la marcha de esa persona.

    La cadencia solo descarta picos demasiado cercanos al anterior
    (probable doble conteo), segun ``cadencia_tolerancia`` y la mediana
    de los intervalos recientes. Los cambios de ritmo mas lentos o mas
    rapidos se aceptan; las pausas se filtran con
    ``intervalo_paso_max_s``. Tras varios picos demasiado cercanos se
    rebasela la cadencia para adaptarse a un nuevo ritmo.

    El reloj del contador es la suma de los ``dt`` recibidos (no
    ``time.monotonic``): asi la deteccion es reproducible en las
    pruebas y no depende de la carga de la camara.
    """

    def __init__(self, config: Configuracion):
        self._config = config
        self._pasos = 0
        # Reloj propio (segundos acumulados): unico origen del tiempo
        # para intervalos y cadencia.
        self._tiempo = 0.0
        self._ultimo_paso: float | None = None
        self._paso_pendiente: tuple[float, float, float] | None = None
        self._filtro = FiltroPaso(
            config.frecuencia_paso_min_hz,
            config.frecuencia_paso_max_hz,
            config.variacion_recalculo_filtro_paso_pct,
        )
        # Pico en curso: hasta que no baja del umbral no se sabe su
        # altura (es la que entra en la media de picos).
        self._en_pico = False
        self._pico_actual = 0.0
        self._pico_contado = False
        self._picos: deque[float] = deque(maxlen=5)
        self._picos_por_paso: list[float] = []
        self._media_picos = 0.0
        # Intervalos de los ultimos pasos CONTADOS (los descartados no
        # entran): de ahi sale la cadencia con la que se juzga el
        # proximo paso.
        self._ritmo: deque[float] = deque(maxlen=5)
        self._cadencia_s: float | None = None
        self._rechazos = 0

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Cuenta un paso si la muestra trae una oscilacion de paso."""
        dt = min(dt, DT_MAXIMO_S)
        self._tiempo += dt

        fs = 1.0 / dt if dt > 0 else 100.0
        filtrada = self._filtro.pasar(lectura.magnitud_aceleracion, fs)
        umbral = self._umbral(filtrada)

        if self._en_pico:
            if filtrada > self._pico_actual:
                self._pico_actual = filtrada
            if filtrada <= umbral * 0.5:
                # Bajo la histéresis: el pico termino y se anota su
                # altura si el paso conto.
                if self._pico_contado:
                    self._picos.append(self._pico_actual)
                    self._media_picos = sum(self._picos) / len(self._picos)
                self._en_pico = False
                self._pico_contado = False
            return

        if filtrada >= umbral:
            self._valorar_pico(filtrada, umbral)

    def _umbral(self, _filtrada: float) -> float:
        """Umbral adaptativo: el piso o la mitad de los picos reales."""
        return max(
            self._config.umbral_paso_min_g,
            self._config.factor_umbral_paso * self._media_picos,
        )

    def _valorar_pico(self, pico: float, umbral: float) -> None:
        """Decide si este pico es un paso (y lo cuenta o lo descarta)."""
        # El pico se abre aunque no se cuente: asi un pico descartado
        # no se re-evalua muestra a muestra hasta que baje.
        self._en_pico = True
        self._pico_actual = pico
        self._pico_contado = False

        if self._ultimo_paso is None:
            # Primer paso (o el primero de un tramo nuevo): siempre
            # cuenta, todavia no hay cadencia con la que compararlo.
            self._contar(pico, umbral, None)
            return

        intervalo = self._tiempo - self._ultimo_paso
        if intervalo < self._config.intervalo_paso_s:
            # Periodo refractario: ni se cuenta ni se juzga (el doble
            # conteo de un mismo pico no debe tocar la cadencia).
            return

        if self._paso_pendiente is not None:
            if (
                self._config.intervalo_paso_max_s <= 0.0
                or intervalo <= self._config.intervalo_paso_max_s
            ):
                pico_pendiente, _, _ = self._paso_pendiente
                self._paso_pendiente = None
                self._picos.append(pico_pendiente)
                self._picos_por_paso.append(pico_pendiente)
                self._media_picos = sum(self._picos) / len(self._picos)
                self._pasos += 1
                print(
                    f"PASO: {self._pasos} "
                    "(reanudacion confirmada por el siguiente paso)"
                )
            else:
                print("PASO PENDIENTE DESCARTADO: no se reanudo la marcha")
                self._paso_pendiente = None

        motivo = self._descarte(intervalo)
        if motivo is None:
            self._contar(pico, umbral, intervalo)
        else:
            self._descartar(pico, umbral, intervalo, motivo)

    def _descarte(self, intervalo: float) -> str | None:
        """Motivo por el que el pico no vale como paso, o ``None``."""
        piso = self._config.intervalo_paso_max_s
        if piso > 0.0 and intervalo > piso:
            return "sin_marcha"
        if len(self._ritmo) < self._config.cadencia_intervalos_minimos:
            return None  # todavia no hay cadencia que juzgar
        mediana = self._mediana(self._ritmo)
        if mediana <= 0.0:
            return None
        intervalo_minimo = (1.0 - self._config.cadencia_tolerancia) * mediana
        if intervalo < intervalo_minimo:
            return "cadencia"
        return None

    @staticmethod
    def _mediana(valores: deque[float]) -> float:
        ordenados = sorted(valores)
        return ordenados[len(ordenados) // 2]

    def _contar(self, pico: float, umbral: float, intervalo: float | None) -> None:
        self._pasos += 1
        self._picos_por_paso.append(pico)
        self._pico_contado = True
        if intervalo is not None:
            self._ritmo.append(intervalo)
            self._cadencia_s = self._mediana(self._ritmo)
        self._rechazos = 0
        self._ultimo_paso = self._tiempo
        ritmo = "" if intervalo is None else f", cada {intervalo:.2f} s"
        # El pico y el umbral se imprimen para poder calibrarlos en la
        # casa: si los pasos reales quedan por debajo del umbral, no se
        # cuentan; si el umbral esta muy bajo, entra ruido.
        print(
            f"PASO: {self._pasos} (pico {pico:+.2f} g, "
            f"umbral {umbral:.2f} g{ritmo})"
        )

    def _descartar(
        self, pico: float, umbral: float, intervalo: float, motivo: str
    ) -> None:
        self._rechazos += 1
        self._ultimo_paso = self._tiempo
        if motivo == "sin_marcha":
            # Se retiene provisionalmente el primer pico al reanudar.
            # Solo se acredita si aparece otro dentro de la ventana de
            # marcha; un movimiento aislado durante la pausa no cuenta.
            self._paso_pendiente = (pico, umbral, self._tiempo)
            self._ritmo.clear()
            self._cadencia_s = None
            self._rechazos = 0
            print(
                "PASO PENDIENTE: reanudacion; se confirmara con el "
                "siguiente paso"
            )
        else:
            mediana = self._mediana(self._ritmo)
            print(
                f"PASO DESCARTADO: cadencia {intervalo:.2f} s; "
                f"separacion minima "
                f"{(1.0 - self._config.cadencia_tolerancia) * mediana:.2f} s "
                f"(ritmo {mediana:.2f} s)"
            )

        if self._rechazos >= self._config.rechazos_rebasar:
            # La cadencia ya no describe la señal: se olvida y el
            # siguiente pico arranca un ritmo nuevo.
            self._ritmo.clear()
            self._cadencia_s = None
            self._rechazos = 0
            print("RITMO REBASELADO: los ultimos pasos no encajaban")

    @property
    def pasos(self) -> int:
        """Pasos contados desde la ultima vez que se reinicio."""
        return self._pasos

    @property
    def cadencia_s(self) -> float | None:
        """Mediana reciente de segundos por paso contado, si está disponible."""
        return self._cadencia_s

    @property
    def segundos_desde_ultimo_paso(self) -> float:
        """Segundos acumulados desde el ultimo paso detectado (o 0.0)."""
        if self._ultimo_paso is None:
            return 0.0
        return max(0.0, self._tiempo - self._ultimo_paso)

    def avanzar(self) -> int:
        """Devuelve los pasos contados hasta ahora."""
        return self._pasos

    @property
    def picos_por_paso(self) -> tuple[float, ...]:
        """Picos filtrados (g), en el mismo orden que los pasos contados."""
        return tuple(self._picos_por_paso)

    @property
    def media_picos_g(self) -> float:
        """Media reciente de picos ya finalizados, en g."""
        return self._media_picos

    def reiniciar(self) -> None:
        """Pone el contador en cero.

        Se queda con la cadencia y la media de picos (son de la
        persona, no del tramo) y borra el ultimo paso: el primero del
        tramo nuevo cuenta sin juzgar, como siempre.
        """
        self._pasos = 0
        self._ultimo_paso = None
        self._paso_pendiente = None
        self._en_pico = False
        self._pico_contado = False
        self._rechazos = 0
        self._picos_por_paso.clear()
