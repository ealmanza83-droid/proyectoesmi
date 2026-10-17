"""Deteccion de caidas a partir del acelerometro.

Una caida se manifiesta como un cambio *brusco* de la aceleracion: el
cuerpo pierde el equilibrio y golpea el suelo.  El detector vigila dos
senales en cada muestra:

* que la magnitud se despegue de 1 g (impacto fuerte);
* que haya cambiado mucho respecto a hace un instante (golpe
  repentino, como el de un cuerpo que cae).

Para no alarmar por nada se piden ademas dos salvaguardas:

* el cambio tiene que durar varias muestras seguidas (un glitch
  electrico del I2C dura una sola lectura; un golpe real dura decenas
  de milisegundos);
* hay un periodo de gracia al arrancar, porque manipular el equipo al
  ponerse la guia tambien produce picos grandes.

Al detectarla levanta un evento que la aplicacion transforma en un beep
constante y un aviso por voz.  Tras cada alerta hay un enfriamiento
para no volver a avisar por el mismo golpe.
"""

from __future__ import annotations

import threading
import time

from ..configuracion import Configuracion
from ..hardware.mpu6050 import LecturaMPU

# La magnitud de la aceleracion vale 1 g cuando la persona esta quieta
# (sea de pie, sentada o tumbada: la magnitud no depende de la postura).
ACELERACION_REPOSO_G = 1.0


class DetectorCaida:
    """Analiza cada muestra y avisa cuando la persona cae."""

    def __init__(self, config: Configuracion):
        self._config = config
        self._detectada = threading.Event()
        self._referencia = ACELERACION_REPOSO_G
        self._momento_referencia = 0.0
        self._enfriamiento_hasta = 0.0
        self._inicio_gracia = time.monotonic()
        self._muestras_bruscas = 0

    @property
    def hay_caida(self) -> bool:
        """``True`` desde que hubo una caida hasta que se avisa."""
        return self._detectada.is_set()

    def reconocer(self) -> None:
        """Limpia el aviso (la aplicacion ya lo anuncio una vez)."""
        self._detectada.clear()

    def reiniciar_gracia(self) -> None:
        """Vuelve a dar un margen sin alertas (arranque del bucle)."""
        self._inicio_gracia = time.monotonic()

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Vigila una lectura del sensor (la llama el muestreador)."""
        ahora = time.monotonic()
        magnitud = lectura.magnitud_aceleracion

        en_enfriamiento = ahora < self._enfriamiento_hasta
        en_gracia = (ahora - self._inicio_gracia) < self._config.gracia_caida_s

        if en_enfriamiento or en_gracia:
            # O esta sonando la alarma o es el arranque (manejo del
            # equipo): solo mantener la referencia al dia.
            self._referencia = magnitud
            self._momento_referencia = ahora
            self._muestras_bruscas = 0
            return

        impacto = (
            abs(magnitud - ACELERACION_REPOSO_G) >= self._config.umbral_caida_g
        )
        salto = (
            abs(magnitud - self._referencia)
            >= self._config.umbral_cambio_caida_g
        )

        if impacto or salto:
            # El cambio tiene que repetirse en muestras seguidas para
            # descartar un glitch aislado del bus I2C.
            self._muestras_bruscas += 1
            if self._muestras_bruscas >= self._config.muestras_caida_requeridas:
                self._enfriamiento_hasta = ahora + self._config.enfriamiento_caida_s
                self._detectada.set()
                self._muestras_bruscas = 0
                print("CAIDA DETECTADA")
            # Mientras se sospecha, la referencia no se mueve (si no,
            # el golpe se redefiniria como normal a mitad de camino).
            return

        # Sin alerta: la referencia se refresca poco a poco para seguir
        # la postura y no comparar nunca contra un valor demasiado viejo
        # (asi un movimiento lento no se confunde con una caida).
        self._muestras_bruscas = 0
        if ahora - self._momento_referencia >= self._config.ventana_cambio_caida_s:
            self._referencia = magnitud
            self._momento_referencia = ahora
