"""Paleta "grafito y tinta", íconos de línea (SVG propios) y widgets pequeños compartidos por el HUD
y la ventana de Configuración."""
import math
import tempfile
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QCheckBox, QSizePolicy, QWidget

# --- Paleta ---
BG = "#1A1E25"          # fondo del HUD y de la ventana
SURFACE = "#232934"     # tarjetas, grupos, campos
SURFACE_2 = "#2C3341"   # elementos elevados / hover
LINE = "#313847"        # bordes y divisores
TEXT = "#E6E9EE"
MUTED = "#8A93A2"
FAINT = "#5D6573"
INK = "#7FB2FF"         # respuestas y acción principal
INK_BG = "#1F2C40"      # fondo teñido de tinta (tarjeta de respuesta, interruptor encendido)
TEAL = "#5CC8B4"        # notas y "escuchando"
AMBER = "#E5AE4A"       # sin verificar / avisos
RED = "#EF7A6D"         # errores
SPEAKER_COLORS = {"Ellos": "#D4D9E1", "Yo": "#A3B9DA", "Pregunta": INK}

# --- Íconos (viewBox 24, trazo redondeado) ---
_PATHS = {
    "call": '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><path d="M4 15h3v5H5.5A1.5 1.5 0 0 1 4 18.5z"/>'
            '<path d="M20 15h-3v5h1.5a1.5 1.5 0 0 0 1.5-1.5z"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>',
    "spark": '<path d="M11 3.5l1.8 4.7 4.7 1.8-4.7 1.8L11 16.5l-1.8-4.7L4.5 10l4.7-1.8z"/>'
             '<path d="M18.5 15l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
    "screen": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8.5 20h7"/><path d="M12 16v4"/>',
    "sliders": '<path d="M4 7h9"/><path d="M17 7h3"/><circle cx="15" cy="7" r="2"/>'
               '<path d="M4 17h3"/><path d="M11 17h9"/><circle cx="9" cy="17" r="2"/>',
    "archive": '<rect x="3" y="4" width="18" height="4.5" rx="1"/><path d="M5 8.5V19a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8.5"/>'
               '<path d="M10 12.5h4"/>',
    "close": '<path d="M6.5 6.5l11 11"/><path d="M17.5 6.5l-11 11"/>',
    "return": '<path d="M9 10l-4 4 4 4"/><path d="M5 14h10.5A3.5 3.5 0 0 0 19 10.5V5"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M6 15l6-6 6 6"/>',
    "collapse": '<path d="M7 14l5-5 5 5"/><path d="M5 19h14"/>',
    "expand": '<path d="M7 10l5 5 5-5"/><path d="M5 5h14"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="M6.5 13.5L12 19l5.5-5.5"/>',
    "wave": '<path d="M3 12h1.5"/><path d="M7 8.5v7"/><path d="M11 5v14"/><path d="M15 8v8"/><path d="M19 10.5v3"/>',
    "text": '<path d="M4 6h16"/><path d="M4 12h11"/><path d="M4 18h14"/>',
    "book": '<path d="M5 18.5V6a2 2 0 0 1 2-2h12v13H7a2 2 0 0 0-2 2v0a2 2 0 0 0 2 2h12"/>',
    "layout": '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M3.5 9h17"/><path d="M9 9v11.5"/>',
    "history": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "pulse": '<path d="M3 12h4l2.5-6.5 5 13L17 12h4"/>',
    "folder": '<path d="M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    "refresh": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    "export": '<path d="M12 4v11"/><path d="M7.5 8.5L12 4l4.5 4.5"/><path d="M5 14v4.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V14"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "bubble": '<path d="M5 4.5h14A1.5 1.5 0 0 1 20.5 6v9.5A1.5 1.5 0 0 1 19 17h-9l-5 3.5V6A1.5 1.5 0 0 1 5 4.5z"/>'
              '<path d="M12 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
}


def _svg(name: str, color: str, stroke: float = 1.7) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>').encode()


def pixmap(name: str, color: str = TEXT, size: int = 18, stroke: float = 1.7) -> QPixmap:
    scale = 2  # nítido en pantallas Retina
    pix = QPixmap(size * scale, size * scale)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(_svg(name, color, stroke))).render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pix.setDevicePixelRatio(scale)
    return pix


def icon(name: str, color: str = TEXT, size: int = 18, hover: str | None = None) -> QIcon:
    result = QIcon(pixmap(name, color, size))
    if hover:
        result.addPixmap(pixmap(name, hover, size), QIcon.Mode.Active)
    return result


def toggle_icon(name: str, off: str = FAINT, on: str = INK, size: int = 18) -> QIcon:
    """Ícono de un interruptor: apagado en gris tenue, encendido en tinta."""
    result = QIcon()
    result.addPixmap(pixmap(name, off, size), QIcon.Mode.Normal, QIcon.State.Off)
    result.addPixmap(pixmap(name, on, size), QIcon.Mode.Normal, QIcon.State.On)
    result.addPixmap(pixmap(name, MUTED, size), QIcon.Mode.Active, QIcon.State.Off)
    result.addPixmap(pixmap(name, on, size), QIcon.Mode.Active, QIcon.State.On)
    return result


def tray_icon() -> QIcon:
    """Ícono de plantilla para la barra de menú (macOS lo pinta claro u oscuro según el tema)."""
    result = QIcon(pixmap("bubble", "#000000", 22, stroke=1.8))
    result.setIsMask(True)
    return result


def svg_file(name: str, color: str, size: int = 12) -> str:
    """Ruta a un .svg en disco, para las hojas de estilo (p. ej. la flecha de los desplegables)."""
    folder = Path(tempfile.gettempdir()) / "meeting_copilot_ui"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}-{color.strip('#')}.svg"
    if not path.exists():
        path.write_bytes(_svg(name, color, 2.0).replace(b'viewBox', f'width="{size}" height="{size}" viewBox'.encode(), 1))
    return path.as_posix()


# --- Widgets ---
class Switch(QCheckBox):
    """Interruptor tipo macOS. Es un QCheckBox: isChecked()/setChecked()/toggled siguen igual."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self._anim = QVariantAnimation(self, duration=140)
        self._anim.valueChanged.connect(self._set_pos)
        self.toggled.connect(self._animate)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def _set_pos(self, value) -> None:
        self._pos = float(value)
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setChecked(self, on: bool) -> None:  # sin animación al cargar valores
        super().setChecked(on)
        self._anim.stop()
        self._set_pos(1.0 if on else 0.0)

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text()) + 10 if self.text() else 0
        return QSize(36 + text_w, 22)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 2, 34, 18)
        off, on = QColor(SURFACE_2), QColor(INK)
        mix = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                     int(off.green() + (on.green() - off.green()) * self._pos),
                     int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix if self.isEnabled() else QColor(SURFACE))
        p.drawRoundedRect(track, 9, 9)
        p.setBrush(QColor("#FFFFFF") if self.isEnabled() else QColor(FAINT))
        p.drawEllipse(QRectF(3 + self._pos * 16, 4, 14, 14))
        if self.text():
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(46, 0, self.width() - 46, self.height()),
                       Qt.AlignmentFlag.AlignVCenter, self.text())
        p.end()


class LevelMeter(QWidget):
    """Cinco barras con el volumen de una fuente: confirma de un vistazo que llega audio."""

    BARS = 5

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(22, 14)
        self._level = 0.0
        self._active = False

    def set_level(self, rms: float | None) -> None:
        """rms del último bloque, o None si la fuente está apagada."""
        self._active = rms is not None
        target = 0.0
        if rms:
            target = min(1.0, max(0.0, (20 * math.log10(rms + 1e-9) + 60) / 48))  # -60 dB … -12 dB
        # sube al instante y cae despacio, como un vúmetro
        self._level = target if target > self._level else self._level * 0.82 + target * 0.18
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        lit = round(self._level * self.BARS)
        for i in range(self.BARS):
            h = 4 + i * 2.5
            on = self._active and i < lit
            p.setBrush(QColor(TEAL if on else (LINE if self._active else SURFACE)))
            p.drawRoundedRect(QRectF(i * 4.5, self.height() - h, 3, h), 1.2, 1.2)
        p.end()


class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor(FAINT)

    def setColor(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        halo = QColor(self._color)
        halo.setAlpha(60)
        p.setBrush(halo)
        p.drawEllipse(QRectF(0, 0, 10, 10))
        p.setBrush(self._color)
        p.drawEllipse(QRectF(2.5, 2.5, 5, 5))
        p.end()
