"""Configuración central. Todo se puede sobrescribir con variables de entorno."""
import os
from dataclasses import dataclass, field


def _env(name: str, default):
    value = os.getenv(name)
    if value is None:
        return default
    return type(default)(value)


@dataclass
class Config:
    # --- Audio ---
    audio_device: str = field(default_factory=lambda: _env("COPILOT_AUDIO_DEVICE", "BlackHole"))
    mic_device: str = field(default_factory=lambda: _env("COPILOT_MIC_DEVICE", ""))  # "" = entrada por defecto
    mic_enabled_at_start: bool = True
    target_sample_rate: int = 16_000          # Whisper trabaja a 16 kHz mono

    # --- Segmentación (VAD por energía) ---
    silence_threshold: float = field(default_factory=lambda: _env("COPILOT_SILENCE_RMS", 0.008))
    silence_duration: float = 0.7             # seg. de silencio que cierran una frase
    max_segment_seconds: float = 12.0         # corta frases muy largas
    min_segment_seconds: float = 0.6          # ignora ruidos cortos

    # --- Whisper ---
    whisper_model: str = field(default_factory=lambda: _env("COPILOT_WHISPER_MODEL", "small"))
    whisper_language: str = field(default_factory=lambda: _env("COPILOT_LANGUAGE", "es"))  # "" = auto
    whisper_compute_type: str = "int8"        # CTranslate2 en Apple Silicon corre en CPU; int8 es lo más rápido

    # --- Búfer de transcripción ---
    buffer_max_sentences: int = 12

    # --- LLM (LM Studio) ---
    llm_base_url: str = field(default_factory=lambda: _env("COPILOT_LLM_URL", "http://localhost:1234/v1"))
    llm_model: str = field(default_factory=lambda: _env("COPILOT_LLM_MODEL", "qwen2.5-coder-7b-instruct"))
    llm_temperature: float = 0.2
    llm_max_tokens: int = 120
    llm_cooldown_seconds: float = 2.0         # mínimo entre llamadas al LLM
    # Conocimiento del proyecto (.md/.txt) que se incluye en el prompt; ver tools/export_epona_context.py
    context_path: str = field(default_factory=lambda: _env("COPILOT_CONTEXT", "knowledge"))
    context_max_chars: int = 90_000           # ~22k tokens; el modelo tiene 32k cargados

    # --- HUD ---
    hud_width: int = 460
    hud_opacity: float = 0.97
    hud_font_size: int = 15
