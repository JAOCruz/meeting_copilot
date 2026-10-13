"""HUD flotante: siempre visible, semitransparente, sin bordes y arrastrable."""
from PyQt6.QtCore import QObject, Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from config import Config


class HudSignals(QObject):
    """Puente thread-safe: los hilos de trabajo emiten, la GUI recibe en el hilo principal."""
    status = pyqtSignal(str)
    transcript = pyqtSignal(str)
    suggestion_start = pyqtSignal()
    suggestion_token = pyqtSignal(str)


class HudWindow(QWidget):
    def __init__(self, cfg: Config, signals: HudSignals):
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

        container = QWidget(self)
        container.setObjectName("card")
        container.setStyleSheet("""
            #card { background-color: rgba(18, 18, 22, 235); border-radius: 14px;
                    border: 1px solid rgba(255, 255, 255, 40); }
            QLabel { color: #f2f2f2; background: transparent; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        self.status_label = QLabel("● Iniciando…")
        self.status_label.setStyleSheet("color: #8a8f98; font-size: 11px;")

        self.transcript_label = QLabel("")
        self.transcript_label.setWordWrap(True)
        self.transcript_label.setStyleSheet("color: #9aa0a6; font-size: 12px; font-style: italic;")

        self.suggestion_label = QLabel("Esperando una pregunta…")
        self.suggestion_label.setWordWrap(True)
        font = QFont()  # fuente del sistema (San Francisco en macOS)
        font.setPointSize(cfg.hud_font_size)
        font.setWeight(QFont.Weight.DemiBold)
        self.suggestion_label.setFont(font)
        self.suggestion_label.setStyleSheet("color: #7CFFB2;")
        self.suggestion_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        hint = QLabel("arrastra para mover · doble clic para cerrar")
        hint.setStyleSheet("color: #5f6368; font-size: 10px;")

        for w in (self.status_label, self.transcript_label, self.suggestion_label, hint):
            layout.addWidget(w)

        signals.status.connect(lambda s: self.status_label.setText(f"● {s}"))
        signals.transcript.connect(self._on_transcript)
        signals.suggestion_start.connect(lambda: self.suggestion_label.setText(""))
        signals.suggestion_token.connect(self._on_token)

        self._place_top_right()

    def _place_top_right(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        self.move(screen.right() - self.width() - 24, screen.top() + 24)

    def _on_transcript(self, text: str) -> None:
        self.transcript_label.setText(f"“{text[-160:]}”")
        self.adjustSize()

    def _on_token(self, token: str) -> None:
        self.suggestion_label.setText(self.suggestion_label.text() + token)
        self.adjustSize()

    # --- arrastrar / cerrar ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event):
        self.close()
