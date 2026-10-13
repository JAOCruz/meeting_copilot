"""Meeting Copilot: llamada (BlackHole) + tu mic → Whisper → LM Studio → HUD."""
import argparse
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from audio_capturer import AudioCapturer, list_input_devices
from audio_route import set_route
from config import Config
import settings_store
from hud_gui import Controls, HudSignals, HudWindow, bring_to_front, create_tray, make_accessory_app
from settings_dialog import SettingsDialog
from llm_client import LLMClient
from screen_context import active_odoo_screen, section_key
from transcriber import ME, THEM, Segmenter, TranscriptBuffer, Transcriber


class LLMWorker(threading.Thread):
    """Despierta con cada frase nueva; agrupa ráfagas y respeta un cooldown.

    Dos tipos de trabajo: "answer" (respuesta a lo que dicen los otros) y "note" (nota sobre lo
    que dice el usuario, solo con conocimiento cargado)."""

    def __init__(self, llm: LLMClient, buffer: TranscriptBuffer, signals: HudSignals, cfg: Config):
        super().__init__(daemon=True, name="llm")
        self.llm, self.buffer, self.signals, self.cfg = llm, buffer, signals, cfg
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_suggestion = ""
        self._last_call = 0.0
        self._questions: list[str] = []  # preguntas escritas: se responden siempre, sin contexto
        self._screen: tuple[str, str] | None = None  # pantalla de Odoo pendiente de comentar
        self._last_note = ""
        self._last_note_at = 0.0
        self.paused = False
        self.idle_status = lambda: "Escuchando"

    def notify(self, kind: str = "answer") -> None:
        if not self.paused:
            with self._lock:
                self._pending.add(kind)
            self._wake.set()

    def screen_changed(self, title: str, path: str) -> None:
        if not self.paused:
            with self._lock:
                self._screen = (title, path)
            self._wake.set()

    def ask(self, question: str) -> None:
        with self._lock:
            self._questions.append(question)
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            if self._stop.is_set():
                return
            wait = self.cfg.llm_cooldown_seconds - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            with self._lock:  # lo que llegue durante la llamada dispara otra vuelta
                kinds, self._pending = self._pending, set()
                questions, self._questions = self._questions, []
                screen, self._screen = self._screen, None
                self._wake.clear()
            self._last_call = time.time()

            self.signals.status.emit("Analizando…")
            try:
                for question in questions:
                    result = self.llm.suggest([(THEM, question)], "",
                                              on_start=self.signals.suggestion_start.emit,
                                              on_token=self.signals.suggestion_token.emit)
                    print(f"[llm] → {result}" if result else "[llm] NO_ACTION")
                answered = False
                if "answer" in kinds and self.cfg.answers_enabled:
                    result = self.llm.suggest(self.buffer.snapshot(), self._last_suggestion,
                                              on_start=self.signals.suggestion_start.emit,
                                              on_token=self.signals.suggestion_token.emit)
                    print(f"[llm] → {result}" if result else "[llm] NO_ACTION")
                    self._last_suggestion = result or self._last_suggestion
                    answered = bool(result)
                # Sin pregunta que responder, se comenta lo que se está hablando (con el intervalo configurado).
                if not answered and kinds and time.time() - self._last_note_at > self.cfg.note_interval_seconds:
                    kinds.add("note")
                if screen and self.cfg.screen_notes_enabled:
                    title, path = screen
                    note = self.llm.section(title, path,
                                            on_start=lambda: self.signals.note_start.emit(f"📍 {title}: "),
                                            on_token=self.signals.note_token.emit)
                    if note:
                        print(f"[llm] 📍 {title}: {note}")
                if ("note" in kinds and self.cfg.notes_enabled
                        and time.time() - self._last_note_at > self.cfg.note_interval_seconds):
                    note = self.llm.note(self.buffer.snapshot(), self._last_note,
                                         on_start=lambda: self.signals.note_start.emit("📝 "),
                                         on_token=self.signals.note_token.emit)
                    if note:
                        print(f"[llm] 📝 {note}")
                        self._last_note, self._last_note_at = note, time.time()
            except Exception as exc:  # LM Studio caído, modelo no cargado, etc.
                print(f"[llm] Error: {exc}")
                self.signals.status.emit("Error con LM Studio (ver terminal)")
                continue
            self.signals.status.emit(self.idle_status())


class ScreenWatcher(threading.Thread):
    """Revisa cada 2 s la pestaña activa de Chrome y avisa cuando el usuario cambia de sección de Odoo
    (y se queda en ella al menos un ciclo, para no comentar pantallas por las que solo pasó)."""

    def __init__(self, on_change):
        super().__init__(daemon=True, name="screen")
        self.on_change = on_change
        self.enabled = True
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        last_seen = announced = None
        while not self._stop.wait(2.0):
            if not self.enabled:
                continue
            screen = active_odoo_screen()
            if screen is None:
                continue
            key = section_key(*screen)
            if key == last_seen and key != announced:
                announced = key
                print(f"[screen] {screen[0]} ({screen[1]})")
                self.on_change(*screen)
            last_seen = key


def main() -> int:
    parser = argparse.ArgumentParser(description="Copiloto de reuniones en tiempo real")
    parser.add_argument("--list-devices", action="store_true", help="lista dispositivos de entrada y sale")
    parser.add_argument("--device", help="dispositivo de la llamada (nombre parcial o índice)")
    parser.add_argument("--mic", help="tu micrófono (nombre parcial o índice); por defecto, la entrada del sistema")
    parser.add_argument("--no-mic", action="store_true", help="arranca con el micrófono apagado")
    parser.add_argument("--no-route", action="store_true",
                        help="no cambiar la salida de audio del sistema (ya tienes tu propio dispositivo múltiple)")
    parser.add_argument("--model", help="modelo Whisper: tiny, base, small, medium, large-v3…")
    parser.add_argument("--language", help="código de idioma (es, en…) o '' para auto")
    args = parser.parse_args()

    if args.list_devices:
        list_input_devices()
        return 0

    cfg = Config()
    settings_store.apply(cfg)  # lo elegido en la ventana de Configuración (settings.json)
    if args.no_route:
        cfg.auto_route = False
    if args.device:
        cfg.audio_device = args.device
    if args.mic is not None:
        cfg.mic_device = args.mic
    if args.no_mic:
        cfg.mic_enabled_at_start = False
    if args.model:
        cfg.whisper_model = args.model
    if args.language is not None:
        cfg.whisper_language = args.language

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # ocultar el HUD no debe cerrar la app
    make_accessory_app()

    llm = LLMClient(cfg)
    try:
        llm.check_connection()
        llm.warm_up()
    except Exception as exc:
        print(f"[llm] No se pudo conectar a {cfg.llm_base_url}: {exc}\n"
              "      Abre LM Studio → Developer → Start Server, con el modelo cargado.")
        return 1

    call = AudioCapturer(cfg.audio_device, cfg.target_sample_rate)
    try:
        mic = AudioCapturer(cfg.mic_device, cfg.target_sample_rate)
        if mic.device_index == call.device_index:
            mic = AudioCapturer("MacBook", cfg.target_sample_rate)
    except RuntimeError as exc:
        print(f"[audio] Micrófono no disponible: {exc}")
        mic = None

    buffer = TranscriptBuffer(cfg.buffer_max_sentences)
    segments: "queue.Queue" = queue.Queue()
    signals = HudSignals()
    llm_worker = LLMWorker(llm, buffer, signals, cfg)

    def on_sentence(speaker: str, text: str) -> None:
        signals.transcript.emit(speaker, text)
        llm_worker.notify("answer" if speaker == THEM else "note")

    def ask(text: str) -> None:
        """Pregunta escrita (HUD o inbox.txt): se responde siempre, sin el contexto de la llamada."""
        print(f"[ask] {text}")
        signals.transcript.emit("Pregunta", text)
        llm_worker.ask(text)

    screen_watcher = ScreenWatcher(llm_worker.screen_changed)
    workers = [Segmenter(call.queue, THEM, cfg, segments), Transcriber(segments, buffer, cfg, on_sentence),
               llm_worker, screen_watcher]
    if mic:
        workers.append(Segmenter(mic.queue, ME, cfg, segments))
    for worker in workers:
        worker.start()

    controls = Controls(app)
    hud = HudWindow(cfg, signals, controls)
    for speaker, text in hud.transcript_lines()[-cfg.buffer_max_sentences:]:
        if speaker in (THEM, ME):
            buffer.add(speaker, text)  # el LLM recupera el contexto de la sesión guardada
    tray = create_tray(controls, app)

    def status_text() -> str:
        sources = [name for name, cap in (("llamada", call), ("tu mic", mic)) if cap and cap.running]
        if not sources:
            return "En pausa · no escucha nada"
        suffix = "" if controls.copilot.isChecked() else " · sugerencias en pausa"
        return f"Escuchando {' + '.join(sources)}{suffix}"

    def toggle_call(on: bool) -> None:
        if cfg.auto_route:
            print(f"[audio] {set_route(on)}")
        call.start() if on else call.stop()
        signals.status.emit(status_text())

    def toggle_mic(on: bool) -> None:
        if mic is None:
            controls.mic.setChecked(False)
            signals.status.emit("No hay micrófono disponible")
            return
        mic.start() if on else mic.stop()
        signals.status.emit(status_text())

    def toggle_copilot(on: bool) -> None:
        llm_worker.paused = not on
        signals.status.emit(status_text())

    llm_worker.idle_status = status_text
    controls.call.toggled.connect(toggle_call)
    controls.mic.toggled.connect(toggle_mic)
    controls.copilot.toggled.connect(toggle_copilot)
    controls.screen.toggled.connect(lambda on: setattr(screen_watcher, "enabled", on))
    controls.clear.triggered.connect(buffer.clear)

    restart_requested = False
    settings_window: SettingsDialog | None = None

    def apply_settings(changed: set, restart: bool) -> None:
        nonlocal restart_requested
        print(f"[config] Guardado: {', '.join(sorted(changed)) or 'sin cambios'}")
        if restart:
            restart_requested = True
            app.quit()
            return
        hud.apply_appearance(cfg)
        controls.screen.setChecked(cfg.screen_notes_enabled)
        if changed & {"llm_base_url", "context_path", "context_max_chars", "llm_model"}:
            def reload_llm() -> None:
                try:
                    llm.reload()
                    llm.warm_up()
                    signals.status.emit(f"Configuración aplicada · {status_text()}")
                except Exception as exc:
                    print(f"[llm] Error al aplicar la configuración: {exc}")
                    signals.status.emit("Error con LM Studio tras el cambio (ver terminal)")
            signals.status.emit("Aplicando configuración…")
            threading.Thread(target=reload_llm, daemon=True).start()

    def open_settings() -> None:
        nonlocal settings_window
        if settings_window is None or not settings_window.isVisible():
            settings_window = SettingsDialog(cfg, llm, on_new_session=controls.clear.trigger)
            settings_window.saved.connect(apply_settings)
        bring_to_front(settings_window)

    controls.settings.triggered.connect(open_settings)
    controls.quit.triggered.connect(app.quit)
    signals.ask.connect(ask)

    # inbox.txt: cada línea nueva es una pregunta (para mandarlas desde otra herramienta o un script);
    # "/config" abre la ventana de Configuración.
    inbox = Path(__file__).parent / "inbox.txt"
    inbox_seen = len(inbox.read_text(encoding="utf-8").splitlines()) if inbox.exists() else 0

    def poll_inbox() -> None:
        nonlocal inbox_seen
        if not inbox.exists():
            return
        lines = inbox.read_text(encoding="utf-8").splitlines()
        for line in lines[inbox_seen:]:
            if line.strip() == "/config":
                controls.settings.trigger()
            elif line.strip():
                ask(line.strip())
        inbox_seen = len(lines)

    inbox_timer = QTimer()
    inbox_timer.timeout.connect(poll_inbox)
    inbox_timer.start(1000)

    toggle_call(True)
    controls.mic.setChecked(cfg.mic_enabled_at_start and mic is not None)
    if controls.mic.isChecked():
        mic.start()
    hud.show()
    controls.screen.setChecked(cfg.screen_notes_enabled)
    signals.status.emit(status_text())

    silence_warned = False

    def check_audio() -> None:
        nonlocal silence_warned
        silent = call.seconds_of_digital_silence() > 30
        if silent and not silence_warned:
            print("[audio] 30 s sin nada en BlackHole. Si la llamada suena, revisa que la salida del sistema sea "
                  "'Copilot (salida + BlackHole)' y que tu terminal tenga permiso de micrófono.")
            signals.status.emit("La llamada no llega · ¿suena algo? (ver terminal)")
        elif not silent and silence_warned:
            signals.status.emit(status_text())
        silence_warned = silent

    audio_timer = QTimer()
    audio_timer.timeout.connect(check_audio)
    audio_timer.start(2000)

    # Ctrl+C en la terminal: Qt necesita un timer para que Python procese la señal
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())  # `kill` también restaura la salida de audio
    timer = QTimer()
    timer.start(250)
    timer.timeout.connect(lambda: None)

    code = app.exec()
    tray.hide()
    call.stop()
    if mic:
        mic.stop()
    if cfg.auto_route:
        print(f"[audio] {set_route(False)}")  # devuelve la salida normal (y las teclas de volumen)
    for worker in workers:
        worker.stop()
    if restart_requested:
        print("[config] Reiniciando para aplicar los cambios…")
        os.execv(sys.executable, [sys.executable, *sys.argv])
    return code


if __name__ == "__main__":
    sys.exit(main())
