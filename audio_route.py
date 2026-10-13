"""Activa/desactiva el dispositivo de salida múltiple (tu salida + BlackHole) usando tools/audio_route.swift."""
import subprocess
from pathlib import Path

TOOLS = Path(__file__).parent / "tools"
SOURCE = TOOLS / "audio_route.swift"
BINARY = TOOLS / "audio_route"


def _binary() -> Path:
    # Compilado una vez: `swift archivo.swift` tarda varios segundos en cada llamada.
    if not BINARY.exists() or BINARY.stat().st_mtime < SOURCE.stat().st_mtime:
        subprocess.run(["swiftc", "-O", "-suppress-warnings", str(SOURCE), "-o", str(BINARY)],
                       check=True, capture_output=True)
    return BINARY


def set_route(enabled: bool) -> str:
    try:
        result = subprocess.run([str(_binary()), "on" if enabled else "off"],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"No se pudo cambiar la salida de audio: {exc}"
    return (result.stdout or result.stderr).strip()
