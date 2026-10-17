"""Generacion y reproduccion del beep de proximidad y la alarma de caida.

Hay dos sonidos con dos comportamientos distintos:

* el **beep corto** de proximidad: un ``beep.wav`` de 80 ms que se
  repite mas o menos segun lo cerca que este el obstaculo;
* la **alarma continua** de caida: un ``alarma.wav`` de tono constante
  que se repite sin cortes hasta que alguien la silencia.  Tiene
  prioridad: mientras suena, los pitidos de proximidad se callan.
"""

from __future__ import annotations

import math
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path

# El tono de la alarma de caida es mas agudo que el beep de
# proximidad para que se distinga sin Ambiguedad.
FRECUENCIA_ALARMA_HZ = 1200.0
DURACION_ALARMA_S = 1.0


class ReproductorBeep:
    """Crea los wav una vez y los reproduce sin superponerse."""

    def __init__(
        self,
        carpeta: Path,
        frecuencia_hz: float = 1000.0,
        duracion_s: float = 0.08,
        frecuencia_muestreo: int = 44100,
        amplitud: int = 15000,
    ):
        self._ruta = Path(carpeta) / "beep.wav"
        self._ruta_alarma = Path(carpeta) / "alarma.wav"
        self._proceso: subprocess.Popen | None = None
        self._proceso_alarma: subprocess.Popen | None = None
        self._hilo_alarma: threading.Thread | None = None
        self._alarma_activa = threading.Event()
        self._candado = threading.Lock()
        self._generar(
            self._ruta, frecuencia_hz, duracion_s, frecuencia_muestreo, amplitud
        )
        self._generar(
            self._ruta_alarma,
            FRECUENCIA_ALARMA_HZ,
            DURACION_ALARMA_S,
            frecuencia_muestreo,
            amplitud,
        )

    def _generar(
        self, ruta: Path, frecuencia: float, duracion: float, muestreo: int, amplitud: int
    ) -> None:
        muestras = int(muestreo * duracion)
        onda = (
            int(amplitud * math.sin(2 * math.pi * frecuencia * i / muestreo))
            for i in range(muestras)
        )
        datos = struct.pack(f"<{muestras}h", *onda)

        with wave.open(str(ruta), "wb") as archivo:
            archivo.setnchannels(1)
            archivo.setsampwidth(2)
            archivo.setframerate(muestreo)
            archivo.writeframes(datos)

    @property
    def sonando(self) -> bool:
        """``True`` mientras el beep corto anterior sigue en marcha."""
        return self._proceso is not None and self._proceso.poll() is None

    @property
    def alarma_activa(self) -> bool:
        """``True`` mientras dure la alarma continua de caida."""
        return self._alarma_activa.is_set()

    def reproducir(self) -> None:
        """Lanza el beep corto (no interrumpe uno que ya este sonando)."""
        with self._candado:
            # La alarma de caida tiene prioridad sobre la proximidad.
            if self.sonando or self.alarma_activa:
                return
            try:
                self._proceso = subprocess.Popen(
                    ["aplay", "-q", str(self._ruta)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as error:
                print("ERROR BEEP:", error)

    def activar_alarma(self, duracion_s: float | None = None) -> None:
        """Enciende el tono constante de caida.

        Suena hasta ``silenciar_alarma()`` o, si se indica
        ``duracion_s``, hasta agotar ese tiempo.
        """
        with self._candado:
            if self.alarma_activa:
                return
            self._alarma_activa.set()
            self._hilo_alarma = threading.Thread(
                target=self._repetir_alarma,
                args=(duracion_s,),
                name="alarma-caida",
                daemon=True,
            )
            self._hilo_alarma.start()

    def _repetir_alarma(self, duracion_s: float | None) -> None:
        """Repite el tono hasta que se silencie o se acabe el tiempo."""
        inicio = time.monotonic()

        while self._alarma_activa.is_set():
            if duracion_s is not None and time.monotonic() - inicio >= duracion_s:
                break
            try:
                proceso = subprocess.Popen(
                    ["aplay", "-q", str(self._ruta_alarma)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._proceso_alarma = proceso
                # Se vigila de cerca el proceso para cortar el tono en
                # cuanto se pida silencio (sin esperar al final del wav).
                while proceso.poll() is None and self._alarma_activa.is_set():
                    time.sleep(0.02)
                if proceso.poll() is None:
                    proceso.terminate()
            except Exception as error:
                print("ERROR ALARMA:", error)
                break

        self._alarma_activa.clear()
        self._proceso_alarma = None

    def silenciar_alarma(self, espera_s: float = 2.0) -> None:
        """Corta el tono constante y espera a que el hilo termine."""
        self._alarma_activa.clear()
        with self._candado:
            proceso = self._proceso_alarma
            hilo = self._hilo_alarma
            if proceso is not None and proceso.poll() is None:
                proceso.terminate()
        if hilo is not None and hilo.is_alive():
            hilo.join(espera_s)

    def esperar(self, espera_s: float = 2.0) -> None:
        """Silencia todo y espera al ultimo sonido (para cerrar limpio)."""
        self.silenciar_alarma(espera_s)
        with self._candado:
            if self.sonando:
                try:
                    self._proceso.wait(timeout=espera_s)
                except Exception:
                    pass
