"""Generacion y reproduccion del beep de proximidad."""

from __future__ import annotations

import math
import struct
import subprocess
import threading
import wave
from pathlib import Path


class ReproductorBeep:
    """Crea ``beep.wav`` una vez y lo reproduce sin superponerse."""

    def __init__(
        self,
        carpeta: Path,
        frecuencia_hz: float = 1000.0,
        duracion_s: float = 0.08,
        frecuencia_muestreo: int = 44100,
        amplitud: int = 15000,
    ):
        self._ruta = Path(carpeta) / "beep.wav"
        self._proceso: subprocess.Popen | None = None
        self._candado = threading.Lock()
        self._generar(frecuencia_hz, duracion_s, frecuencia_muestreo, amplitud)

    def _generar(
        self, frecuencia: float, duracion: float, muestreo: int, amplitud: int
    ) -> None:
        muestras = int(muestreo * duracion)
        onda = (
            int(amplitud * math.sin(2 * math.pi * frecuencia * i / muestreo))
            for i in range(muestras)
        )
        datos = struct.pack(f"<{muestras}h", *onda)

        with wave.open(str(self._ruta), "wb") as archivo:
            archivo.setnchannels(1)
            archivo.setsampwidth(2)
            archivo.setframerate(muestreo)
            archivo.writeframes(datos)

    @property
    def sonando(self) -> bool:
        """``True`` mientras el beep anterior sigue en marcha."""
        return self._proceso is not None and self._proceso.poll() is None

    def reproducir(self) -> None:
        """Lanza el beep (no interrumpe uno que ya este sonando)."""
        with self._candado:
            if self.sonando:
                return
            try:
                self._proceso = subprocess.Popen(
                    ["aplay", "-q", str(self._ruta)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as error:
                print("ERROR BEEP:", error)

    def esperar(self, espera_s: float = 2.0) -> None:
        """Espera a que el ultimo beep termine (para cerrar limpio)."""
        with self._candado:
            if self.sonando:
                try:
                    self._proceso.wait(timeout=espera_s)
                except Exception:
                    pass
