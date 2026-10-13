"""HUD flotante: siempre visible, semitransparente, sin bordes y arrastrable, con controles
y un icono en la barra de menú."""
import html
import sys

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMenu, QSizeGrip, QSystemTrayIcon,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from config import Config

SPEAKER_COLORS = {"Yo": "#8ab4f8", "Ellos": "#e0e3e7"}


class HudSignals(QObject):
    """Puente thread-safe: los hilos de trabajo emiten, la GUI recibe en el hilo principal."""
    status = pyqtSignal(str)
    transcript = pyqtSignal(str, str)  # (quién, texto)
    suggestion_start = pyqtSignal()
    suggestion_token = pyqtSignal(str)
    note_start = pyqtSignal(str)  # prefijo: "📝 " (lo que dices) o "📍 Pantalla: "
    note_token = pyqtSignal(str)
    ask = pyqtSignal(str)  # pregunta escrita en el HUD


class Controls(QObject):
    """Acciones compartidas por los botones del HUD y el menú de la barra superior."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.call = self._toggle("🎧 Llamada", "Escuchar el audio de la llamada (BlackHole)")
        self.mic = self._toggle("🎙 Mi mic", "Escuchar tu micrófono (aparece como 'Yo')")
        self.copilot = self._toggle("✨ Copiloto", "Generar sugerencias con el LLM")
        self.screen = self._toggle("📍 Pantalla", "Notas sobre la pantalla de Odoo abierta en Chrome")
        self.clear = QAction("🧹", self, toolTip="Borrar transcripción y sugerencia")
        self.toggle_hud = QAction("Mostrar/ocultar HUD", self)
        self.hide_hud = QAction("✕", self, toolTip="Ocultar (vuelve desde el icono verde de la barra de menú)")
        self.quit = QAction("Salir del copiloto", self)

    def _toggle(self, text: str, tip: str) -> QAction:
        return QAction(text, self, checkable=True, checked=True, toolTip=tip)


TOGGLE_STYLE = """
QToolButton { color: #c8ccd2; background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,30);
              border-radius: 8px; padding: 3px 8px; font-size: 12px; }
QToolButton:checked { color: #0d1f16; background: #7CFFB2; border-color: #7CFFB2; }
QToolButton:hover { border-color: rgba(255,255,255,90); }
"""
PLAIN_STYLE = """
QToolButton { color: #9aa0a6; background: transparent; border: none; padding: 3px 6px; font-size: 13px; }
QToolButton:hover { color: #ffffff; }
"""


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
        self.setMinimumWidth(320)
        self.resize(cfg.hud_width, 10)
        self._drag_offset: QPoint | None = None

        container = QWidget(self)
        container.setObjectName("card")
        container.setStyleSheet("""
            #card { background-color: rgba(18, 18, 22, 250); border-radius: 14px;
                    border: 1px solid rgba(255, 255, 255, 40); }
            QLabel { color: #f2f2f2; background: transparent; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        for action in (controls.call, controls.mic, controls.copilot, controls.screen):
            toolbar.addWidget(self._button(action, TOGGLE_STYLE))
        toolbar.addStretch()
        for action in (controls.clear, controls.hide_hud):
            toolbar.addWidget(self._button(action, PLAIN_STYLE))

        self.status_label = QLabel("● Iniciando…")
        self.status_label.setStyleSheet("color: #8a8f98; font-size: 11px;")

        # Historial de toda la sesión: lo que se dice, las respuestas y las notas, en orden.
        self.feed = QTextBrowser()
        self.feed.setOpenLinks(False)
        self.feed.setMinimumHeight(cfg.hud_feed_height)
        self.feed.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; color: #c8ccd2; font-size: 12px; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,60); border-radius: 3px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }")
        self._entries: list[list[str]] = []  # [tipo, prefijo, texto]
        self._suggestion_font = cfg.hud_font_size

        self.ask_input = QLineEdit()
        self.ask_input.setPlaceholderText("Pregunta rápida… (Enter)")
        self.ask_input.setStyleSheet(
            "QLineEdit { color: #e0e3e7; background: rgba(255,255,255,14); border: 1px solid rgba(255,255,255,30);"
            " border-radius: 8px; padding: 4px 8px; font-size: 12px; }")
        self.ask_input.returnPressed.connect(self._on_ask)
        self._signals = signals

        layout.addLayout(toolbar)
        layout.addWidget(self.status_label)
        layout.addWidget(self.feed, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.ask_input, 1)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(bottom)

        signals.status.connect(lambda s: self.status_label.setText(f"● {s}"))
        signals.transcript.connect(self._on_transcript)
        signals.suggestion_start.connect(lambda: self._add("answer", "💬 ", ""))
        signals.suggestion_token.connect(self._append_token)
        signals.note_start.connect(lambda prefix: self._add("note", prefix, ""))
        signals.note_token.connect(self._append_token)
        controls.clear.triggered.connect(self.clear)
        controls.hide_hud.triggered.connect(self.hide)
        controls.toggle_hud.triggered.connect(lambda: self.setVisible(not self.isVisible()))

        self._place_top_right()

        # Los ajustes de macOS se aplican antes de mostrar la ventana por primera vez (si no, se queda
        # en el Space donde nació) y se reafirman cada 2 s por si macOS la manda al fondo.
        if sys.platform == "darwin":
            _float_over_fullscreen(self)
            self._keep_front = QTimer(self)
            self._keep_front.timeout.connect(lambda: self.isVisible() and _float_over_fullscreen(self))
            self._keep_front.start(2000)

    @staticmethod
    def _button(action: QAction, style: str) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(action)
        button.setStyleSheet(style)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _place_top_right(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.top() + 24)

    def clear(self) -> None:
        self._entries.clear()
        self._render()

    def _on_ask(self) -> None:
        text = self.ask_input.text().strip()
        if text:
            self.ask_input.clear()
            self._signals.ask.emit(text)

    def _on_transcript(self, speaker: str, text: str) -> None:
        self._add("line", speaker, text)

    def _add(self, kind: str, prefix: str, text: str) -> None:
        self._entries.append([kind, prefix, text])
        self._render()

    def _append_token(self, token: str) -> None:
        if self._entries:
            self._entries[-1][2] += token
            self._render()

    def _render(self) -> None:
        bar = self.feed.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        parts = []
        for kind, prefix, text in self._entries:
            body = html.escape(text)
            if kind == "line":
                color = SPEAKER_COLORS.get(prefix, "#e0e3e7")
                parts.append(f"<p style='margin:2px 0'><b style='color:{color}'>{html.escape(prefix)}:</b> "
                             f"<span style='color:#9aa0a6'>{body}</span></p>")
            elif kind == "answer":
                parts.append(f"<p style='margin:6px 0; color:#7CFFB2; font-size:{self._suggestion_font}px;"
                             f" font-weight:600'>{prefix}{body}</p>")
            else:
                parts.append(f"<p style='margin:6px 0; color:#8ab4f8; font-size:13px'>{html.escape(prefix)}{body}</p>")
        self.feed.setHtml("".join(parts) or "<p style='color:#5f6368'>Esperando…</p>")
        if at_bottom:
            bar.setValue(bar.maximum())

    # --- arrastrar ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None


def create_tray(controls: Controls, parent: QObject) -> QSystemTrayIcon:
    """Icono en la barra de menú con los mismos controles que el HUD."""
    pixmap = QPixmap(44, 44)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#7CFFB2"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(6, 10, 32, 24, 7, 7)
    painter.end()

    menu = QMenu()
    menu.addAction(controls.toggle_hud)
    menu.addSeparator()
    for action in (controls.call, controls.mic, controls.copilot, controls.screen):
        menu.addAction(action)
    menu.addSeparator()
    menu.addAction("Borrar transcripción", controls.clear.trigger)
    menu.addAction("Salir", controls.quit.trigger)

    tray = QSystemTrayIcon(QIcon(pixmap), parent)
    tray.setToolTip("Meeting Copilot")
    tray.setContextMenu(menu)
    tray.show()
    tray.menu_ref = menu  # el menú no tiene padre: evita que Python lo recolecte
    return tray


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
