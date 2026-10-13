"""Ventana de Configuración: edita los ajustes (settings.json), actualiza el conocimiento y gestiona sesiones."""
import json
import subprocess
import sys
from pathlib import Path

import sounddevice as sd
from PyQt6.QtCore import QProcess, Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

import session_store
from audio_capturer import find_device
import settings_store
from config import Config
from llm_client import LLMClient, load_knowledge
from openai import OpenAI

ROOT = Path(__file__).parent
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
LANGUAGES = [("Español", "es"), ("Inglés", "en"), ("Detectar automáticamente", "")]

STYLE = """
QDialog, QWidget { background: #17181c; color: #e0e3e7; font-size: 13px; }
QTabWidget::pane { border: 1px solid #2c2e34; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; color: #9aa0a6; padding: 7px 14px; border: none; }
QTabBar::tab:selected { color: #7CFFB2; border-bottom: 2px solid #7CFFB2; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget {
    background: #22242a; border: 1px solid #33363d; border-radius: 6px; padding: 4px 6px; }
QPushButton { background: #2a2d34; border: 1px solid #3a3d45; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { border-color: #7CFFB2; }
QPushButton#primary { background: #7CFFB2; color: #0d1f16; border: none; font-weight: 600; }
QLabel#hint { color: #8a8f98; font-size: 11px; }
"""


def input_devices() -> list[str]:
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


def _same_device(name: str, old: str, new: str) -> bool:
    """«BlackHole» y «BlackHole 2ch» son el mismo dispositivo: no cuenta como cambio."""
    if name not in {"audio_device", "mic_device"}:
        return False
    try:
        return find_device(old) == find_device(new)
    except RuntimeError:
        return False


def export_markdown(session: Path) -> Path:
    """Convierte una sesión guardada en un .md legible junto al .json."""
    entries = json.loads(session.read_text(encoding="utf-8")).get("entries", [])
    lines = [f"# Reunión {session.stem}\n"]
    for kind, prefix, text in entries:
        if kind == "line":
            lines.append(f"**{prefix}:** {text}  ")
        else:
            lines.append(f"\n> {prefix}{text}\n")
    out = session.with_suffix(".md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


class SettingsDialog(QDialog):
    saved = pyqtSignal(set, bool)  # (nombres de ajustes cambiados, reiniciar ahora)

    def __init__(self, cfg: Config, llm: LLMClient, on_new_session, parent=None):
        super().__init__(parent)
        self.cfg, self.llm, self.on_new_session = cfg, llm, on_new_session
        self.setWindowTitle("Meeting Copilot · Configuración")
        self.setStyleSheet(STYLE)
        self.resize(620, 560)
        self._process: QProcess | None = None

        tabs = QTabWidget()
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._transcription_tab(), "Transcripción")
        tabs.addTab(self._ai_tab(), "IA")
        tabs.addTab(self._knowledge_tab(), "Conocimiento")
        tabs.addTab(self._hud_tab(), "HUD")
        tabs.addTab(self._sessions_tab(), "Sesiones")

        save = QPushButton("Guardar")
        save.setObjectName("primary")
        save.clicked.connect(lambda: self._save(restart=False))
        save_restart = QPushButton("Guardar y reiniciar")
        save_restart.clicked.connect(lambda: self._save(restart=True))
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self._hint("Los ajustes marcados con ↻ se aplican al reiniciar."))
        buttons.addStretch()
        for b in (cancel, save_restart, save):
            buttons.addWidget(b)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _form() -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)
        return page, form

    @staticmethod
    def _combo(options: list[tuple[str, str]], current: str, editable: bool = False) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(editable)
        for label, value in options:
            combo.addItem(label, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif editable:
            combo.setEditText(current)
        return combo

    @staticmethod
    def _spin(value: float, low: float, high: float, step: float, decimals: int = 0):
        spin = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            spin.setDecimals(decimals)
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setValue(value)
        return spin

    @staticmethod
    def _value(combo: QComboBox) -> str:
        """Valor de una lista: el dato de la opción elegida, o el texto escrito a mano si no es una opción."""
        if combo.isEditable() and combo.currentText() != combo.itemText(combo.currentIndex()):
            return combo.currentText().strip()
        data = combo.currentData()
        return combo.currentText().strip() if data is None else data

    # --------------------------------------------------------------------- tabs
    def _audio_tab(self) -> QWidget:
        page, form = self._form()
        devices = input_devices()
        call_current = next((d for d in devices if self.cfg.audio_device.lower() in d.lower()), self.cfg.audio_device)
        self.w_audio_device = self._combo([(d, d) for d in devices], call_current, editable=True)
        self.w_mic_device = self._combo([("Entrada del sistema (por defecto)", "")] + [(d, d) for d in devices],
                                        self.cfg.mic_device, editable=True)
        self.w_mic_enabled = QCheckBox("Escuchar mi micrófono al abrir")
        self.w_mic_enabled.setChecked(self.cfg.mic_enabled_at_start)
        self.w_auto_route = QCheckBox("Enviar el audio del sistema a BlackHole automáticamente")
        self.w_auto_route.setChecked(self.cfg.auto_route)
        self.w_silence = self._spin(self.cfg.silence_threshold, 0.001, 0.05, 0.001, decimals=3)
        form.addRow("Llamada ↻", self.w_audio_device)
        form.addRow("", self._hint("Normalmente «BlackHole 2ch»: por ahí entra lo que dicen los demás."))
        form.addRow("Mi micrófono ↻", self.w_mic_device)
        form.addRow("", self.w_mic_enabled)
        form.addRow("Ruta de audio ↻", self.w_auto_route)
        form.addRow("", self._hint("Crea «Copilot (salida + BlackHole)» al escuchar la llamada y vuelve a tu "
                                   "salida normal al salir. Desactívalo si ya tienes tu propio dispositivo múltiple."))
        form.addRow("Sensibilidad", self.w_silence)
        form.addRow("", self._hint("Volumen mínimo que cuenta como voz. Bájalo (p. ej. 0.004) si no detecta "
                                   "frases; súbelo si transcribe ruido."))
        return page

    def _transcription_tab(self) -> QWidget:
        page, form = self._form()
        self.w_whisper = self._combo([(m, m) for m in WHISPER_MODELS], self.cfg.whisper_model)
        self.w_language = self._combo(LANGUAGES, self.cfg.whisper_language)
        self.w_silence_duration = self._spin(self.cfg.silence_duration, 0.3, 3.0, 0.1, decimals=1)
        form.addRow("Modelo Whisper ↻", self.w_whisper)
        form.addRow("", self._hint("small: buen equilibrio en un M3. base: más rápido, menos preciso. "
                                   "medium / large: más precisos pero más lentos."))
        form.addRow("Idioma ↻", self.w_language)
        form.addRow("Pausa que cierra una frase (s) ↻", self.w_silence_duration)
        return page

    def _ai_tab(self) -> QWidget:
        page, form = self._form()
        self.w_url = QLineEdit(self.cfg.llm_base_url)
        self.w_model = self._combo([(self.cfg.llm_model, self.cfg.llm_model)], self.cfg.llm_model, editable=True)
        refresh = QPushButton("Buscar modelos")
        refresh.clicked.connect(self._refresh_models)
        self.w_model_status = self._hint("")
        model_row = QHBoxLayout()
        model_row.addWidget(self.w_model, 1)
        model_row.addWidget(refresh)
        self.w_temperature = self._spin(self.cfg.llm_temperature, 0.0, 1.0, 0.05, decimals=2)
        self.w_max_tokens = self._spin(self.cfg.llm_max_tokens, 40, 600, 20)
        self.w_cooldown = self._spin(self.cfg.llm_cooldown_seconds, 0.5, 30.0, 0.5, decimals=1)
        self.w_note_interval = self._spin(self.cfg.note_interval_seconds, 2.0, 120.0, 1.0, decimals=1)
        self.w_answers = QCheckBox("💬 Proponer respuestas a las preguntas de los demás")
        self.w_answers.setChecked(self.cfg.answers_enabled)
        self.w_notes = QCheckBox("📝 Comentar lo que se está hablando")
        self.w_notes.setChecked(self.cfg.notes_enabled)
        self.w_screen = QCheckBox("📍 Notas sobre la pantalla de Odoo abierta en Chrome")
        self.w_screen.setChecked(self.cfg.screen_notes_enabled)
        form.addRow("Servidor (LM Studio)", self.w_url)
        form.addRow("Modelo", model_row)
        form.addRow("", self.w_model_status)
        form.addRow("Creatividad", self.w_temperature)
        form.addRow("", self._hint("Baja = más fiel al conocimiento (recomendado 0.1–0.3)."))
        form.addRow("Largo máximo (tokens)", self.w_max_tokens)
        form.addRow("Espera entre análisis (s)", self.w_cooldown)
        form.addRow("Un comentario 📝 cada (s)", self.w_note_interval)
        form.addRow("Qué genera", self.w_answers)
        form.addRow("", self.w_notes)
        form.addRow("", self.w_screen)
        self._refresh_models()
        return page

    def _knowledge_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.w_context = QLineEdit(self.cfg.context_path)
        choose = QPushButton("Elegir…")
        choose.clicked.connect(self._choose_knowledge)
        path_row = QHBoxLayout()
        path_row.addWidget(self.w_context, 1)
        path_row.addWidget(choose)
        self.w_knowledge_info = self._hint("")
        self.w_project = QLineEdit(self.cfg.knowledge_project)
        update = QPushButton("Actualizar desde aiviber")
        update.clicked.connect(self._update_knowledge)
        project_row = QHBoxLayout()
        project_row.addWidget(self.w_project, 1)
        project_row.addWidget(update)
        form.addRow("Carpeta o archivo", path_row)
        form.addRow("", self.w_knowledge_info)
        form.addRow("Proyecto aiviber", project_row)
        form.addRow("", self._hint("Exporta las notas del proyecto (vía epona) a knowledge/<proyecto>.md. "
                                   "Tarda ~2 min por el límite de aiviber. Las notas técnicas se omiten."))
        self.w_export_log = QPlainTextEdit()
        self.w_export_log.setReadOnly(True)
        self.w_export_log.setPlaceholderText("Aquí aparece el progreso de la actualización.")
        layout.addLayout(form)
        layout.addWidget(self.w_export_log, 1)
        self.w_context.textChanged.connect(self._show_knowledge_info)
        self._show_knowledge_info()
        return page

    def _hud_tab(self) -> QWidget:
        page, form = self._form()
        self.w_opacity = self._spin(self.cfg.hud_opacity, 0.5, 1.0, 0.05, decimals=2)
        self.w_font = self._spin(self.cfg.hud_font_size, 10, 28, 1)
        self.w_width = self._spin(self.cfg.hud_width, 320, 1200, 20)
        form.addRow("Opacidad", self.w_opacity)
        form.addRow("Tamaño de las respuestas", self.w_font)
        form.addRow("Ancho (px)", self.w_width)
        form.addRow("", self._hint("El alto se ajusta arrastrando la esquina inferior derecha del HUD."))
        return page

    def _sessions_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.w_sessions = QListWidget()
        layout.addWidget(self._hint("Sesión en curso y reuniones archivadas (🧹 archiva la actual)."))
        layout.addWidget(self.w_sessions, 1)
        row = QHBoxLayout()
        for text, slot in (("Exportar a Markdown", self._export_session), ("Abrir carpeta", self._open_sessions),
                           ("Nueva sesión", self._new_session)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self._refresh_sessions()
        return page

    # ------------------------------------------------------------------ actions
    def _refresh_models(self) -> None:
        current = self.w_model.currentText()
        try:
            client = OpenAI(base_url=self.w_url.text().strip(), api_key="lm-studio", timeout=3)
            models = [m.id for m in client.models.list().data if "embed" not in m.id.lower()]
        except Exception as exc:
            self.w_model_status.setText(f"No se pudo conectar con LM Studio: {str(exc)[:80]}")
            return
        self.w_model.clear()
        for model in models:
            self.w_model.addItem(model, model)
        index = self.w_model.findData(current)
        if index >= 0:
            self.w_model.setCurrentIndex(index)
        else:
            self.w_model.setEditText(current)
        self.w_model_status.setText(f"{len(models)} modelo(s) en LM Studio. "
                                    "Recomendado: Qwen3-30B-A3B-Instruct-2507 (Q4_K_M).")

    def _choose_knowledge(self) -> None:
        start = str(ROOT / self.w_context.text()) if not Path(self.w_context.text()).is_absolute() \
            else self.w_context.text()
        folder = QFileDialog.getExistingDirectory(self, "Carpeta de conocimiento", start)
        if folder:
            try:
                folder = str(Path(folder).relative_to(ROOT))
            except ValueError:
                pass
            self.w_context.setText(folder)

    def _show_knowledge_info(self) -> None:
        text = load_knowledge(self.w_context.text().strip() or "knowledge", 10**9)
        if not text:
            self.w_knowledge_info.setText("Sin conocimiento: el copiloto responderá de forma genérica.")
            return
        notes = text.count("\n[")  # cada nota exportada de aiviber empieza con [proyecto.clave | …]
        limit = self.cfg.context_max_chars
        extra = f" · se recorta a {limit:,}" if len(text) > limit else ""
        self.w_knowledge_info.setText(f"{len(text):,} caracteres (~{len(text) // 4:,} tokens) · "
                                      f"{notes} notas{extra}")

    def _update_knowledge(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            return
        project = self.w_project.text().strip() or "transport"
        self.w_export_log.setPlainText(f"Exportando proyecto «{project}» desde aiviber…\n")
        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(ROOT))
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_export_output)
        self._process.finished.connect(self._on_export_finished)
        self._process.start(sys.executable, ["-u", str(ROOT / "tools/export_epona_context.py"), "--project", project])

    def _on_export_output(self) -> None:
        chunk = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        lines = self.w_export_log.toPlainText().split("\n")
        for part in chunk.replace("\r", "\n\r").split("\n"):
            if part.startswith("\r"):  # barra de progreso: reemplaza la última línea
                lines[-1] = part[1:]
            elif part:
                lines.append(part)
        self.w_export_log.setPlainText("\n".join(lines))
        self.w_export_log.verticalScrollBar().setValue(self.w_export_log.verticalScrollBar().maximum())

    def _on_export_finished(self, code: int) -> None:
        self.w_export_log.appendPlainText("✓ Listo. Pulsa Guardar para que el copiloto use el conocimiento nuevo."
                                          if code == 0 else f"✗ Falló (código {code}).")
        self.w_context.setText("knowledge")
        self._show_knowledge_info()
        self._knowledge_refreshed = code == 0

    def _refresh_sessions(self) -> None:
        self.w_sessions.clear()
        files = sorted(session_store.DIR.glob("*.json"), reverse=True) if session_store.DIR.exists() else []
        files.sort(key=lambda p: p.name != "current.json")  # la sesión en curso primero
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            label = "En curso" if path.name == "current.json" else path.stem.replace("_", " ")
            item = QListWidgetItem(f"{label} · {len(data.get('entries', []))} entradas · "
                                   f"guardada {data.get('saved_at', '?')}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.w_sessions.addItem(item)
        if self.w_sessions.count():
            self.w_sessions.setCurrentRow(0)

    def _export_session(self) -> None:
        item = self.w_sessions.currentItem()
        if item:
            out = export_markdown(Path(item.data(Qt.ItemDataRole.UserRole)))
            subprocess.run(["open", "-R", str(out)])  # lo muestra en Finder

    def _open_sessions(self) -> None:
        session_store.DIR.mkdir(exist_ok=True)
        subprocess.run(["open", str(session_store.DIR)])

    def _new_session(self) -> None:
        if QMessageBox.question(self, "Nueva sesión", "¿Archivar la sesión en curso y empezar una nueva?") \
                == QMessageBox.StandardButton.Yes:
            self.on_new_session()
            self._refresh_sessions()

    # --------------------------------------------------------------------- save
    def _collect(self) -> dict:
        return {
            "audio_device": self._value(self.w_audio_device),
            "mic_device": self._value(self.w_mic_device),
            "mic_enabled_at_start": self.w_mic_enabled.isChecked(),
            "auto_route": self.w_auto_route.isChecked(),
            "silence_threshold": self.w_silence.value(),
            "whisper_model": self._value(self.w_whisper),
            "whisper_language": self._value(self.w_language),
            "silence_duration": self.w_silence_duration.value(),
            "llm_base_url": self.w_url.text().strip(),
            "llm_model": self.w_model.currentText().strip(),
            "llm_temperature": self.w_temperature.value(),
            "llm_max_tokens": self.w_max_tokens.value(),
            "llm_cooldown_seconds": self.w_cooldown.value(),
            "note_interval_seconds": self.w_note_interval.value(),
            "answers_enabled": self.w_answers.isChecked(),
            "notes_enabled": self.w_notes.isChecked(),
            "screen_notes_enabled": self.w_screen.isChecked(),
            "context_path": self.w_context.text().strip() or "knowledge",
            "knowledge_project": self.w_project.text().strip() or "transport",
            "hud_opacity": self.w_opacity.value(),
            "hud_font_size": self.w_font.value(),
            "hud_width": self.w_width.value(),
        }

    def _save(self, restart: bool) -> None:
        values = self._collect()
        changed = {name for name, value in values.items()
                   if getattr(self.cfg, name) != value and not _same_device(name, getattr(self.cfg, name), value)}
        if getattr(self, "_knowledge_refreshed", False):
            changed.add("context_path")  # mismo nombre de carpeta, contenido nuevo
        settings_store.apply(self.cfg, values)
        settings_store.save(self.cfg)
        pending = changed & settings_store.RESTART_REQUIRED
        if pending and not restart:
            QMessageBox.information(self, "Guardado", "Guardado. Algunos cambios (↻) se aplicarán la próxima vez "
                                    "que abras el copiloto, o con «Guardar y reiniciar».")
        self.saved.emit(changed, restart)
        self.accept()
