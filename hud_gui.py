"""HUD flotante: siempre visible, semitransparente, sin bordes y arrastrable, con controles
y un icono en la barra de menú."""
import html
import sys

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QSystemTrayIcon, QToolButton, QVBoxLayout,
                             QWidget)

from config import Config

SPEAKER_COLORS = {"Yo": "#8ab4f8", "Ellos": "#e0e3e7"}


class HudSignals(QObject):
    """Puente thread-safe: los hilos de trabajo emiten, la GUI recibe en el hilo principal."""
    status = pyqtSignal(str)
    transcript = pyqtSignal(str, str)  # (quién, texto)
    suggestion_start = pyqtSignal()
    suggestion_token = pyqtSignal(str)


class Controls(QObject):
    """Acciones compartidas por los botones del HUD y el menú de la barra superior."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.call = self._toggle("🎧 Llamada", "Escuchar el audio de la llamada (BlackHole)")
        self.mic = self._toggle("🎙 Mi mic", "Escuchar tu micrófono (aparece como 'Yo')")
        self.copilot = self._toggle("✨ Copiloto", "Generar sugerencias con el LLM")
        self.clear = QAction("🧹", self, toolTip="Borrar transcripción y sugerencia")
        self.toggle_hud = QAction("Mostrar/ocultar HUD", self)
        self.hide_hud = QAction("—", self, toolTip="Ocultar (vuelve desde el icono de la barra de menú)")
        self.quit = QAction("✕", self, toolTip="Salir")

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
        self.setFixedWidth(cfg.hud_width)
        self._drag_offset: QPoint | None = None
        self._transcript_lines: list[tuple[str, str]] = []

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
        for action in (controls.call, controls.mic, controls.copilot):
            toolbar.addWidget(self._button(action, TOGGLE_STYLE))
        toolbar.addStretch()
        for action in (controls.clear, controls.hide_hud, controls.quit):
            toolbar.addWidget(self._button(action, PLAIN_STYLE))

        self.status_label = QLabel("● Iniciando…")
        self.status_label.setStyleSheet("color: #8a8f98; font-size: 11px;")

        self.transcript_label = QLabel("")
        self.transcript_label.setWordWrap(True)
        self.transcript_label.setTextFormat(Qt.TextFormat.RichText)
        self.transcript_label.setStyleSheet("color: #9aa0a6; font-size: 12px;")

        self.suggestion_label = QLabel("Esperando una pregunta…")
        self.suggestion_label.setWordWrap(True)
        self.suggestion_label.setTextFormat(Qt.TextFormat.PlainText)
        font = QFont()  # fuente del sistema (San Francisco en macOS)
        font.setPointSize(cfg.hud_font_size)
        font.setWeight(QFont.Weight.DemiBold)
        self.suggestion_label.setFont(font)
        self.suggestion_label.setStyleSheet("color: #7CFFB2;")
        self.suggestion_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        layout.addLayout(toolbar)
        for w in (self.status_label, self.transcript_label, self.suggestion_label):
            layout.addWidget(w)

        signals.status.connect(lambda s: self.status_label.setText(f"● {s}"))
        signals.transcript.connect(self._on_transcript)
        signals.suggestion_start.connect(lambda: self.suggestion_label.setText(""))
        signals.suggestion_token.connect(self._on_token)
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
        self.adjustSize()
        self.move(screen.right() - self.width() - 24, screen.top() + 24)

    def clear(self) -> None:
        self._transcript_lines.clear()
        self.transcript_label.setText("")
        self.suggestion_label.setText("Esperando una pregunta…")
        self.adjustSize()

    def _on_transcript(self, speaker: str, text: str) -> None:
        self._transcript_lines = (self._transcript_lines + [(speaker, text)])[-3:]
        self.transcript_label.setText("<br>".join(
            f"<b style='color:{SPEAKER_COLORS.get(s, '#e0e3e7')}'>{s}:</b> {html.escape(t[-140:])}"
            for s, t in self._transcript_lines
        ))
        self.adjustSize()

    def _on_token(self, token: str) -> None:
        self.suggestion_label.setText(self.suggestion_label.text() + token)
        self.adjustSize()

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
    for action in (controls.call, controls.mic, controls.copilot):
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
