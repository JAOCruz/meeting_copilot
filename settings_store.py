"""Ajustes elegidos en la ventana de Configuración: settings.json sobrescribe los valores de config.py.

Orden de prioridad: config.py / variables de entorno < settings.json < opciones de línea de comandos.
"""
import json
from dataclasses import fields
from pathlib import Path

from config import Config

PATH = Path(__file__).parent / "settings.json"

# Ajustes que solo se aplican al reiniciar (el resto se aplica en vivo).
RESTART_REQUIRED = {"audio_device", "mic_device", "whisper_model", "whisper_language", "auto_route",
                    "silence_duration", "max_segment_seconds", "min_segment_seconds"}


def load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply(cfg: Config, values: dict | None = None) -> None:
    """Copia a `cfg` los ajustes guardados, convirtiendo cada valor al tipo del campo."""
    types = {f.name: type(getattr(cfg, f.name)) for f in fields(cfg)}
    for name, value in (load() if values is None else values).items():
        if name in types:
            try:
                setattr(cfg, name, types[name](value))
            except (TypeError, ValueError):
                print(f"[config] Ignorado {name}={value!r} en settings.json")


def save(cfg: Config) -> None:
    """Guarda todos los campos editables de `cfg` (los que no son constantes internas)."""
    data = {f.name: getattr(cfg, f.name) for f in fields(cfg) if f.name not in {"target_sample_rate"}}
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
