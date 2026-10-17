"""Escucha continua del microfono con reconocimiento de voz (Vosk).

El microfono corre en su propio hilo y solo publica transcripciones
en ``eventos``; quien decide que hacer con ellas es la aplicacion.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from enum import Enum, auto

import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

from ..configuracion import Configuracion
from ..dominio.comandos import contiene_despertador
from .gramaticas import GRAMATICA_COMANDO, GRAMATICA_DESPERTADOR, GRAMATICA_DESTINO


class ModoMicrofono(Enum):
    """Que esta escuchando el sistema en este momento."""

    APAGADO = auto()
    DESPERTADOR = auto()
    DESTINO = auto()
    COMANDO = auto()


# Gramatica que corresponde a cada modo.
GRAMATICAS: dict[ModoMicrofono, str] = {
    ModoMicrofono.DESPERTADOR: GRAMATICA_DESPERTADOR,
    ModoMicrofono.DESTINO: GRAMATICA_DESTINO,
    ModoMicrofono.COMANDO: GRAMATICA_COMANDO,
}


@dataclass(frozen=True)
class EventoVoz:
    """Transcripcion reconocida y el modo en que se escucho."""

    modo: ModoMicrofono
    texto: str


def _seleccionar_microfono(config: Configuracion) -> int:
    """Elige el microfono USB mas probable si no esta fijado en config."""
    if config.microfono_id is not None:
        return config.microfono_id

    pistas = (("usb", 3), ("microphone", 2), ("mic", 1), ("headset", 1))
    candidatos: list[tuple[int, int]] = []

    for indice, dispositivo in enumerate(sd.query_devices()):
        if dispositivo["max_input_channels"] <= 0:
            continue
        nombre = dispositivo["name"].lower()
        puntuacion = sum(peso for palabra, peso in pistas if palabra in nombre)
        if puntuacion:
            candidatos.append((puntuacion, indice))

    if candidatos:
        return max(candidatos)[1]

    return int(sd.default.device[0])


class Microfono:
    """Captura audio y publica transcripciones en ``eventos``."""

    def __init__(self, config: Configuracion):
        self._config = config
        self.eventos: queue.Queue[EventoVoz] = queue.Queue()
        self._cola_audio: queue.Queue[bytes] = queue.Queue()
        self._modo = ModoMicrofono.APAGADO
        self._candado = threading.Lock()
        self._detener = threading.Event()
        self._hilo = threading.Thread(
            target=self._bucle, name="microfono", daemon=True
        )

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------
    @property
    def modo(self) -> ModoMicrofono:
        """Modo de escucha actual."""
        with self._candado:
            return self._modo

    def cambiar_modo(self, modo: ModoMicrofono) -> None:
        """Cambia la escucha y descarta audio viejo (evita ecos)."""
        with self._candado:
            self._modo = modo
        _vacer(self._cola_audio)

    def iniciar(self) -> None:
        """Arranca el hilo de escucha una sola vez."""
        if not self._hilo.is_alive():
            self._hilo.start()

    def detener(self, espera_s: float = 2.0) -> None:
        """Pide la salida del hilo y espera a que termine."""
        self._detener.set()
        if self._hilo.is_alive():
            self._hilo.join(espera_s)

    # ------------------------------------------------------------------
    # Hilo de captura
    # ------------------------------------------------------------------
    def _bucle(self) -> None:
        SetLogLevel(-1)

        if not self._config.ruta_vosk.exists():
            print(f"ADVERTENCIA: no se encontro el modelo Vosk en {self._config.ruta_vosk}")
            return

        try:
            modelo = Model(str(self._config.ruta_vosk))
            dispositivo = _seleccionar_microfono(self._config)
            info = sd.query_devices(dispositivo, "input")

            frecuencia = int(info.get("default_samplerate", 16000) or 16000)
            if frecuencia <= 0:
                frecuencia = 16000

            print(f"MICROFONO: {info['name']} | FRECUENCIA: {frecuencia}")

            with sd.RawInputStream(
                samplerate=frecuencia,
                blocksize=4000,
                device=dispositivo,
                dtype="int16",
                channels=1,
                # Latencia holgada: con la carga de YOLO y la camara,
                # buffers cortos provocaban "MIC: input overflow".
                latency="high",
                callback=self._capturar_audio,
            ):
                print("MICROFONO OK")
                self._reconocer(modelo, frecuencia)

        except Exception as error:
            print("ERROR MICROFONO:", error)

    def _capturar_audio(self, indata, frames, tiempo, status) -> None:
        if status:
            print("MIC:", status)
        self._cola_audio.put(bytes(indata))

    def _reconocer(self, modelo: Model, frecuencia: int) -> None:
        reconocedor: KaldiRecognizer | None = None
        modo_anterior: ModoMicrofono | None = None

        while not self._detener.is_set():
            modo = self.modo

            # Al cambiar de modo se cambia la gramatica y se limpia el
            # audio acumulado para no mezclar frases.
            if modo != modo_anterior:
                modo_anterior = modo
                gramatica = GRAMATICAS.get(modo)
                reconocedor = (
                    KaldiRecognizer(modelo, frecuencia, gramatica)
                    if gramatica
                    else None
                )
                _vacer(self._cola_audio)

            if reconocedor is None:
                self._detener.wait(0.05)
                continue

            try:
                datos = self._cola_audio.get(timeout=0.2)
            except queue.Empty:
                continue

            if not reconocedor.AcceptWaveform(datos):
                continue

            texto = json.loads(reconocedor.Result()).get("text", "").strip()
            if not texto:
                continue

            print("VOZ RECONOCIDA:", texto)
            self._publicar(modo, texto)

    def _publicar(self, modo: ModoMicrofono, texto: str) -> None:
        if modo is ModoMicrofono.DESPERTADOR:
            # Solo interesa si trae la palabra clave "Raspi".
            if contiene_despertador(texto):
                self.eventos.put(EventoVoz(ModoMicrofono.DESPERTADOR, texto))
        elif modo in (ModoMicrofono.DESTINO, ModoMicrofono.COMANDO):
            self.eventos.put(EventoVoz(modo, texto))


def _vacer(cola: queue.Queue) -> None:
    """Vacía una cola sin bloquearse."""
    while True:
        try:
            cola.get_nowait()
        except queue.Empty:
            return
