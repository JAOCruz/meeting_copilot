"""HUD flotante: siempre visible, sin bordes y arrastrable. Arriba el estado y los interruptores, luego la
última respuesta fija en una tarjeta y debajo el historial filtrable de la sesión."""
import html
import re
import sys
import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QGuiApplication, QIcon
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QSizeGrip,
                             QSizePolicy, QSystemTrayIcon, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

import session_store
import ui_theme as T
from config import Config

UNVERIFIED = re.compile(r"\s*\(⚠ sin verificar:\s*([^)]*)\)")
FILTERS = [("all", "Todo"), ("answer", "Respuestas"), ("note", "Notas"), ("line", "Transcripción")]
EMPTY_FEED = {
    "all": "Lo que se diga en la llamada aparecerá aquí.",
    "answer": "Aún no hay respuestas. Aparecen cuando alguien hace una pregunta.",
    "note": "Aún no hay notas.",
    "line": "Aún no hay transcripción. Revisa que la llamada y el micrófono estén encendidos.",
}


class HudSignals(QObject):
    """Puente thread-safe: los hilos de trabajo emiten, la GUI recibe en el hilo principal."""
    status = pyqtSignal(str)
    transcript = pyqtSignal(str, str)  # (quién, texto)
    suggestion_start = pyqtSignal()
    suggestion_token = pyqtSignal(str)
    note_start = pyqtSignal(str)  # prefijo: "📝 " (comentario) o "📍 <pantalla>: "
    note_token = pyqtSignal(str)
    ask = pyqtSignal(str)  # pregunta escrita en el HUD


class Controls(QObject):
    """Acciones compartidas por los botones del HUD y el menú de la barra superior."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.call = self._toggle("Llamada", "call", "escuchar el audio de la llamada (BlackHole)")
        self.mic = self._toggle("Mi micrófono", "mic", "escuchar tu micrófono (aparece como «Yo»)")
        self.copilot = self._toggle("Copiloto", "spark", "generar respuestas y notas con el modelo")
        self.screen = self._toggle("Pantalla", "screen", "notas sobre la pantalla de Odoo abierta en Chrome")
        self.settings = self._action("Configuración…", "sliders", "Configuración")
        self.clear = self._action("Nueva sesión (archivar la actual)", "archive",
                                  "Archivar la sesión y empezar una nueva")
        self.hide_hud = self._action("Ocultar", "close", "Ocultar (vuelve desde el ícono de la barra de menú)")
        compact_icon = QIcon()
        for state, name in ((QIcon.State.Off, "collapse"), (QIcon.State.On, "expand")):
            compact_icon.addPixmap(T.pixmap(name, T.MUTED), QIcon.Mode.Normal, state)
            compact_icon.addPixmap(T.pixmap(name, T.TEXT), QIcon.Mode.Active, state)
        self.compact = QAction(compact_icon, "Modo compacto", self, checkable=True,
                               toolTip="Modo compacto: solo la respuesta (o doble clic en la cabecera)")
        self.toggle_hud = QAction("Mostrar/ocultar HUD", self)
        self.quit = QAction("Salir del copiloto", self)
        for action in self.findChildren(QAction):
            action.setIconVisibleInMenu(False)  # el menú de la barra superior queda nativo, solo texto

    def _toggle(self, text: str, icon_name: str, tip: str) -> QAction:
        return QAction(T.toggle_icon(icon_name), text, self, checkable=True, checked=True,
                       toolTip=f"{text}: {tip}")

    def _action(self, text: str, icon_name: str, tip: str) -> QAction:
        return QAction(T.icon(icon_name, T.MUTED, hover=T.TEXT), text, self, toolTip=tip)


STYLE = f"""
#card {{ background: {T.BG}; border: 1px solid {T.LINE}; border-radius: 14px; }}
QLabel {{ background: transparent; color: {T.TEXT}; }}
#status {{ color: {T.MUTED}; font-size: 12px; }}
#segment {{ background: {T.SURFACE}; border-radius: 9px; }}
#segment QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 4px 8px; }}
#segment QToolButton:checked {{ background: {T.INK_BG}; }}
#segment QToolButton:hover:!checked {{ background: {T.SURFACE_2}; }}
#iconButton {{ background: transparent; border: none; border-radius: 7px; padding: 4px; }}
#iconButton:hover {{ background: {T.SURFACE}; }}
#answer {{ background: {T.INK_BG}; border-radius: 10px; }}
#answer[unverified="true"] {{ background: #2A2416; }}
#accent {{ background: {T.INK}; border-radius: 1px; }}
#accent[unverified="true"] {{ background: {T.AMBER}; }}
#answerTitle {{ color: {T.INK}; font-size: 11px; font-weight: 600; }}
#answerTitle[unverified="true"] {{ color: {T.AMBER}; }}
#answerMeta {{ color: {T.FAINT}; font-size: 11px; }}
#answerContext {{ color: {T.MUTED}; font-size: 12px; }}
#answerWarning {{ color: {T.AMBER}; font-size: 12px; }}
#filters QToolButton {{ color: {T.FAINT}; background: transparent; border: none; border-radius: 6px;
                        padding: 3px 9px; font-size: 12px; }}
#filters QToolButton:checked {{ color: {T.TEXT}; background: {T.SURFACE}; }}
#filters QToolButton:hover:!checked {{ color: {T.MUTED}; }}
QTextBrowser {{ background: transparent; border: none; color: {T.TEXT}; font-size: 13px; }}
QScrollBar:vertical {{ width: 6px; background: transparent; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: {T.SURFACE_2}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
#newMessages {{ color: #0F1B2D; background: {T.INK}; border: none; border-radius: 12px; padding: 4px 10px 4px 6px;
                font-size: 12px; font-weight: 600; }}
QLineEdit {{ color: {T.TEXT}; background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 9px;
             padding: 7px 8px; font-size: 13px; selection-background-color: {T.INK_BG}; }}
QLineEdit:focus {{ border-color: {T.INK}; }}
"""


def _status_color(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ("error", "no llega", "no hay")):
        return T.RED
    if lowered.startswith(("analizando", "aplicando", "iniciando")):
        return T.INK
    if lowered.startswith("en pausa"):
        return T.FAINT
    if "pausa" in lowered:
        return T.AMBER
    return T.TEAL


def _note_label(prefix: str) -> str:
    """'📍 Pantalla: Facturas: ' → 'En pantalla: Facturas'; '📝 ' → 'Nota'."""
    label = prefix.strip().rstrip(":").strip()
    if label.startswith("📍"):
        title = label[1:].strip()
        if title.lower().startswith("pantalla"):
            title = title[len("pantalla"):].strip(" :")
        return f"En pantalla: {title}" if title else "En pantalla"
    return "Nota"


def _split_unverified(text: str) -> tuple[str, str]:
    """Separa la marca '(⚠ sin verificar: …)' que añade el LLM para mostrarla aparte."""
    match = UNVERIFIED.search(text)
    if not match:
        return text, ""
    return UNVERIFIED.sub("", text).strip(), match.group(1).strip()


class HudWindow(QWidget):
    def __init__(self, cfg: Config, signals: HudSignals, controls: Controls):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no aparece en el Dock ni roba el foco
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(cfg.hud_opacity)
        self.setMinimumWidth(340)
        self.resize(cfg.hud_width, 10)
        self._drag_offset: QPoint | None = None
        self._signals, self._controls = signals, controls

        container = QWidget(self)
        container.setObjectName("card")
        container.setStyleSheet(STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addLayout(self._build_status_row())
        layout.addLayout(self._build_toolbar())
        layout.addWidget(self._build_answer_card())
        self.filters = self._build_filters()
        layout.addWidget(self.filters)

        # Historial de toda la sesión: lo que se dice, las respuestas y las notas, en orden.
        self.feed = QTextBrowser()
        self.feed.setOpenLinks(False)
        self.feed.setMinimumHeight(cfg.hud_feed_height)
        self.feed.installEventFilter(self)
        layout.addWidget(self.feed, 1)
        self.new_button = QToolButton(self.feed)
        self.new_button.setObjectName("newMessages")
        self.new_button.setIcon(T.icon("arrow-down", "#0F1B2D", 14))
        self.new_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_button.clicked.connect(self._jump_to_latest)
        self.new_button.hide()

        self.ask_input = QLineEdit()
        self.ask_input.setPlaceholderText("Pregúntale algo al copiloto")
        send = self.ask_input.addAction(T.icon("return", T.FAINT, hover=T.INK),
                                        QLineEdit.ActionPosition.TrailingPosition)
        send.setToolTip("Enviar (Enter)")
        send.triggered.connect(self._on_ask)
        self.ask_input.returnPressed.connect(self._on_ask)
        self.ask_row = QWidget()
        bottom = QHBoxLayout(self.ask_row)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(4)
        bottom.addWidget(self.ask_input, 1)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.ask_row)

        # [tipo, prefijo, texto, hora]; sobrevive a los reinicios
        self._entries: list[list[str]] = session_store.load()
        self._filter = "all"
        self._answer_font = cfg.hud_font_size
        self._model = cfg.llm_model
        # Pegado al fondo: si el usuario está abajo, los mensajes nuevos lo mantienen abajo; si sube,
        # se suelta y no se le mueve; si vuelve a bajar hasta el final, se pega otra vez.
        self._follow = True
        self._unseen = 0
        self._rendering = False
        self._dirty = False
        self._full_height = 0
        self.feed.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        # Los tokens llegan muy seguidos: el historial se redibuja como mucho cada 50 ms.
        self._render_timer = QTimer(self, singleShot=True, interval=50)
        self._render_timer.timeout.connect(self._flush)
        QTimer.singleShot(0, self._show_restored)

        signals.status.connect(self.set_status)
        signals.transcript.connect(self._on_transcript)
        signals.suggestion_start.connect(lambda: self._add("answer", "💬 ", ""))
        signals.suggestion_token.connect(self._append_token)
        signals.note_start.connect(lambda prefix: self._add("note", prefix, ""))
        signals.note_token.connect(self._append_token)
        controls.clear.triggered.connect(self.clear)
        controls.hide_hud.triggered.connect(self.hide)
        controls.toggle_hud.triggered.connect(lambda: self.setVisible(not self.isVisible()))
        controls.compact.toggled.connect(self.set_compact)

        self.set_status("Iniciando…")
        self._place_top_right()

        # Los ajustes de macOS se aplican antes de mostrar la ventana por primera vez (si no, se queda
        # en el Space donde nació) y se reafirman cada 2 s por si macOS la manda al fondo.
        if sys.platform == "darwin":
            _float_over_fullscreen(self)
            self._keep_front = QTimer(self)
            self._keep_front.timeout.connect(lambda: self.isVisible() and _float_over_fullscreen(self))
            self._keep_front.start(2000)

    # ------------------------------------------------------------------ construcción
    def _build_status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.dot = T.StatusDot()
        self.status_label = QLabel()
        self.status_label.setObjectName("status")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.dot)
        row.addWidget(self.status_label, 1)
        self.meters = {}
        for key, icon_name, tip in (("call", "call", "Nivel de la llamada"), ("mic", "mic", "Nivel de tu micrófono")):
            glyph = QLabel()
            glyph.setPixmap(T.pixmap(icon_name, T.FAINT, 13))
            meter = T.LevelMeter()
            glyph.setToolTip(tip)
            meter.setToolTip(tip)
            row.addSpacing(4)
            row.addWidget(glyph)
            row.addWidget(meter)
            self.meters[key] = meter
        return row

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        segment = QFrame()
        segment.setObjectName("segment")
        seg_layout = QHBoxLayout(segment)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)
        c = self._controls
        for action in (c.call, c.mic, c.copilot, c.screen):
            seg_layout.addWidget(self._tool_button(action))
        row.addWidget(segment)
        row.addStretch()
        for action in (c.compact, c.settings, c.clear, c.hide_hud):
            button = self._tool_button(action)
            button.setObjectName("iconButton")
            row.addWidget(button)
        return row

    @staticmethod
    def _tool_button(action: QAction) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _build_answer_card(self) -> QFrame:
        self.card = QFrame()
        self.card.setObjectName("answer")
        row = QHBoxLayout(self.card)
        row.setContentsMargins(0, 0, 12, 0)
        row.setSpacing(11)
        self.accent = QFrame()
        self.accent.setObjectName("accent")
        self.accent.setFixedWidth(3)
        row.addWidget(self.accent)

        body = QVBoxLayout()
        body.setContentsMargins(0, 10, 0, 11)
        body.setSpacing(5)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.answer_title = QLabel("Respuesta")
        self.answer_title.setObjectName("answerTitle")
        self.answer_time = QLabel()
        self.answer_time.setObjectName("answerMeta")
        self.model_label = QLabel()
        self.model_label.setObjectName("answerMeta")
        header.addWidget(self.answer_title)
        header.addWidget(self.answer_time)
        header.addStretch()
        header.addWidget(self.model_label)
        self.answer_context = QLabel()
        self.answer_context.setObjectName("answerContext")
        self.answer_context.setWordWrap(True)
        self.answer_context.setTextFormat(Qt.TextFormat.RichText)
        self.answer_text = QLabel()
        self.answer_text.setTextFormat(Qt.TextFormat.RichText)
        self.answer_text.setWordWrap(True)
        self.answer_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.answer_warning = QLabel()
        self.answer_warning.setObjectName("answerWarning")
        self.answer_warning.setWordWrap(True)
        self.answer_warning.setTextFormat(Qt.TextFormat.RichText)
        body.addLayout(header)
        body.addWidget(self.answer_context)
        body.addWidget(self.answer_text)
        body.addWidget(self.answer_warning)
        row.addLayout(body, 1)
        return self.card

    def _build_filters(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("filters")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self._filter_group = QButtonGroup(self)
        for key, label in FILTERS:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(key == "all")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._filter_group.addButton(button)
            button.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            row.addWidget(button)
        row.addStretch()
        return bar

    def _place_top_right(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.top() + 24)

    # ------------------------------------------------------------------ API para main.py
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setToolTip(text)
        self.dot.setColor(QColor(_status_color(text)))

    def set_levels(self, call: float | None, mic: float | None) -> None:
        """Volumen actual de cada fuente (None = apagada)."""
        self.meters["call"].set_level(call)
        self.meters["mic"].set_level(mic)

    def apply_appearance(self, cfg: Config) -> None:
        """Aplica en vivo los ajustes visuales de la ventana de Configuración."""
        self.setWindowOpacity(cfg.hud_opacity)
        self._answer_font = cfg.hud_font_size
        self._model = cfg.llm_model
        self.resize(cfg.hud_width, self.height())
        self._update_card()
        self._schedule()

    def transcript_lines(self) -> list[tuple[str, str]]:
        """Frases guardadas de la sesión, para devolverle el contexto al LLM tras un reinicio."""
        return [(prefix, text) for kind, prefix, text, _ in self._entries if kind == "line"]

    def clear(self) -> None:
        session_store.archive()  # la sesión anterior queda guardada en sessions/<fecha>.json
        self._entries.clear()
        self._unseen = 0
        self._update_card()
        self._flush()

    def set_compact(self, on: bool) -> None:
        """Modo compacto: solo estado, interruptores y la tarjeta de respuesta."""
        if on:
            self._full_height = self.height()
        for widget in (self.filters, self.feed, self.ask_row):
            widget.setVisible(not on)
        if on:
            QTimer.singleShot(0, self._fit_compact)
        else:
            self.resize(self.width(), max(self._full_height, 300))

    def _fit_compact(self) -> None:
        """En modo compacto la ventana mide justo lo que ocupa la tarjeta (crece si la respuesta crece)."""
        if self._controls.compact.isChecked():
            self.layout().activate()
            height = self.layout().heightForWidth(self.width())
            self.resize(self.width(), height if height > 0 else self.sizeHint().height())

    # ------------------------------------------------------------------ entradas
    def _on_transcript(self, speaker: str, text: str) -> None:
        self._add("line", speaker, text)

    def _on_ask(self) -> None:
        text = self.ask_input.text().strip()
        if text:
            self.ask_input.clear()
            self._signals.ask.emit(text)

    def _add(self, kind: str, prefix: str, text: str) -> None:
        self._entries.append([kind, prefix, text, time.strftime("%H:%M")])
        if not self._follow and self._visible(self._entries[-1]):
            self._unseen += 1
        if kind == "answer":
            self._update_card()
        self._schedule()

    def _append_token(self, token: str) -> None:
        if self._entries:
            self._entries[-1][2] += token
            if self._entries[-1][0] == "answer":
                self._update_card()
            self._schedule()

    def _visible(self, entry: list[str]) -> bool:
        kind, prefix = entry[0], entry[1]
        if self._filter == "all":
            return True
        if self._filter == "answer":
            return kind == "answer" or (kind == "line" and prefix == "Pregunta")
        return kind == self._filter

    # ------------------------------------------------------------------ tarjeta de respuesta
    def _update_card(self) -> None:
        index = next((i for i in range(len(self._entries) - 1, -1, -1) if self._entries[i][0] == "answer"), None)
        self.model_label.setText(self._short_model())
        self.answer_text.setStyleSheet(f"font-size: {self._answer_font}px; font-weight: 500; color: {T.TEXT};")
        if index is None:
            self.answer_time.setText("")
            self.answer_context.hide()
            self.answer_warning.hide()
            self.answer_text.setText(f"<span style='color:{T.MUTED}; font-size:13px; font-weight:400'>"
                                     "Cuando alguien haga una pregunta, la respuesta aparecerá aquí.</span>")
            self._set_unverified(False)
            return
        _, _, raw, at = self._entries[index]
        text, problem = _split_unverified(raw)
        self.answer_time.setText(at)
        question = next((e for e in reversed(self._entries[:index])
                         if e[0] == "line" and e[1] in ("Ellos", "Pregunta")), None)
        if question:
            q = question[2] if len(question[2]) <= 110 else question[2][:108].rstrip() + "…"
            self.answer_context.setText(f"«{html.escape(q)}»")
            self.answer_context.show()
        else:
            self.answer_context.hide()
        if text:
            self.answer_text.setText(html.escape(text))
        else:
            self.answer_text.setText(f"<span style='color:{T.MUTED}; font-size:13px; font-weight:400'>"
                                     "Pensando…</span>")
        self.answer_warning.setText(f"Sin verificar: {html.escape(problem)}. Confírmalo antes de decirlo.")
        self.answer_warning.setVisible(bool(problem))
        self._set_unverified(bool(problem))
        if self._controls.compact.isChecked():
            QTimer.singleShot(0, self._fit_compact)

    def _set_unverified(self, on: bool) -> None:
        for widget in (self.card, self.accent, self.answer_title):
            if widget.property("unverified") != on:
                widget.setProperty("unverified", on)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self.answer_title.setText("Respuesta sin verificar" if on else "Respuesta")

    def _short_model(self) -> str:
        name = self._model.split("/")[-1]
        return name if len(name) <= 30 else name[:29] + "…"

    # ------------------------------------------------------------------ historial
    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._follow = True
        self._unseen = 0
        self._flush()

    def _schedule(self) -> None:
        self._dirty = True
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _flush(self) -> None:
        self._render_timer.stop()
        self._render()
        if self._dirty:
            session_store.save(self._entries)
            self._dirty = False

    def _show_restored(self) -> None:
        """Muestra la sesión recuperada empezando por lo último (pegado al fondo)."""
        self._follow = True
        self._update_card()
        self._render()

    def _on_user_scroll(self, value: int) -> None:
        if not self._rendering:  # solo cuentan los movimientos del usuario, no los del redibujado
            self._follow = value >= self.feed.verticalScrollBar().maximum() - 4
            if self._follow:
                self._unseen = 0
                self.new_button.hide()

    def _jump_to_latest(self) -> None:
        self._follow = True
        self._unseen = 0
        self.new_button.hide()
        bar = self.feed.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _entry_html(self, entry: list[str], show_time: bool = True) -> str:
        kind, prefix, text, at = entry
        at = at if show_time else ""
        time_cell = (f"<td width='42' valign='top' style='color:{T.FAINT}; font-size:11px; "
                     f"padding-top:2px'>{html.escape(at)}</td>")
        if kind == "line":
            color = T.SPEAKER_COLORS.get(prefix, T.TEXT)
            return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:5px'><tr>{time_cell}"
                    f"<td valign='top'><span style='color:{color}; font-weight:600'>{html.escape(prefix)}</span>"
                    f"&nbsp;&nbsp;<span style='color:#A9B0BC'>{html.escape(text)}</span></td></tr></table>")
        if kind == "answer":
            body, problem = _split_unverified(text)
            accent, background, title = (T.AMBER, "#2A2416", "Respuesta sin verificar") if problem \
                else (T.INK, T.INK_BG, "Respuesta")
            warning = (f"<br><span style='color:{T.AMBER}; font-size:12px'>Sin verificar: {html.escape(problem)}</span>"
                       if problem else "")
            return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:9px; margin-bottom:4px'>"
                    f"<tr>{time_cell}<td width='3' bgcolor='{accent}'></td>"
                    f"<td bgcolor='{background}' style='padding:7px 10px'>"
                    f"<span style='color:{accent}; font-size:11px; font-weight:600'>{title}</span><br>"
                    f"<span style='color:{T.TEXT}'>{html.escape(body) or '…'}</span>{warning}</td></tr></table>")
        return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:9px; margin-bottom:4px'>"
                f"<tr>{time_cell}<td width='3' bgcolor='{T.TEAL}'></td><td style='padding:2px 10px'>"
                f"<span style='color:{T.TEAL}; font-size:11px; font-weight:600'>{html.escape(_note_label(prefix))}"
                f"</span><br><span style='color:#C3CAD4'>{html.escape(text) or '…'}</span></td></tr></table>")

    def _render(self) -> None:
        bar = self.feed.verticalScrollBar()
        position = bar.value()
        parts, last_time = [], None
        for entry in self._entries:
            if self._visible(entry):
                parts.append(self._entry_html(entry, show_time=entry[3] != last_time))  # la hora solo si cambia
                last_time = entry[3]
        self._rendering = True
        self.feed.setHtml("".join(parts) or
                          f"<p style='color:{T.FAINT}; margin-top:24px' align='center'>{EMPTY_FEED[self._filter]}</p>")
        bar.setValue(bar.maximum() if self._follow else position)
        self._rendering = False
        if self._unseen and not self._follow:
            self.new_button.setText(f"{self._unseen} nuevo{'s' if self._unseen > 1 else ''}")
            self.new_button.adjustSize()
            self._place_new_button()
            self.new_button.show()
        else:
            self.new_button.hide()

    def _place_new_button(self) -> None:
        self.new_button.move(self.feed.width() - self.new_button.width() - 12,
                             self.feed.height() - self.new_button.height() - 10)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.feed and event.type() == QEvent.Type.Resize:
            self._place_new_button()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ arrastrar / compacto
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._controls.compact.toggle()


def create_tray(controls: Controls, parent: QObject) -> QSystemTrayIcon:
    """Ícono en la barra de menú con los mismos controles que el HUD."""
    menu = QMenu()
    menu.addAction(controls.toggle_hud)
    menu.addAction(controls.compact)
    menu.addSeparator()
    for action in (controls.call, controls.mic, controls.copilot, controls.screen):
        menu.addAction(action)
    menu.addSeparator()
    menu.addAction(controls.settings)
    menu.addAction(controls.clear)
    menu.addAction(controls.quit)

    tray = QSystemTrayIcon(T.tray_icon(), parent)
    tray.setToolTip("Meeting Copilot")
    tray.setContextMenu(menu)
    tray.show()
    tray.menu_ref = menu  # el menú no tiene padre: evita que Python lo recolecte
    return tray


def bring_to_front(widget: QWidget) -> None:
    """Muestra una ventana normal (p. ej. Configuración) al frente y con foco de teclado, incluso sobre una
    app en pantalla completa: la app es "accesorio" y por sí sola no se activa."""
    widget.show()
    if sys.platform == "darwin":
        try:
            import objc
            from AppKit import (NSApp, NSFloatingWindowLevel, NSWindowCollectionBehaviorFullScreenAuxiliary,
                                NSWindowCollectionBehaviorMoveToActiveSpace)
            window = objc.objc_object(c_void_p=int(widget.winId())).window()
            window.setCollectionBehavior_(NSWindowCollectionBehaviorMoveToActiveSpace
                                          | NSWindowCollectionBehaviorFullScreenAuxiliary)
            window.setLevel_(NSFloatingWindowLevel)
            NSApp.activateIgnoringOtherApps_(True)
        except ImportError:
            pass
    widget.raise_()
    widget.activateWindow()


def make_accessory_app() -> None:
    """Sin icono en el Dock: solo una app "accesorio" puede mostrar ventanas sobre el Space
    en pantalla completa de otra app. Llamar antes de mostrar cualquier ventana."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory
    except ImportError:
        return
    NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def _float_over_fullscreen(widget: QWidget) -> None:
    """Qt no expone esto: sin ello el HUD se queda en un solo escritorio y no aparece sobre
    Zoom/Meet/Chrome en pantalla completa, y como ventana Tool se oculta cuando la app pierde el foco."""
    try:
        import objc
        from AppKit import (
            NSStatusWindowLevel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
            NSWindowStyleMaskNonactivatingPanel,
        )
    except ImportError:
        print("[hud] Instala pyobjc-framework-Cocoa para ver el HUD sobre apps en pantalla completa.")
        return
    view = objc.objc_object(c_void_p=int(widget.winId()))  # winId() crea la ventana nativa si hace falta
    window = view.window()
    if window is None:
        return
    window.setStyleMask_(window.styleMask() | NSWindowStyleMaskNonactivatingPanel)
    window.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )
    window.setLevel_(NSStatusWindowLevel)
    window.setHidesOnDeactivate_(False)
    if widget.isVisible():
        window.orderFrontRegardless()  # lanzado desde la terminal, la app nunca se "activa"
