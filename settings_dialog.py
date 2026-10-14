"""Ventana de Configuración: edita los ajustes (settings.json), actualiza el conocimiento y gestiona sesiones.

Estilo Ajustes de macOS: secciones en una barra lateral y, en cada una, grupos de filas con título,
explicación breve y el control a la derecha."""
import json
import subprocess
import sys
from pathlib import Path

import sounddevice as sd
from PyQt6.QtCore import QProcess, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

import session_store
import settings_store
import providers
import ui_theme as T
from audio_capturer import find_device
from config import Config
from llm_client import LLMClient, load_knowledge
from openai import OpenAI

ROOT = Path(__file__).parent
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
LANGUAGES = [("Español", "es"), ("Inglés", "en"), ("Detectar automáticamente", "")]
CONTROL_WIDTH = 250


def _style() -> str:
    down, up = T.svg_file("chevron-down", T.MUTED), T.svg_file("chevron-up", T.MUTED)
    return f"""
QDialog {{ background: {T.BG}; }}
QWidget {{ color: {T.TEXT}; font-size: 13px; }}
QScrollArea, #page {{ background: {T.BG}; border: none; }}
#sidebar {{ background: #161A20; border-right: 1px solid {T.LINE}; }}
#appName {{ font-size: 14px; font-weight: 600; }}
#appStatus {{ color: {T.MUTED}; font-size: 12px; }}
#nav {{ background: transparent; border: none; outline: none; }}
#nav::item {{ color: {T.MUTED}; padding: 7px 8px; border-radius: 7px; margin: 1px 0; }}
#nav::item:hover {{ background: {T.SURFACE}; color: {T.TEXT}; }}
#nav::item:selected {{ background: {T.SURFACE_2}; color: {T.TEXT}; }}
#pageTitle {{ font-size: 20px; font-weight: 600; }}
#pageLead {{ color: {T.MUTED}; font-size: 13px; }}
#groupTitle {{ color: {T.MUTED}; font-size: 12px; font-weight: 600; }}
#group {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 10px; }}
#divider {{ background: {T.LINE}; }}
#rowTitle {{ font-size: 13px; }}
#rowHint, #hint {{ color: {T.MUTED}; font-size: 12px; }}
#restartTag {{ color: {T.AMBER}; background: #2A2416; border-radius: 5px; padding: 1px 6px; font-size: 11px; }}
#value {{ color: {T.TEXT}; }}
#valueMuted {{ color: {T.MUTED}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {T.BG}; border: 1px solid {T.LINE}; border-radius: 7px; padding: 5px 8px; min-height: 18px;
    selection-background-color: {T.INK_BG}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {T.INK}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({down}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 7px;
                               selection-background-color: {T.SURFACE_2}; outline: none; padding: 4px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; width: 18px; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
QPlainTextEdit {{ background: {T.BG}; border: 1px solid {T.LINE}; border-radius: 8px; padding: 6px;
                  color: {T.MUTED}; font-family: Menlo; font-size: 11px; }}
#sessions {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 10px; padding: 4px; outline: none; }}
#sessions::item {{ padding: 8px 10px; border-radius: 7px; color: {T.TEXT}; }}
#sessions::item:selected {{ background: {T.SURFACE_2}; }}
QPushButton {{ background: {T.SURFACE_2}; border: 1px solid {T.LINE}; border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {T.MUTED}; }}
QPushButton#primary {{ background: {T.INK}; color: #0F1B2D; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #9AC3FF; }}
QPushButton#ghost {{ background: transparent; border: none; color: {T.MUTED}; }}
QPushButton#ghost:hover {{ color: {T.TEXT}; }}
#footer {{ background: #161A20; border-top: 1px solid {T.LINE}; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {T.SURFACE_2}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QToolTip {{ background: {T.SURFACE}; color: {T.TEXT}; border: 1px solid {T.LINE}; padding: 4px 6px; }}
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
    for kind, prefix, text, *rest in entries:
        at = f"`{rest[0]}` " if rest and rest[0] else ""
        if kind == "line":
            lines.append(f"{at}**{prefix}:** {text}  ")
        else:
            lines.append(f"\n> {prefix}{text}\n")
    out = session.with_suffix(".md")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _session_title(stem: str) -> str:
    """'2026-10-13_181502' → '13 oct 2026, 18:15'."""
    months = "ene feb mar abr may jun jul ago sep oct nov dic".split()
    try:
        day, clock = stem.split("_")
        y, m, d = day.split("-")
        return f"{int(d)} {months[int(m) - 1]} {y}, {clock[:2]}:{clock[2:4]}"
    except ValueError:
        return stem.replace("_", " ")


class Group(QFrame):
    """Bloque redondeado con filas separadas por divisores."""

    def __init__(self):
        super().__init__()
        self.setObjectName("group")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 4, 14, 4)
        self._layout.setSpacing(0)

    def add(self, widget: QWidget) -> None:
        if self._layout.count():
            divider = QFrame()
            divider.setObjectName("divider")
            divider.setFixedHeight(1)
            self._layout.addWidget(divider)
        self._layout.addWidget(widget)


class SettingsDialog(QDialog):
    saved = pyqtSignal(set, bool)  # (nombres de ajustes cambiados, reiniciar ahora)

    def __init__(self, cfg: Config, llm: LLMClient, on_new_session, parent=None):
        super().__init__(parent)
        self.cfg, self.llm, self.on_new_session = cfg, llm, on_new_session
        self.setWindowTitle("Configuración")
        self.setStyleSheet(_style())
        self.resize(820, 620)
        self.setMinimumSize(720, 520)
        self._process: QProcess | None = None
        self._lm_error = ""
        self._lm_models: list[str] = []

        self.stack = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(16, 16))
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        pages = [  # Estado se construye al final porque resume lo que leen las demás
            ("Audio", "wave", self._audio_page),
            ("Transcripción", "text", self._transcription_page),
            ("Copiloto", "spark", self._ai_page),
            ("Conocimiento", "book", self._knowledge_page),
            ("HUD", "layout", self._hud_page),
            ("Sesiones", "history", self._sessions_page),
        ]
        built = [(name, icon_name, build()) for name, icon_name, build in pages]
        built.insert(0, ("Estado", "pulse", self._status_page()))
        for name, icon_name, page in built:
            item = QListWidgetItem(T.icon(icon_name, T.MUTED, 16), name)
            item.setSizeHint(QSize(0, 34))
            self.nav.addItem(item)
            self.stack.addWidget(self._scroll(page))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._sidebar())
        body.addWidget(self.stack, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(body, 1)
        layout.addWidget(self._footer())

    # ------------------------------------------------------------------ estructura
    def _sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(208)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(9)
        logo = QLabel()
        logo.setPixmap(T.pixmap("bubble", T.INK, 22))
        names = QVBoxLayout()
        names.setSpacing(1)
        name = QLabel("Meeting Copilot")
        name.setObjectName("appName")
        self.app_status = QLabel()
        self.app_status.setObjectName("appStatus")
        names.addWidget(name)
        names.addWidget(self.app_status)
        brand.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        brand.addLayout(names, 1)
        layout.addLayout(brand)
        layout.addSpacing(14)
        layout.addWidget(self.nav, 1)
        self._update_app_status()
        return side

    def _footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(8)
        row.addWidget(self._hint("Lo marcado con «Requiere reiniciar» se aplica al reiniciar el copiloto."), 1)
        cancel = QPushButton("Cancelar")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save_restart = QPushButton("Guardar y reiniciar")
        save_restart.clicked.connect(lambda: self._save(restart=True))
        save = QPushButton("Guardar")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(lambda: self._save(restart=False))
        for button in (cancel, save_restart, save):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        return footer

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(page)
        return area

    @staticmethod
    def _page(title: str, lead: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        text = QLabel(lead)
        text.setObjectName("pageLead")
        text.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(text)
        layout.addSpacing(12)
        return page, layout

    @staticmethod
    def _section(layout: QVBoxLayout, title: str | None = None) -> Group:
        if title:
            label = QLabel(title)
            label.setObjectName("groupTitle")
            layout.addSpacing(8)
            layout.addWidget(label)
            layout.addSpacing(2)
        group = Group()
        layout.addWidget(group)
        return group

    @staticmethod
    def _row(title: str, hint: str = "", control: QWidget | QHBoxLayout | None = None,
             restart: bool = False) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("rowTitle")
        head.addWidget(label)
        if restart:
            tag = QLabel("Requiere reiniciar")
            tag.setObjectName("restartTag")
            head.addWidget(tag)
        head.addStretch()
        text.addLayout(head)
        if hint:
            note = QLabel(hint)
            note.setObjectName("rowHint")
            note.setWordWrap(True)
            text.addWidget(note)
        layout.addLayout(text, 1)
        if isinstance(control, QHBoxLayout):
            layout.addLayout(control)
        elif control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _value_label(text: str, muted: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName("valueMuted" if muted else "value")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    @staticmethod
    def _combo(options: list[tuple[str, str]], current: str, editable: bool = False) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(editable)
        combo.setFixedWidth(CONTROL_WIDTH)
        for label, value in options:
            combo.addItem(label, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif editable:
            combo.setEditText(current)
        return combo

    @staticmethod
    def _spin(value: float, low: float, high: float, step: float, decimals: int = 0, suffix: str = ""):
        spin = QDoubleSpinBox() if decimals else QSpinBox()
        if decimals:
            spin.setDecimals(decimals)
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.setFixedWidth(120)
        if suffix:
            spin.setSuffix(f" {suffix}")
        return spin

    @staticmethod
    def _switch(on: bool) -> T.Switch:
        switch = T.Switch()
        switch.setChecked(on)
        return switch

    @staticmethod
    def _button(text: str, icon_name: str | None = None) -> QPushButton:
        button = QPushButton(text)
        if icon_name:
            button.setIcon(T.icon(icon_name, T.TEXT, 15))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _value(combo: QComboBox) -> str:
        """Valor de una lista: el dato de la opción elegida, o el texto escrito a mano si no es una opción."""
        if combo.isEditable() and combo.currentText() != combo.itemText(combo.currentIndex()):
            return combo.currentText().strip()
        data = combo.currentData()
        return combo.currentText().strip() if data is None else data

    # --------------------------------------------------------------------- páginas
    def _status_page(self) -> QWidget:
        page, layout = self._page("Estado", "Cómo está funcionando el copiloto ahora mismo.")
        group = self._section(layout)
        conn = providers.connection(self.cfg)
        if conn.info.local:
            lm = (f"Conectado, {len(self._lm_models)} modelos" if not self._lm_error
                  else f"Sin conexión: {self._lm_error[:70]}")
            group.add(self._row("LM Studio", self.cfg.llm_base_url, self._value_label(lm, muted=bool(self._lm_error))))
        else:
            group.add(self._row("Proveedor", "La transcripción y el conocimiento salen de tu Mac.",
                                self._value_label(conn.info.label + ("" if conn.api_key else ", sin API key"),
                                                  muted=not conn.api_key)))
        group.add(self._row("Modelo", "El que genera respuestas y notas.", self._value_label(conn.model)))
        knowledge = load_knowledge(str(providers.knowledge_source(self.cfg.context_path, self.cfg.knowledge_project)),
                                   10**9)
        group.add(self._row("Conocimiento", f"Proyecto «{self.cfg.knowledge_project}»",
                            self._value_label(f"{knowledge.count(chr(10) + '[')} notas, ~{len(knowledge) // 4:,} tokens"
                                              if knowledge else "Sin conocimiento", muted=not knowledge)))
        language = dict((v, k) for k, v in LANGUAGES).get(self.cfg.whisper_language, self.cfg.whisper_language)
        group.add(self._row("Transcripción", "Whisper, en tu Mac.",
                            self._value_label(f"{self.cfg.whisper_model}, {language.lower()}")))
        audio = self._section(layout, "Audio")
        audio.add(self._row("Llamada", "Lo que dicen los demás.", self._value_label(self.cfg.audio_device)))
        audio.add(self._row("Mi micrófono", "Lo que dices tú.",
                            self._value_label(self.cfg.mic_device or "Entrada del sistema")))
        tips = self._section(layout, "En el HUD")
        tips.add(self._row("Modo compacto", "Deja solo la última respuesta. Útil con la pantalla llena.",
                           self._value_label("Doble clic en la cabecera", muted=True)))
        tips.add(self._row("Historial", "Si subes para leer, el historial deja de moverse hasta que vuelvas abajo.",
                           self._value_label("Botón «nuevos» para volver", muted=True)))
        layout.addStretch()
        return page

    def _audio_page(self) -> QWidget:
        page, layout = self._page("Audio", "De dónde escucha el copiloto la llamada y tu voz.")
        devices = input_devices()
        call_current = next((d for d in devices if self.cfg.audio_device.lower() in d.lower()), self.cfg.audio_device)
        self.w_audio_device = self._combo([(d, d) for d in devices], call_current, editable=True)
        self.w_mic_device = self._combo([("Entrada del sistema (por defecto)", "")] + [(d, d) for d in devices],
                                        self.cfg.mic_device, editable=True)
        self.w_mic_enabled = self._switch(self.cfg.mic_enabled_at_start)
        self.w_auto_route = self._switch(self.cfg.auto_route)
        self.w_silence = self._spin(self.cfg.silence_threshold, 0.001, 0.05, 0.001, decimals=3)

        sources = self._section(layout, "Fuentes")
        sources.add(self._row("Llamada", "Normalmente «BlackHole 2ch»: por ahí entra lo que dicen los demás.",
                              self.w_audio_device, restart=True))
        sources.add(self._row("Mi micrófono", "Tu voz aparece como «Yo» en el historial.",
                              self.w_mic_device, restart=True))
        sources.add(self._row("Escuchar mi micrófono al abrir", "Puedes encenderlo o apagarlo luego desde el HUD.",
                              self.w_mic_enabled))
        routing = self._section(layout, "Ruta y detección")
        routing.add(self._row("Enviar el audio a BlackHole automáticamente",
                              "Crea «Copilot (salida + BlackHole)» al escuchar la llamada y vuelve a tu salida "
                              "normal al salir. Apágalo si ya tienes tu propio dispositivo múltiple.",
                              self.w_auto_route, restart=True))
        routing.add(self._row("Sensibilidad", "Volumen mínimo que cuenta como voz. Bájalo (p. ej. 0.004) si no "
                              "detecta frases; súbelo si transcribe ruido.", self.w_silence))
        layout.addStretch()
        return page

    def _transcription_page(self) -> QWidget:
        page, layout = self._page("Transcripción", "Cómo se convierte la voz en texto. Todo ocurre en tu Mac.")
        self.w_whisper = self._combo([(m, m) for m in WHISPER_MODELS], self.cfg.whisper_model)
        self.w_language = self._combo(LANGUAGES, self.cfg.whisper_language)
        self.w_silence_duration = self._spin(self.cfg.silence_duration, 0.3, 3.0, 0.1, decimals=1, suffix="s")
        group = self._section(layout)
        group.add(self._row("Modelo Whisper", "small: buen equilibrio en un M3. base: más rápido, menos preciso. "
                            "medium y large: más precisos pero más lentos.", self.w_whisper, restart=True))
        group.add(self._row("Idioma", "", self.w_language, restart=True))
        group.add(self._row("Pausa que cierra una frase", "Más corta: respuestas antes, pero frases partidas.",
                            self.w_silence_duration, restart=True))
        layout.addStretch()
        return page

    def _ai_page(self) -> QWidget:
        page, layout = self._page("Copiloto", "Qué modelo responde y qué genera durante la llamada.")
        self.w_provider = self._combo([(p.label, key) for key, p in providers.PROVIDERS.items()],
                                      self.cfg.llm_provider)
        self.w_url = QLineEdit(self.cfg.llm_base_url)
        self.w_url.setFixedWidth(CONTROL_WIDTH)
        self.w_model = self._combo([(self.cfg.llm_model, self.cfg.llm_model)], self.cfg.llm_model, editable=True)
        self.w_model.setFixedWidth(CONTROL_WIDTH - 40)
        refresh = self._icon_button("refresh", "Buscar modelos en LM Studio", self._refresh_models)
        self.w_model_status = self._hint("")
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        model_row.addWidget(self.w_model)
        model_row.addWidget(refresh)
        claude = providers.PROVIDERS["claude"]
        self.w_claude_key = self._key_field("claude")
        self.w_claude_model = self._combo([(m, m) for m in claude.models], self.cfg.claude_model, editable=True)
        self.w_custom_url = QLineEdit(self.cfg.custom_base_url)
        self.w_custom_url.setPlaceholderText("https://api.openai.com/v1")
        self.w_custom_url.setFixedWidth(CONTROL_WIDTH)
        self.w_custom_key = self._key_field("openai")
        self.w_custom_model = QLineEdit(self.cfg.custom_model)
        self.w_custom_model.setPlaceholderText("p. ej. gpt-5-mini")
        self.w_custom_model.setFixedWidth(CONTROL_WIDTH)
        self.w_temperature = self._spin(self.cfg.llm_temperature, 0.0, 1.0, 0.05, decimals=2)
        self.w_max_tokens = self._spin(self.cfg.llm_max_tokens, 40, 600, 20, suffix="tokens")
        self.w_cooldown = self._spin(self.cfg.llm_cooldown_seconds, 0.5, 30.0, 0.5, decimals=1, suffix="s")
        self.w_note_interval = self._spin(self.cfg.note_interval_seconds, 2.0, 120.0, 1.0, decimals=1, suffix="s")
        self.w_answers = self._switch(self.cfg.answers_enabled)
        self.w_notes = self._switch(self.cfg.notes_enabled)
        self.w_screen = self._switch(self.cfg.screen_notes_enabled)

        chooser = self._section(layout, "Proveedor")
        test = self._button("Probar conexión", "pulse")
        test.clicked.connect(self._test_connection)
        self.w_provider_note = self._hint("")
        provider = self._row("Proveedor predeterminado", "", self.w_provider)
        provider.layout().itemAt(0).layout().addWidget(self.w_provider_note)
        chooser.add(provider)
        self.w_test_result = self._hint("")
        check = self._row("Comprobar", "Hace una consulta mínima con lo que hay escrito, aunque no esté guardado.",
                          test)
        check.layout().itemAt(0).layout().addWidget(self.w_test_result)
        chooser.add(check)

        # Un grupo por proveedor; solo se ve el del proveedor elegido.
        self._provider_groups = {}
        lm = self._section(layout)
        lm.add(self._row("Servidor", "LM Studio → Developer → Start Server.", self.w_url))
        lm_model = self._row("Modelo", "", model_row)
        lm_model.layout().itemAt(0).layout().addWidget(self.w_model_status)
        lm.add(lm_model)
        self._provider_groups["lmstudio"] = lm
        cl = self._section(layout)
        cl.add(self._row("API key", "Se guarda en el Llavero de macOS, no en archivos. Créala en "
                         "console.anthropic.com.", self.w_claude_key))
        cl.add(self._row("Modelo", "Haiku: rápido y barato, ideal para reuniones. Sonnet: más preciso.",
                         self.w_claude_model))
        self._provider_groups["claude"] = cl
        other = self._section(layout)
        other.add(self._row("Servidor", "Cualquier API compatible con OpenAI: OpenAI, OpenRouter, Groq…",
                            self.w_custom_url))
        other.add(self._row("API key", "Se guarda en el Llavero de macOS, no en archivos.", self.w_custom_key))
        other.add(self._row("Modelo", "", self.w_custom_model))
        self._provider_groups["openai"] = other
        self.w_provider.currentIndexChanged.connect(self._on_provider_changed)
        self._on_provider_changed()

        model = self._section(layout, "Respuestas")
        model.add(self._row("Creatividad", "Baja es más fiel al conocimiento (recomendado 0.1–0.3).",
                            self.w_temperature))
        model.add(self._row("Largo máximo", "Respuestas más cortas llegan antes.", self.w_max_tokens))
        output = self._section(layout, "Qué genera")
        output.add(self._row("Respuestas a preguntas", "Cuando alguien de la llamada pregunta algo, propone qué "
                             "contestar.", self.w_answers))
        output.add(self._row("Notas sobre la conversación", "Comentarios breves y datos útiles sobre lo que se "
                             "habla.", self.w_notes))
        output.add(self._row("Notas sobre la pantalla", "Explica la pantalla de Odoo que tengas abierta en Chrome.",
                             self.w_screen))
        pace = self._section(layout, "Ritmo")
        pace.add(self._row("Espera entre análisis", "Tiempo mínimo entre dos llamadas al modelo.", self.w_cooldown))
        pace.add(self._row("Una nota cada", "Evita que las notas tapen las respuestas.", self.w_note_interval))
        layout.addStretch()
        self._refresh_models()
        return page

    def _knowledge_page(self) -> QWidget:
        page, layout = self._page("Conocimiento", "Lo que el copiloto sabe de cada proyecto. Elige el proyecto de "
                                  "la reunión: solo se carga su conocimiento.")
        self._project_providers = dict(self.cfg.project_providers)
        self.w_project_active = QComboBox()
        self.w_project_active.setFixedWidth(CONTROL_WIDTH)
        self.w_project_info = self._hint("")
        self.w_project_provider = self._combo(
            [("Usar el predeterminado", "")] + [(p.label, key) for key, p in providers.PROVIDERS.items()], "")
        self.w_context = QLineEdit(self.cfg.context_path)
        self.w_context.setFixedWidth(CONTROL_WIDTH - 40)
        choose = self._icon_button("folder", "Elegir carpeta…", self._choose_knowledge)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(self.w_context)
        path_row.addWidget(choose)
        self.w_knowledge_info = self._hint("")
        self.w_project = QLineEdit(self.cfg.knowledge_project)
        self.w_project.setFixedWidth(CONTROL_WIDTH - 126)
        update = self._button("Exportar", "refresh")
        update.setFixedWidth(120)
        update.clicked.connect(self._update_knowledge)
        project_row = QHBoxLayout()
        project_row.setSpacing(6)
        project_row.addWidget(self.w_project)
        project_row.addWidget(update)

        active = self._section(layout, "Reunión")
        project = self._row("Proyecto", "", self.w_project_active)
        project.layout().itemAt(0).layout().addWidget(self.w_project_info)
        active.add(project)
        active.add(self._row("Proveedor para este proyecto", "Por ejemplo, un proyecto con datos de clientes "
                             "siempre en local y los demás con Claude.", self.w_project_provider))
        sources = self._section(layout, "Fuentes")
        folder = self._row("Carpeta de conocimiento", "Un .md o .txt (o una subcarpeta) por proyecto.", path_row)
        folder.layout().itemAt(0).layout().addWidget(self.w_knowledge_info)
        sources.add(folder)
        sources.add(self._row("Exportar desde aiviber", "Guarda las notas del proyecto (vía epona) como "
                              "knowledge/<proyecto>.md. Tarda unos 2 minutos y omite las notas técnicas.",
                              project_row))
        self.w_export_log = QPlainTextEdit()
        self.w_export_log.setReadOnly(True)
        self.w_export_log.setMinimumHeight(120)
        self.w_export_log.setPlaceholderText("Aquí aparece el progreso cuando exportas desde aiviber.")
        layout.addSpacing(10)
        layout.addWidget(self.w_export_log, 1)
        self.w_context.textChanged.connect(self._refresh_projects)
        self.w_project_active.currentIndexChanged.connect(self._on_project_changed)
        self.w_project_provider.currentIndexChanged.connect(self._on_project_provider_changed)
        self._refresh_projects()
        return page

    def _icon_button(self, icon_name: str, tip: str, slot) -> QPushButton:
        button = QPushButton()
        button.setIcon(T.icon(icon_name, T.TEXT, 15))
        button.setToolTip(tip)
        button.setFixedWidth(34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _key_field(self, provider: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setFixedWidth(CONTROL_WIDTH)
        stored = bool(providers.get_key(provider))
        field.setPlaceholderText("Guardada · escribe para cambiarla" if stored else "Pega aquí tu API key")
        return field

    def _hud_page(self) -> QWidget:
        page, layout = self._page("HUD", "La ventana flotante que ves durante la llamada.")
        self.w_opacity = self._spin(self.cfg.hud_opacity, 0.5, 1.0, 0.05, decimals=2)
        self.w_font = self._spin(self.cfg.hud_font_size, 10, 28, 1, suffix="px")
        self.w_width = self._spin(self.cfg.hud_width, 320, 1200, 20, suffix="px")
        group = self._section(layout)
        group.add(self._row("Opacidad", "1 es opaco. Bájala si quieres ver lo que hay detrás.", self.w_opacity))
        group.add(self._row("Tamaño de las respuestas", "Texto de la tarjeta de respuesta.", self.w_font))
        group.add(self._row("Ancho", "El alto se ajusta arrastrando la esquina inferior derecha.", self.w_width))
        layout.addStretch()
        return page

    def _sessions_page(self) -> QWidget:
        page, layout = self._page("Sesiones", "Cada reunión queda guardada en tu Mac. «Nueva sesión» archiva la "
                                  "actual y empieza otra.")
        self.w_sessions = QListWidget()
        self.w_sessions.setObjectName("sessions")
        self.w_sessions.setMinimumHeight(260)
        layout.addWidget(self.w_sessions, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, icon_name, slot in (("Exportar a Markdown", "export", self._export_session),
                                      ("Abrir carpeta", "folder", self._open_sessions),
                                      ("Nueva sesión", "plus", self._new_session)):
            button = self._button(text, icon_name)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch()
        layout.addSpacing(8)
        layout.addLayout(row)
        self._refresh_sessions()
        return page

    def _update_app_status(self) -> None:
        conn = providers.connection(self.cfg)
        if not conn.info.local:
            ok = bool(conn.api_key)
            self.app_status.setText(f"Usando {conn.info.label.split(' (')[0]}" if ok else "Falta la API key")
            self.app_status.setStyleSheet(f"color: {T.TEAL if ok else T.RED};")
        elif self._lm_error:
            self.app_status.setText("LM Studio sin conexión")
            self.app_status.setStyleSheet(f"color: {T.RED};")
        else:
            self.app_status.setText("Conectado a LM Studio")
            self.app_status.setStyleSheet(f"color: {T.TEAL};")

    # ------------------------------------------------------------------ acciones
    def _refresh_models(self) -> None:
        current = self.w_model.currentText()
        try:
            client = OpenAI(base_url=self.w_url.text().strip(), api_key="lm-studio", timeout=3)
            models = [m.id for m in client.models.list().data if "embed" not in m.id.lower()]
        except Exception as exc:
            self._lm_error = str(exc)
            self.w_model_status.setText(f"No se pudo conectar con LM Studio: {str(exc)[:80]}")
            if hasattr(self, "app_status"):
                self._update_app_status()
            return
        self._lm_error, self._lm_models = "", models
        if hasattr(self, "app_status"):
            self._update_app_status()
        self.w_model.clear()
        for model in models:
            self.w_model.addItem(model, model)
        index = self.w_model.findData(current)
        if index >= 0:
            self.w_model.setCurrentIndex(index)
        else:
            self.w_model.setEditText(current)
        self.w_model_status.setText(f"{len(models)} modelo(s) en LM Studio.")
        self.w_model.lineEdit().setCursorPosition(0)

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

    def _refresh_projects(self) -> None:
        """Rellena «Proyecto» con los de la carpeta de conocimiento (conserva el elegido si sigue existiendo)."""
        folder = self.w_context.text().strip() or "knowledge"
        names = providers.list_projects(folder)
        current = self.w_project_active.currentText() or self.cfg.knowledge_project
        self.w_project_active.blockSignals(True)
        self.w_project_active.clear()
        self.w_project_active.addItems(names)
        if current in names:
            self.w_project_active.setCurrentText(current)
        self.w_project_active.blockSignals(False)
        self.w_knowledge_info.setText(f"{len(names)} proyecto(s): {', '.join(names)}" if names
                                      else "No hay proyectos: exporta uno desde aiviber o añade un .md.")
        self._on_project_changed()

    def _on_project_changed(self) -> None:
        name = self.w_project_active.currentText()
        source = providers.knowledge_source(self.w_context.text().strip() or "knowledge", name)
        text = load_knowledge(str(source), 10**9) if name else ""
        if not text:
            self.w_project_info.setText("Sin conocimiento: el copiloto responderá de forma genérica.")
        else:
            limit = self.cfg.context_max_chars
            extra = f", se recorta a {limit:,} caracteres" if len(text) > limit else ""
            self.w_project_info.setText(f"{text.count(chr(10) + '[')} notas, ~{len(text) // 4:,} tokens{extra}")
        self.w_project_provider.blockSignals(True)
        index = self.w_project_provider.findData(self._project_providers.get(name, ""))
        self.w_project_provider.setCurrentIndex(max(index, 0))
        self.w_project_provider.blockSignals(False)
        if name:
            self.w_project.setText(name)

    def _on_project_provider_changed(self) -> None:
        name, value = self.w_project_active.currentText(), self.w_project_provider.currentData()
        if not name:
            return
        if value:
            self._project_providers[name] = value
        else:
            self._project_providers.pop(name, None)

    def _on_provider_changed(self) -> None:
        key = self.w_provider.currentData()
        for name, group in self._provider_groups.items():
            group.setVisible(name == key)
        info = providers.PROVIDERS[key]
        self.w_provider_note.setText("Todo se queda en tu Mac." if info.local else
                                     f"La transcripción y el conocimiento se envían a {info.label.split(' (')[0]}.")
        self.w_test_result.setText("")
        self.w_test_result.setVisible(False)

    def _form_connection(self) -> providers.Connection:
        """La conexión tal como está escrita en el formulario (aún sin guardar)."""
        key = self.w_provider.currentData()
        if key == "claude":
            return providers.Connection(key, providers.CLAUDE_URL, self._value(self.w_claude_model),
                                        self.w_claude_key.text().strip() or providers.get_key(key))
        if key == "openai":
            return providers.Connection(key, self.w_custom_url.text().strip(), self.w_custom_model.text().strip(),
                                        self.w_custom_key.text().strip() or providers.get_key(key))
        return providers.Connection(key, self.w_url.text().strip(), self.w_model.currentText().strip(), "lm-studio")

    def _test_connection(self) -> None:
        conn = self._form_connection()
        if conn.info.needs_key and not conn.api_key:
            self._show_test(False, "Falta la API key.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            client = OpenAI(base_url=conn.base_url, api_key=conn.api_key, timeout=15)
            if conn.info.local:
                ids = [m.id for m in client.models.list().data]
                ok = conn.model in ids
                self._show_test(ok, f"Conectado. «{conn.model}» disponible." if ok
                                else f"Conectado, pero «{conn.model}» no está en LM Studio.")
            else:
                client.chat.completions.create(model=conn.model, max_tokens=1,
                                               messages=[{"role": "user", "content": "ok"}])
                self._show_test(True, f"Conectado. «{conn.model}» responde.")
        except Exception as exc:
            self._show_test(False, f"No conecta: {str(exc)[:160]}")
        finally:
            QApplication.restoreOverrideCursor()

    def _show_test(self, ok: bool, text: str) -> None:
        self.w_test_result.setVisible(True)
        self.w_test_result.setText(text)
        self.w_test_result.setStyleSheet(f"color: {T.TEAL if ok else T.RED};")

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
        self.w_export_log.appendPlainText("✓ Listo. Pulsa Guardar para usarlo en la reunión."
                                          if code == 0 else f"✗ Falló (código {code}).")
        exported = self.w_project.text().strip()
        self.w_context.setText("knowledge")
        self._refresh_projects()
        if code == 0 and self.w_project_active.findText(exported) >= 0:
            self.w_project_active.setCurrentText(exported)  # la reunión pasa a usar lo recién exportado
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
            label = "Sesión en curso" if path.name == "current.json" else _session_title(path.stem)
            item = QListWidgetItem(T.icon("history", T.MUTED, 16),
                                   f"{label}\n{len(data.get('entries', []))} entradas · "
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
            "llm_provider": self.w_provider.currentData(),
            "claude_model": self._value(self.w_claude_model),
            "custom_base_url": self.w_custom_url.text().strip(),
            "custom_model": self.w_custom_model.text().strip(),
            "project_providers": dict(self._project_providers),
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
            "knowledge_project": self.w_project_active.currentText() or self.cfg.knowledge_project,
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
        for provider, field in (("claude", self.w_claude_key), ("openai", self.w_custom_key)):
            if field.text().strip():  # al Llavero, nunca a settings.json
                providers.set_key(provider, field.text().strip())
                changed.add("api_key")
        settings_store.apply(self.cfg, values)
        settings_store.save(self.cfg)
        pending = changed & settings_store.RESTART_REQUIRED
        if pending and not restart:
            QMessageBox.information(self, "Guardado", "Guardado. Algunos cambios (↻) se aplicarán la próxima vez "
                                    "que abras el copiloto, o con «Guardar y reiniciar».")
        self.saved.emit(changed, restart)
        self.accept()
