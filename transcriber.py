"""Segmenta el audio de cada fuente por silencios y lo transcribe con faster-whisper.

Cada fuente (llamada = "Ellos", micrófono = "Yo") tiene su propio Segmenter; un único
Transcriber comparte el modelo Whisper entre ambas y etiqueta cada frase con su fuente.
"""
import queue
import threading
import time
from collections import deque
from difflib import SequenceMatcher
from typing import Callable

import numpy as np
from faster_whisper import WhisperModel

from config import Config

THEM = "Ellos"
ME = "Yo"


class TranscriptBuffer:
    """Búfer circular thread-safe con las últimas N frases y quién las dijo."""

    def __init__(self, max_sentences: int):
        self._lines: deque[tuple[str, str]] = deque(maxlen=max_sentences)
        self._lock = threading.Lock()

    def add(self, speaker: str, text: str) -> bool:
        """Añade la frase. Devuelve False si era eco: con bocinas, el mic también capta la llamada."""
        with self._lock:
            for i in range(len(self._lines) - 1, max(-1, len(self._lines) - 5), -1):
                other_speaker, other_text = self._lines[i]
                if other_speaker != speaker and _similar(text, other_text):
                    if speaker == ME:
                        return False  # el mic repitió lo que dijo la llamada
                    del self._lines[i]  # llegó primero el eco del mic; gana la versión de la llamada
                    break
            self._lines.append((speaker, text))
            return True

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def snapshot(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._lines)


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.6


class Segmenter(threading.Thread):
    """Junta bloques de audio de una fuente hasta detectar el fin de una frase (VAD por energía)."""

    def __init__(self, source: "queue.Queue[np.ndarray]", speaker: str, cfg: Config,
                 segments: "queue.Queue[tuple[str, np.ndarray]]"):
        super().__init__(daemon=True, name=f"segmenter-{speaker}")
        self.source, self.speaker, self.cfg, self.segments = source, speaker, cfg, segments
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        rate = self.cfg.target_sample_rate
        silence_limit = int(self.cfg.silence_duration * rate)
        max_samples = int(self.cfg.max_segment_seconds * rate)
        min_samples = int(self.cfg.min_segment_seconds * rate)
        speech: list[np.ndarray] = []
        speech_samples = silence_samples = 0

        def flush() -> None:
            nonlocal speech, speech_samples, silence_samples
            if speech_samples >= min_samples:
                self.segments.put((self.speaker, np.concatenate(speech)))
            speech, speech_samples, silence_samples = [], 0, 0

        while not self._stop.is_set():
            try:
                chunk = self.source.get(timeout=1.0)
            except queue.Empty:
                if speech:  # la fuente se apagó a media frase
                    flush()
                continue

            rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
            if rms > self.cfg.silence_threshold:
                speech.append(chunk)
                speech_samples += chunk.size
                silence_samples = 0
            elif speech:
                speech.append(chunk)  # conserva un poco de cola para no cortar palabras
                silence_samples += chunk.size

            if speech and (silence_samples >= silence_limit or speech_samples >= max_samples):
                flush()


class Transcriber(threading.Thread):
    def __init__(self, segments: "queue.Queue[tuple[str, np.ndarray]]", buffer: TranscriptBuffer,
                 cfg: Config, on_sentence: Callable[[str, str], None]):
        super().__init__(daemon=True, name="transcriber")
        self.segments, self.buffer, self.cfg, self.on_sentence = segments, buffer, cfg, on_sentence
        self._stop = threading.Event()

        print(f"[stt] Cargando modelo Whisper '{cfg.whisper_model}' ({cfg.whisper_compute_type})…")
        self.model = WhisperModel(cfg.whisper_model, device="cpu", compute_type=cfg.whisper_compute_type)
        print("[stt] Modelo listo.")

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                speaker, audio = self.segments.get(timeout=0.2)
            except queue.Empty:
                continue
            self._transcribe(speaker, audio)

    def _transcribe(self, speaker: str, audio: np.ndarray) -> None:
        t0 = time.time()
        segments, _ = self.model.transcribe(
            audio,
            language=self.cfg.whisper_language or None,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            return
        if not self.buffer.add(speaker, text):
            print(f"[stt] (eco descartado) {speaker}: {text}")
            return
        print(f"[stt] ({time.time() - t0:.1f}s) {speaker}: {text}")
        self.on_sentence(speaker, text)
