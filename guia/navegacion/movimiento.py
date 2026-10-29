"""Contadores de movimiento basados en el MPU6050.

El giroscopio no mide angulos: mide *velocidad angular* (grados por
segundo).  Para saber cuantos grados ha girado la persona hace falta
integrar esa velocidad a una frecuencia fija.  Por eso
:class:`MuestreadorMPU` lee el sensor en su propio hilo (100 veces por
segundo) y le pasa cada lectura a los oyentes:

* :class:`MedidorGiro` integra la velocidad angular en grados.
* :class:`ContadorPasos` detecta los picos de aceleracion al caminar.

Asi un giro lento, una pausa del bucle principal o la carga de la
camara ya no hacen perder la cuenta de grados.
"""

from __future__ import annotations

import math
import threading
import time

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
        offset fijo que cambia con la temperatura.  Solo se integra
        cuando la persona gira de verdad (por encima de la velocidad
        minima), asi el temblor de la mano no cuenta como giro.
        """
        dt = min(dt, DT_MAXIMO_S)
        velocidad = self._velocidad_de_giro(lectura)
        self._velocidad_derecha = velocidad

        if abs(velocidad - self._sesgo) <= self._config.velocidad_reposo_dps:
            if dt > 0:
                alfa = dt / self._config.constante_sesgo_s
                self._sesgo += (velocidad - self._sesgo) * alfa

        direccion = (velocidad - self._sesgo) * self._config.signo_derecha
        umbral = self._config.velocidad_minima_giro_dps

        if direccion >= umbral:
            self._angulo_derecha += abs(direccion) * dt
        elif direccion <= -umbral:
            self._angulo_izquierda += abs(direccion) * dt

        # El rumbo (orientacion continua) recibe solo los giros reales:
        # por debajo del umbral es temblor, y temblor no desvia la
        # ruta.  La referencia se ancla en cada senal orientadora.
        if self._rumbo is not None and abs(direccion) >= umbral:
            self._rumbo.girar(direccion * dt)

    @property
    def velocidad_derecha(self) -> float:
        """Ultima velocidad de giro medida (positiva = derecha)."""
        return self._velocidad_derecha

    def avanzar(self, derecha: bool) -> float:
        """Grados acumulados en el sentido pedido desde ``reiniciar()``."""
        return self._angulo_derecha if derecha else self._angulo_izquierda

    def reiniciar(self) -> None:
        """Pone ambos acumulados en cero."""
        self._angulo_derecha = 0.0
        self._angulo_izquierda = 0.0


class ContadorPasos:
    """Detecta pasos por picos de aceleracion.

    El sensor sin calibrar no marca exactamente 1 g en reposo (puede
    marcar 1.05, 1.15...), asi que el umbral no es un valor absoluto:
    se aprende una *linea base* de reposo y un paso es un pico claro
    por encima de ella.  Ademas solo cuenta cuando la magnitud *cruza*
    el umbral de abajo hacia arriba (modo borde, no nivel): si la
    aceleracion se queda alta sostenida, no vuelve a contar pasos
    fantasma cada ``intervalo_paso_s`` segundos.
    """

    def __init__(self, config: Configuracion):
        self._config = config
        self._pasos = 0
        self._ultimo_paso = 0.0
        self._reposo: float | None = None
        self._en_pico = False

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Cuenta un paso si la muestra trae un pico de aceleracion."""
        magnitud = lectura.magnitud_aceleracion
        ahora = time.monotonic()

        # La primera muestra fija el reposo inicial (evita el conteo
        # fantasma del arranque mientras se manipula el equipo).
        if self._reposo is None:
            self._reposo = magnitud

        delta = self._config.delta_paso_g
        umbral_alto = self._reposo + delta
        umbral_bajo = self._reposo + delta * 0.5

        if self._en_pico:
            # Para volver a contar, la magnitud tiene que bajar hysteresis.
            if magnitud <= umbral_bajo:
                self._en_pico = False
        elif (
            magnitud >= umbral_alto
            and ahora - self._ultimo_paso >= self._config.intervalo_paso_s
        ):
            self._pasos += 1
            self._ultimo_paso = ahora
            self._en_pico = True
            # El pico se imprime junto al paso para poder calibrar el
            # umbral (delta_paso_g) en la casa: si el pico de un paso
            # real queda por debajo del umbral, el paso no se cuenta.
            print(f"PASO: {self._pasos} (pico {magnitud:.2f} g, umbral {umbral_alto:.2f} g)")

        # Linea base que sigue la deriva lenta del sensor; solo se
        # actualiza fuera de los picos para no subir con cada paso.
        if not self._en_pico and dt > 0:
            alfa = min(dt / self._config.tau_reposo_paso_s, 1.0)
            self._reposo += (magnitud - self._reposo) * alfa

    @property
    def pasos(self) -> int:
        """Pasos contados desde la ultima vez que se reinicio."""
        return self._pasos

    def avanzar(self) -> int:
        """Devuelve los pasos contados hasta ahora."""
        return self._pasos

    def reiniciar(self) -> None:
        """Pone el contador en cero (la linea base se conserva)."""
        self._pasos = 0
        self._ultimo_paso = 0.0
        self._en_pico = False
