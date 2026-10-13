"""Detecta en qué pantalla de Odoo está el usuario leyendo la pestaña activa de Google Chrome."""
import re
import subprocess
from urllib.parse import urlparse

# Pestaña activa de cada ventana, una por línea ("título ||| URL"), la ventana del frente primero:
# durante una reunión el frente suele ser Meet y Odoo está en la ventana que se comparte.
SCRIPT = """if application "Google Chrome" is running then
    set out to ""
    tell application "Google Chrome"
        repeat with w in windows
            set out to out & (title of active tab of w) & " ||| " & (URL of active tab of w) & linefeed
        end repeat
    end tell
    return out
end if"""


def active_odoo_screen() -> tuple[str, str] | None:
    """(título, ruta) de la primera pestaña activa de Chrome que sea Odoo; None si no hay.
    La primera vez macOS pide permiso para que la terminal controle Chrome."""
    try:
        out = subprocess.run(["osascript", "-e", SCRIPT], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        title, sep, url = line.partition(" ||| ")
        path = urlparse(url).path
        if sep and (path.startswith("/odoo") or path.startswith("/web")):
            title = re.sub(r"\s*[-|]\s*Odoo\s*$|^Odoo\s*[-|]\s*", "", title).strip()
            return title, path
    return None


def section_key(title: str, path: str) -> str:
    """Identifica la sección sin el registro concreto: /odoo/action-348/15 y /odoo/action-348 son la misma."""
    return re.sub(r"(/\d+|/new)+$", "", path)
