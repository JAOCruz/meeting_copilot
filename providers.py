"""Proveedores del modelo y proyectos de conocimiento.

Todos los proveedores se usan con el cliente de OpenAI: LM Studio y Claude (api.anthropic.com/v1/) aceptan ese
formato, igual que OpenAI, OpenRouter o Groq. Las API keys se guardan en el Llavero de macOS (nunca en
settings.json ni en el repo); una variable de entorno (ANTHROPIC_API_KEY, OPENAI_API_KEY) tiene prioridad.

Cada proyecto es un archivo (o carpeta) dentro de la carpeta de conocimiento: knowledge/transport.md,
knowledge/arcova.md… El proyecto activo decide qué conocimiento se carga y, si así se configuró, con qué
proveedor (p. ej. transport siempre en local por privacidad).
"""
import os
from dataclasses import dataclass
from pathlib import Path

from config import Config

ROOT = Path(__file__).parent
KEYCHAIN_SERVICE = "meeting-copilot"


@dataclass(frozen=True)
class Provider:
    label: str
    local: bool
    needs_key: bool
    env_key: str = ""
    models: tuple[str, ...] = ()


PROVIDERS = {
    "lmstudio": Provider("LM Studio (local)", local=True, needs_key=False),
    "claude": Provider("Claude (Anthropic)", local=False, needs_key=True, env_key="ANTHROPIC_API_KEY",
                       models=("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")),
    "openai": Provider("Otro compatible con OpenAI", local=False, needs_key=True, env_key="OPENAI_API_KEY"),
}
CLAUDE_URL = "https://api.anthropic.com/v1/"


@dataclass(frozen=True)
class Connection:
    provider: str
    base_url: str
    model: str
    api_key: str

    @property
    def info(self) -> Provider:
        return PROVIDERS[self.provider]

    @property
    def label(self) -> str:
        """Texto corto para el HUD: modelo y, si sale del Mac, «(nube)»."""
        name = self.model.split("/")[-1]
        return name if self.info.local else f"{name} (nube)"


def provider_for(cfg: Config, project: str | None = None) -> str:
    """Proveedor del proyecto (si tiene uno propio) o el predeterminado."""
    chosen = cfg.project_providers.get(project if project is not None else cfg.knowledge_project, "")
    return chosen if chosen in PROVIDERS else (cfg.llm_provider if cfg.llm_provider in PROVIDERS else "lmstudio")


def connection(cfg: Config) -> Connection:
    provider = provider_for(cfg)
    if provider == "claude":
        return Connection(provider, CLAUDE_URL, cfg.claude_model, get_key(provider))
    if provider == "openai":
        return Connection(provider, cfg.custom_base_url, cfg.custom_model, get_key(provider))
    return Connection(provider, cfg.llm_base_url, cfg.llm_model, "lm-studio")


# --- API keys (Llavero de macOS) ---
def get_key(provider: str) -> str:
    env = PROVIDERS[provider].env_key
    if env and os.getenv(env):
        return os.environ[env]
    try:
        import keyring
        return keyring.get_password(KEYCHAIN_SERVICE, provider) or ""
    except Exception as exc:  # sin keyring o Llavero bloqueado: se trata como «sin key»
        print(f"[llm] No se pudo leer la API key del Llavero: {exc}")
        return ""


def set_key(provider: str, key: str) -> None:
    import keyring
    if key:
        keyring.set_password(KEYCHAIN_SERVICE, provider, key)
    else:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, provider)
        except keyring.errors.PasswordDeleteError:
            pass


# --- Proyectos de conocimiento ---
def _root(context_path: str) -> Path:
    root = Path(context_path)
    return root if root.is_absolute() else ROOT / root


def list_projects(context_path: str) -> list[str]:
    """Proyectos disponibles: cada .md/.txt o subcarpeta de la carpeta de conocimiento."""
    root = _root(context_path)
    if not root.is_dir():
        return []
    names = {p.stem for p in root.iterdir()
             if not p.name.startswith(".") and (p.is_dir() or p.suffix.lower() in {".md", ".txt"})}
    return sorted(names)


def knowledge_source(context_path: str, project: str) -> Path:
    """Qué cargar: el archivo/carpeta del proyecto dentro de la carpeta de conocimiento, o `context_path` tal
    cual si apunta a un archivo. Un proyecto que no existe no carga nada (nunca la carpeta entera: mezclaría
    el conocimiento de varios proyectos)."""
    root = _root(context_path)
    if not root.is_dir():
        return root
    for candidate in (root / f"{project}.md", root / f"{project}.txt", root / project):
        if project and candidate.exists():
            return candidate
    print(f"[llm] No hay conocimiento para el proyecto «{project}» en {root.name}/")
    return root / f"{project or '_'}.md"
