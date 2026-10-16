"""Locucion en voz alta con Piper y reproduccion con ``aplay``.

Las frases se generan una sola vez y se guardan en cache: asi
repetir "Buscando la senal durante un minuto" no vuelve a sintetizar.
"""

from __future__ import annotations

import subprocess
import threading
import unicodedata
import wave
from pathlib import Path

from piper import PiperVoice


def nombre_de_archivo(texto: str) -> str:
    """Convierte cualquier frase en un nombre de archivo seguro."""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    limpio = "".join(c if c.isalnum() else "_" for c in texto.lower())
    return limpio[:80] or "audio"


class Locutor:
    """Sintetiza y reproduce texto; expone ``hablando`` para otros hilos."""

    def __init__(self, ruta_voz: Path, carpeta_audio: Path):
        print("CARGANDO PIPER")
        self._voz = PiperVoice.load(str(ruta_voz))
        self._carpeta = Path(carpeta_audio)
        self._cache: dict[str, Path] = {}
        self._candado = threading.Lock()
        # Evento compartido con la alerta ultrasonica: si la voz habla,
        # los pitidos se hacen a un lado.
        self.hablando = threading.Event()
        print("PIPER OK")

    @property
    def ocupado(self) -> bool:
        """``True`` mientras se esta reproduciendo voz."""
        return self.hablando.is_set()

    def hablar(self, texto: str) -> bool:
        """Reproduce ``texto`` y devuelve si pudo hacerlo."""
        print("VOZ:", texto)
        self.hablando.set()
        try:
            ruta = self._obtener_audio(texto)
            subprocess.run(
                ["aplay", "-q", str(ruta)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as error:
            print("ERROR DE AUDIO:", error)
            return False
        finally:
            self.hablando.clear()

    def _obtener_audio(self, texto: str) -> Path:
        """Devuelve la ruta del wav, generandolo solo si hace falta."""
        with self._candado:
            if texto in self._cache:
                return self._cache[texto]

            ruta = self._carpeta / f"{nombre_de_archivo(texto)}.wav"
            with wave.open(str(ruta), "wb") as archivo:
                self._voz.synthesize_wav(texto, archivo)

            self._cache[texto] = ruta
            return ruta

    def limpiar_cache(self) -> None:
        """Borra los audios generados en la carpeta temporal."""
        with self._candado:
            for ruta in self._cache.values():
                try:
                    ruta.unlink(missing_ok=True)
                except OSError:
                    pass
            self._cache.clear()
