"""Cliente para LM Studio (API compatible con OpenAI) con detección de NO_ACTION en streaming."""
from typing import Callable, Iterable

from openai import OpenAI

from config import Config

NO_ACTION = "NO_ACTION"

SYSTEM_PROMPT = f"""Eres un copiloto silencioso que asiste al usuario durante una reunión en vivo.
Recibirás la transcripción reciente de lo que dicen los OTROS participantes (el usuario no aparece en ella).

Reglas:
1. Analiza el búfer de transcripción, dando prioridad a las últimas líneas.
2. ACTÚA si en las últimas líneas hay: una pregunta de trabajo (plazos, cómo se hará algo, opinión, \
estado, costos, decisiones), una petición, o una objeción. Toda pregunta de trabajo cuenta como dirigida \
al usuario. Escribe la RESPUESTA que el usuario diría en voz alta: afirmativa, profesional, directa y \
ultra-concisa (máximo 2 oraciones). Si faltan datos, propón una respuesta razonable o un compromiso concreto; \
nunca respondas con otra pregunta. Usa el idioma de la conversación. Sin preámbulos, comillas ni explicaciones.
3. Responde ÚNICAMENTE {NO_ACTION} si es charla trivial (clima, saludos, bromas), logística sin pregunta, \
o si la pregunta ya fue respondida en la transcripción.
4. No repitas una sugerencia que ya diste.

Ejemplos:
Transcripción: - Oye, ¿para cuándo tendrías lista la integración de pagos?
Respuesta: Calculo tenerla lista para el viernes; el miércoles les comparto un avance para validar.

Transcripción: - ¿Cómo vas a manejar los reintentos si falla el pago?
Respuesta: Usaremos reintentos con backoff exponencial y claves de idempotencia para evitar cobros dobles.

Transcripción: - Qué calor hace hoy. - Sí, y el tráfico estaba horrible.
Respuesta: {NO_ACTION}"""


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
        user_msg = f"Transcripción reciente:\n{transcript}"
        if previous:
            user_msg += f"\n\nÚltima sugerencia que ya mostraste: {previous}"
        stream = self.client.chat.completions.create(
            model=self.cfg.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
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
