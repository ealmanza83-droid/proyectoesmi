"""Deteccion de caidas a partir del acelerometro.

Una caida se manifiesta como un cambio *brusco* de la aceleracion: el
cuerpo pierde el equilibrio y golpea el suelo.  El detector vigila dos
senales en cada muestra:

* que la magnitud se despegue de 1 g (impacto fuerte);
* que haya cambiado mucho respecto a hace un instante (golpe
  repentino, como el de un cuerpo que cae).

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

    @property
    def hay_caida(self) -> bool:
        """``True`` desde que hubo una caida hasta que se avisa."""
        return self._detectada.is_set()

    def reconocer(self) -> None:
        """Limpia el aviso (la aplicacion ya lo anuncio una vez)."""
        self._detectada.clear()

    def en_muestra(self, lectura: LecturaMPU, dt: float) -> None:
        """Vigila una lectura del sensor (la llama el muestreador)."""
        ahora = time.monotonic()

        if ahora < self._enfriamiento_hasta:
            return  # misma caida: no volver a avisar todavia

        magnitud = lectura.magnitud_aceleracion

        if self._enfriamiento_hasta and ahora >= self._enfriamiento_hasta:
            # Primera muestra despues del enfriamiento: la referencia
            # se refresca para no comparar contra el valor de antes
            # de la caida y disparar una alerta fantasma.
            self._enfriamiento_hasta = 0.0
            self._referencia = magnitud
            self._momento_referencia = ahora
            return

        impacto = (
            abs(magnitud - ACELERACION_REPOSO_G) >= self._config.umbral_caida_g
        )
        salto = (
            abs(magnitud - self._referencia)
            >= self._config.umbral_cambio_caida_g
        )

        if impacto or salto:
            self._enfriamiento_hasta = ahora + self._config.enfriamiento_caida_s
            self._detectada.set()
            print("CAIDA DETECTADA")
            return

        # Sin alerta: la referencia se refresca poco a poco para seguir
        # la postura y no comparar nunca contra un valor demasiado viejo
        # (asi un movimiento lento no se confunde con una caida).
        if ahora - self._momento_referencia >= self._config.ventana_cambio_caida_s:
            self._referencia = magnitud
            self._momento_referencia = ahora
