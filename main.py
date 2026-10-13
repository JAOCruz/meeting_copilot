"""Meeting Copilot: audio (BlackHole) → Whisper → LM Studio → HUD."""
import argparse
import signal
import sys
import threading
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from audio_capturer import AudioCapturer, list_input_devices
from config import Config
from hud_gui import HudSignals, HudWindow
from llm_client import LLMClient
from transcriber import TranscriptBuffer, Transcriber


class LLMWorker(threading.Thread):
    """Despierta con cada frase nueva; agrupa ráfagas y respeta un cooldown."""

    def __init__(self, llm: LLMClient, buffer: TranscriptBuffer, signals: HudSignals, cfg: Config):
        super().__init__(daemon=True, name="llm")
        self.llm, self.buffer, self.signals, self.cfg = llm, buffer, signals, cfg
        self._pending = threading.Event()
        self._stop = threading.Event()
        self._last_suggestion = ""
        self._last_call = 0.0

    def notify(self) -> None:
        self._pending.set()

    def stop(self) -> None:
        self._stop.set()
        self._pending.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self._pending.wait()
            if self._stop.is_set():
                return
            wait = self.cfg.llm_cooldown_seconds - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._pending.clear()  # las frases que lleguen durante la llamada disparan otra
            self._last_call = time.time()

            self.signals.status.emit("Analizando…")
            try:
                result = self.llm.suggest(
                    self.buffer.as_text(),
                    self._last_suggestion,
                    on_start=self.signals.suggestion_start.emit,
                    on_token=self.signals.suggestion_token.emit,
                )
            except Exception as exc:  # LM Studio caído, modelo no cargado, etc.
                print(f"[llm] Error: {exc}")
                self.signals.status.emit("Error con LM Studio (ver terminal)")
                continue
            if result:
                print(f"[llm] → {result}")
                self._last_suggestion = result
            self.signals.status.emit("Escuchando")


def main() -> int:
    parser = argparse.ArgumentParser(description="Copiloto de reuniones en tiempo real")
    parser.add_argument("--list-devices", action="store_true", help="lista dispositivos de entrada y sale")
    parser.add_argument("--device", help="nombre (parcial) o índice del dispositivo de audio")
    parser.add_argument("--model", help="modelo Whisper: tiny, base, small, medium, large-v3…")
    parser.add_argument("--language", help="código de idioma (es, en…) o '' para auto")
    args = parser.parse_args()

    if args.list_devices:
        list_input_devices()
        return 0

    cfg = Config()
    if args.device:
        cfg.audio_device = args.device
    if args.model:
        cfg.whisper_model = args.model
    if args.language is not None:
        cfg.whisper_language = args.language

    app = QApplication(sys.argv)
    signals = HudSignals()
    hud = HudWindow(cfg, signals)
    hud.show()

    llm = LLMClient(cfg)
    try:
        llm.check_connection()
    except Exception as exc:
        print(f"[llm] No se pudo conectar a {cfg.llm_base_url}: {exc}\n"
              "      Abre LM Studio → Developer → Start Server, con el modelo cargado.")
        return 1

    capturer = AudioCapturer(cfg.audio_device, cfg.target_sample_rate)
    buffer = TranscriptBuffer(cfg.buffer_max_sentences)
    llm_worker = LLMWorker(llm, buffer, signals, cfg)

    def on_sentence(text: str) -> None:
        signals.transcript.emit(text)
        llm_worker.notify()

    transcriber = Transcriber(capturer.queue, buffer, cfg, on_sentence)

    transcriber.start()
    llm_worker.start()
    capturer.start()
    signals.status.emit(f"Escuchando · {capturer.device_name}")

    silence_warned = False

    def check_audio() -> None:
        nonlocal silence_warned
        silent = capturer.seconds_of_digital_silence() > 15
        if silent and not silence_warned:
            print("[audio] 15 s de ceros absolutos. Si hay audio sonando, la app de terminal no tiene "
                  "permiso de micrófono (Ajustes → Privacidad y seguridad → Micrófono).")
            signals.status.emit("Sin audio: ¿suena algo? ¿permiso de micrófono?")
        elif not silent and silence_warned:
            signals.status.emit(f"Escuchando · {capturer.device_name}")
        silence_warned = silent

    audio_timer = QTimer()
    audio_timer.timeout.connect(check_audio)
    audio_timer.start(2000)

    # Ctrl+C en la terminal: Qt necesita un timer para que Python procese la señal
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.start(250)
    timer.timeout.connect(lambda: None)

    code = app.exec()
    capturer.stop()
    transcriber.stop()
    llm_worker.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
