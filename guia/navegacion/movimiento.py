"""Contadores de movimiento basados en el MPU6050.

El giroscopio no mide angulos: mide *velocidad angular* (grados por
segundo).  Para saber cuantos grados ha girado la persona hace falta
integrar esa velocidad a una frecuencia fija.  Por eso
:class:`MuestreadorMPU` lee el sensor en su propio hilo (100 veces por
segundo) y le pasa cada lectura a los oyentes:

* :class:`MedidorGiro` integra la velocidad angular en grados.
* :class:`ContadorPasos` detecta la oscilacion de la aceleracion al
  caminar (contra una media local, no contra un reposo congelado).

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
        self._sesgo = config.offset_giroscopio
        self._velocidad_derecha = 0.0
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

    def _velocidad_de_giro(self, lectura: LecturaMPU) -> float:
        """Velocidad de giro sobre la vertical, positiva hacia la derecha."""
        modulo = lectura.magnitud_aceleracion
        if modulo >= MAGNITUD_MINIMA_VERTICAAL_G:
            proyectada = (
                lectura.ax * lectura.gx
                + lectura.ay * lectura.gy
                + lectura.az * lectura.gz
            ) / modulo
            return -proyectada
        # Sin gravedad fiable (caida libre): se asume el montaje plano.
        return -lectura.gz

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
        velocidad = self._velocidad_de_giro(lectura)
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
        # La valvula de recalibracion cronometra un giro sostenido: cada
        # instruccion empieza con su propio cronometro.
        self._plano_girando_s = 0.0


class ContadorPasos:
    """Detecta pasos por la oscilacion de la aceleracion al caminar.

    El sensor va montado en la cintura: al caminar la magnitud de la
    aceleracion OSCILA alrededor de una media que cambia con la
    postura (apoyar un pie, girar el torso, cargar algo).  Por eso el
    umbral no se fija contra un reposo congelado sino contra una
    **media local** (pasa-largas con ``tau_media_paso_s``): sigue la
    postura en segundos, pero no la oscilacion del paso (2 a 6 Hz),
    que es justo lo que hay que detectar.  Con la linea base vieja la
    marcha la iba inflando hasta dejar los pasos reales debajo del
    umbral ("a veces no cuenta").

    Un paso es un cruce de ``delta_paso_g`` por encima de esa media,
    con histéresis (hay que volver a bajar para rearmar) y un tiempo
    refractario (``intervalo_paso_s``) contra el doble conteo.  Una
    magnitud ALTA SOSTENIDA no genera cruces: sin oscilacion no hay
    pasos, asi que tampoco hay pasos fantasma.

    Ademas, un cruce con mas de ``intervalo_paso_max_s`` desde el
    anterior se descarta: no hay ritmo de marcha detras (la persona
    estaba parada y movio el pie), y esos pasos de mas hacian que el
    tramo terminara antes de tiempo.
    """

    def __init__(self, config: Configuracion):
        self._config = config
        self._pasos = 0
        self._ultimo_paso = 0.0
        self._media: float | None = None
        self._en_pico = False
        # El primer paso despues de ``reiniciar`` siempre cuenta: no
        # hay cadencia previa con la que compararlo todavia.
        self._arranque = True

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Cuenta un paso si la muestra trae una oscilacion de paso."""
        magnitud = lectura.magnitud_aceleracion

        # La primera muestra fija la media inicial (evita el conteo
        # fantasma del arranque mientras se manipula el equipo).
        if self._media is None:
            self._media = magnitud

        # Senal de paso: cuanto se separa la magnitud de su media
        # local.  Se mide ANTES de actualizar la media, para que el
        # pico actual no se coma su propio umbral.
        oscilacion = magnitud - self._media

        if dt > 0:
            alfa = min(dt / self._config.tau_media_paso_s, 1.0)
            self._media += (magnitud - self._media) * alfa

        delta = self._config.delta_paso_g
        ahora = time.monotonic()

        if self._en_pico:
            # Para volver a contar, la oscilacion tiene que bajar
            # (histéresis a la mitad del umbral).
            if oscilacion <= delta * 0.5:
                self._en_pico = False
        elif (
            oscilacion >= delta
            and ahora - self._ultimo_paso >= self._config.intervalo_paso_s
        ):
            intervalo = ahora - self._ultimo_paso
            maximo = self._config.intervalo_paso_max_s
            if not self._arranque and maximo > 0.0 and intervalo > maximo:
                # Paso suelto: sin ritmo de marcha detras (la persona
                # estaba parada y movio el pie, o el sensor registro
                # un movimiento suelto).  No suma al tramo, pero el
                # ritmo se reinicia para no descartar tambien el
                # primer paso real despues de la pausa.
                self._ultimo_paso = ahora
                self._en_pico = True
                print(
                    f"PASO DESCARTADO: {intervalo:.1f} s sin marcha "
                    f"(maximo {maximo:.1f} s)"
                )
            else:
                ritmo = "" if self._arranque else f", cada {intervalo:.2f} s"
                self._pasos += 1
                self._arranque = False
                self._ultimo_paso = ahora
                self._en_pico = True
                # Se imprime la oscilacion junto al paso para poder
                # calibrar el umbral (delta_paso_g) en la casa: si los
                # pasos reales quedan por debajo del umbral, no se
                # cuentan.  El intervalo deja ver en consola si un paso
                # salio pegado al anterior (doble conteo).
                print(
                    f"PASO: {self._pasos} (pico {oscilacion:+.2f} g, "
                    f"umbral {delta:.2f} g{ritmo})"
                )

    @property
    def pasos(self) -> int:
        """Pasos contados desde la ultima vez que se reinicio."""
        return self._pasos

    def avanzar(self) -> int:
        """Devuelve los pasos contados hasta ahora."""
        return self._pasos

    def reiniciar(self) -> None:
        """Pone el contador en cero (la media local se conserva)."""
        self._pasos = 0
        self._ultimo_paso = 0.0
        self._en_pico = False
        self._arranque = True
