"""Cliente para LM Studio (API compatible con OpenAI) con detección de NO_ACTION en streaming."""
from typing import Callable, Iterable

from openai import OpenAI

from config import Config

NO_ACTION = "NO_ACTION"

SYSTEM_PROMPT = f"""Eres un copiloto silencioso que asiste al usuario durante una reunión en vivo.
Recibirás lo que dicen los OTROS participantes (el usuario no aparece): un CONTEXTO previo y la FRASE NUEVA.

Decide SOLO sobre la FRASE NUEVA (el contexto sirve para entenderla, no para decidir):
- Si la FRASE NUEVA es una pregunta de trabajo (plazos, cómo se hará algo, opinión, estado, costos, \
decisiones), una petición o una objeción: escribe la RESPUESTA que el usuario diría en voz alta. \
Afirmativa, profesional, directa, máximo 2 oraciones. Si faltan datos, propón una respuesta razonable \
o un compromiso concreto; nunca respondas con otra pregunta. Usa el idioma de la frase. \
Sin preámbulos, comillas ni explicaciones. Los nombres pueden venir mal transcritos; asume que van dirigidos al usuario.
- Si la FRASE NUEVA es charla trivial (clima, saludos, bromas) o no pide nada: responde ÚNICAMENTE {NO_ACTION}
- Si la FRASE NUEVA repite algo que ya respondiste (ver "Ya sugeriste"): responde {NO_ACTION}"""

# Ejemplos como turnos de chat: los modelos de 7B los imitan mucho mejor que si van dentro del prompt.
FEW_SHOT = [
    ("(ninguno)", "¿Cuándo podrías enviarnos la propuesta con los costos?",
     "Se la envío mañana antes del mediodía, con el desglose por fase."),
    ("- Buenos días a todos.\n- Qué frío hace en esta oficina.",
     "Oye, ¿cómo vas a manejar los reintentos si falla un pago?",
     "Usaremos reintentos con backoff exponencial y claves de idempotencia para evitar cobros dobles."),
    ("- ¿Cuál es el estado del módulo de reportes?", "Sí, y el tráfico hoy estaba horrible.", NO_ACTION),
    ("(ninguno)", "Creo que el cronograma es demasiado agresivo.",
     "Entiendo; podemos priorizar lo crítico para la primera entrega y mover el resto a una segunda fase."),
    ("(ninguno)", "Hola, ¿me escuchan bien?", NO_ACTION),
]


def build_user_message(context: str, newest: str, previous: str = "") -> str:
    msg = f"CONTEXTO:\n{context}\n\nFRASE NUEVA: {newest}"
    if previous:
        msg += f"\n\nYa sugeriste: {previous}"
    return msg


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.llm_base_url, api_key="lm-studio")

    def check_connection(self) -> None:
        models = [m.id for m in self.client.models.list().data]
        print(f"[llm] LM Studio OK. Modelos cargados: {models}")
        if self.cfg.llm_model not in models:
            print(f"[llm] Aviso: '{self.cfg.llm_model}' no aparece en la lista; "
                  "usa COPILOT_LLM_MODEL con uno de los anteriores.")

    def _stream(self, transcript: str, previous: str) -> Iterable[str]:
        lines = transcript.splitlines()
        context = "\n".join(lines[:-1]) or "(ninguno)"
        newest = lines[-1].removeprefix("- ") if lines else ""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for ex_context, ex_newest, ex_answer in FEW_SHOT:
            messages.append({"role": "user", "content": build_user_message(ex_context, ex_newest)})
            messages.append({"role": "assistant", "content": ex_answer})
        messages.append({"role": "user", "content": build_user_message(context, newest, previous)})
        stream = self.client.chat.completions.create(
            model=self.cfg.llm_model,
            messages=messages,
            temperature=self.cfg.llm_temperature,
            max_tokens=self.cfg.llm_max_tokens,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def suggest(self, transcript: str, previous: str, on_start: Callable[[], None],
                on_token: Callable[[str], None]) -> str | None:
        """Devuelve la sugerencia completa, o None si el modelo respondió NO_ACTION.

        Retiene los primeros tokens hasta saber si la respuesta es NO_ACTION, para que el HUD
        no parpadee; a partir de ahí reenvía cada token en vivo con `on_token`.
        """
        text = ""
        started = False
        for token in filter_no_action(self._stream(transcript, previous)):
            if not started:
                on_start()
                started = True
            text += token
            on_token(token)
        return text.strip() or None


def filter_no_action(tokens: Iterable[str]) -> Iterable[str]:
    """Pasa los tokens solo si la respuesta NO empieza con NO_ACTION."""
    held = ""
    deciding = True
    for token in tokens:
        if not deciding:
            yield token
            continue
        held += token
        probe = held.lstrip().strip("\"'`*")
        if probe.startswith(NO_ACTION):
            return  # descartamos el resto
        if NO_ACTION.startswith(probe):
            continue  # todavía puede ser NO_ACTION; seguimos reteniendo
        deciding = False
        yield held.lstrip()
