"""Segmenta el audio por silencios y lo transcribe con faster-whisper en un hilo propio."""
import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np
from faster_whisper import WhisperModel

from config import Config


class TranscriptBuffer:
    """Búfer circular thread-safe con las últimas N oraciones."""

    def __init__(self, max_sentences: int):
        self._lines: deque[tuple[float, str]] = deque(maxlen=max_sentences)
        self._lock = threading.Lock()

    def add(self, text: str) -> None:
        with self._lock:
            self._lines.append((time.time(), text))

    def as_text(self) -> str:
        with self._lock:
            return "\n".join(f"- {t}" for _, t in self._lines)


class Transcriber(threading.Thread):
    def __init__(
        self,
        audio_queue: "queue.Queue[np.ndarray]",
        buffer: TranscriptBuffer,
        cfg: Config,
        on_sentence: Callable[[str], None],
    ):
        super().__init__(daemon=True, name="transcriber")
        self.audio_queue = audio_queue
        self.buffer = buffer
        self.cfg = cfg
        self.on_sentence = on_sentence
        self._stop = threading.Event()

        print(f"[stt] Cargando modelo Whisper '{cfg.whisper_model}' ({cfg.whisper_compute_type})…")
        self.model = WhisperModel(cfg.whisper_model, device="cpu", compute_type=cfg.whisper_compute_type)
        print("[stt] Modelo listo.")

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        rate = self.cfg.target_sample_rate
        speech: list[np.ndarray] = []
        speech_samples = 0
        silence_samples = 0
        silence_limit = int(self.cfg.silence_duration * rate)
        max_samples = int(self.cfg.max_segment_seconds * rate)
        min_samples = int(self.cfg.min_segment_seconds * rate)

        while not self._stop.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
            is_voice = rms > self.cfg.silence_threshold

            if is_voice:
                speech.append(chunk)
                speech_samples += chunk.size
                silence_samples = 0
            elif speech:
                speech.append(chunk)  # conserva un poco de cola para no cortar palabras
                silence_samples += chunk.size

            sentence_ended = speech and silence_samples >= silence_limit
            too_long = speech_samples >= max_samples
            if sentence_ended or too_long:
                if speech_samples >= min_samples:
                    self._transcribe(np.concatenate(speech))
                speech, speech_samples, silence_samples = [], 0, 0

    def _transcribe(self, audio: np.ndarray) -> None:
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
        print(f"[stt] ({time.time() - t0:.1f}s) {text}")
        self.buffer.add(text)
        self.on_sentence(text)
