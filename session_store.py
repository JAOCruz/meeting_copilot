"""Guarda el historial del HUD en disco para que sobreviva a los reinicios.

sessions/current.json = la sesión en curso; 🧹 la archiva como sessions/<fecha>.json y empieza otra.
sessions/ está en .gitignore: contiene lo que se habló en las reuniones.
"""
import json
import time
from pathlib import Path

DIR = Path(__file__).parent / "sessions"
CURRENT = DIR / "current.json"


def load() -> list[list[str]]:
    """Entradas [tipo, prefijo, texto] de la sesión en curso (vacío si no hay o está dañada)."""
    try:
        entries = json.loads(CURRENT.read_text(encoding="utf-8"))["entries"]
        return [list(e) for e in entries if isinstance(e, list) and len(e) == 3]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save(entries: list[list[str]]) -> None:
    DIR.mkdir(exist_ok=True)
    tmp = CURRENT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "entries": entries},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(CURRENT)  # escritura atómica: un cierre a mitad no deja el archivo roto


def archive() -> None:
    if CURRENT.exists():
        CURRENT.replace(DIR / f"{time.strftime('%Y-%m-%d_%H%M%S')}.json")
