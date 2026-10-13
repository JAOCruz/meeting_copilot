"""Cliente para LM Studio (API compatible con OpenAI) con detección de NO_ACTION en streaming."""
import re
import time
from difflib import SequenceMatcher
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from openai import OpenAI

from config import Config
from transcriber import ME, THEM

NO_ACTION = "NO_ACTION"

SYSTEM_PROMPT = """Eres un copiloto silencioso que asiste al usuario durante una reunión en vivo.
Recibirás un CONTEXTO de la conversación ("Ellos" = otros participantes, "Yo" = el usuario) \
y la FRASE NUEVA, dicha por otro participante.

Decide SOLO sobre la FRASE NUEVA (el contexto sirve para entenderla, no para decidir):
- Si la FRASE NUEVA es una pregunta de trabajo (plazos, cómo se hará algo, opinión, estado, costos, \
decisiones), una petición o una objeción: escribe la RESPUESTA que el usuario diría en voz alta. \
Afirmativa, profesional, directa, máximo 2 oraciones. {missing_data_rule} \
Nunca respondas con otra pregunta. Usa el idioma de la frase. \
Sin preámbulos, comillas ni explicaciones. Los nombres pueden venir mal transcritos; asume que van dirigidos al usuario.
- Si la FRASE NUEVA es charla trivial (clima, saludos, bromas) o no pide nada: responde ÚNICAMENTE NO_ACTION
- Si la FRASE NUEVA repite algo que ya respondiste (ver "Ya sugeriste"): responde NO_ACTION"""

MISSING_DATA_GENERIC = "Si faltan datos, propón una respuesta razonable o un compromiso concreto."
MISSING_DATA_KNOWLEDGE = ("Responde SOLO con lo que diga el CONOCIMIENTO; si no está ahí, la respuesta es "
                          "comprometerse a confirmarlo (con quien indique el CONOCIMIENTO).")

KNOWLEDGE_HEADER = """

=== CONOCIMIENTO DEL PROYECTO ===
"""

KNOWLEDGE_FOOTER = """
=== FIN DEL CONOCIMIENTO ===

Reglas al usar el CONOCIMIENTO (son las más importantes):
1. Si la FRASE NUEVA pregunta algo del sistema o del negocio, responde con los datos exactos del \
CONOCIMIENTO: la ruta de menú completa (A > B > C), el botón, el tipo de comprobante, la regla.
2. NUNCA inventes una ruta de menú, un porcentaje, una tasa, un código de cuenta, un NCF ni una cantidad. \
Si el dato no aparece escrito en el CONOCIMIENTO, NO lo des: responde que lo confirmas (y con quién).
3. Si el CONOCIMIENTO dice que un dato cambia o se mira en una pantalla, responde dónde se mira.
4. Máximo 2 oraciones, en español, sin preámbulos."""

# Ejemplo extra cuando hay conocimiento: no inventar cifras que no están documentadas.
FEW_SHOT_KNOWLEDGE = [
    ("(ninguno)", "¿Y qué porcentaje exacto se le descuenta al empleado por ese concepto?",
     "Ese porcentaje lo actualiza la autoridad y es configurable, así que lo confirmo en la configuración "
     "vigente y te lo paso hoy."),
]


def load_knowledge(path: str, max_chars: int) -> str:
    """Lee los .md/.txt de `path` (archivo o carpeta) en orden alfabético, hasta `max_chars`."""
    root = Path(path)
    if not root.is_absolute():
        root = Path(__file__).parent / root
    files = sorted(root.glob("**/*")) if root.is_dir() else [root]
    parts = [f.read_text(encoding="utf-8") for f in files
             if f.is_file() and f.suffix.lower() in {".md", ".txt"}]
    text = "\n\n".join(p.strip() for p in parts if p.strip())
    if len(text) > max_chars:
        print(f"[llm] Aviso: el conocimiento ({len(text)} caracteres) se recorta a {max_chars}.")
        text = text[:max_chars]
    return text

# Ejemplos como turnos de chat: los modelos de 7B los imitan mucho mejor que si van dentro del prompt.
FEW_SHOT = [
    ("(ninguno)", "¿Cuándo podrías enviarnos la propuesta con los costos?",
     "Se la envío mañana antes del mediodía, con el desglose por fase."),
    (f"{THEM}: Buenos días a todos.\n{ME}: Buenos días.\n{THEM}: Qué frío hace en esta oficina.",
     "Oye, ¿cómo vas a manejar los reintentos si falla un pago?",
     "Usaremos reintentos con backoff exponencial y claves de idempotencia para evitar cobros dobles."),
    (f"{THEM}: ¿Cuál es el estado del módulo de reportes?\n{ME}: Está casi listo, falta probarlo.",
     "Perfecto. Oye, y el tráfico hoy estaba horrible.", NO_ACTION),
    ("(ninguno)", "Creo que el cronograma es demasiado agresivo.",
     "Entiendo; podemos priorizar lo crítico para la primera entrega y mover el resto a una segunda fase."),
    ("(ninguno)", "Hola, ¿me escuchan bien?", NO_ACTION),
]


NOTE_MESSAGE = """CONTEXTO (lo que se está hablando ahora en la reunión):
{context}

MODO COMENTARIO: lo último que se dijo fue «{newest}».
No escribas una respuesta para decir en voz alta. Escribe un COMENTARIO para que el usuario lo lea \
mientras sigue la conversación: el dato exacto del CONOCIMIENTO que más se relaciona con lo que se habla \
(ruta de menú, regla, tipo de comprobante, cuenta, impuesto, a quién preguntar), una advertencia, o algo \
que conviene mencionar. Máximo 2 oraciones. Escribe siempre el comentario (aquí no existe NO_ACTION).{avoid}"""

SECTION_MESSAGE = """MODO PANTALLA: el usuario acaba de abrir en Odoo la pantalla «{title}» (ruta {path}).
No es una pregunta. Escribe una NOTA para que el usuario la lea: para qué sirve esta pantalla y el dato, \
regla o cuidado más útil que el CONOCIMIENTO tenga sobre ella (ruta de menú, impuesto, comprobante, \
quién decide). Máximo 2 oraciones. Si el CONOCIMIENTO no dice nada útil sobre esta pantalla, responde NO_ACTION."""

# Ejemplos del modo nota (fijos: forman parte del prefijo que LM Studio reutiliza).
FEW_SHOT_NOTE = [
    ("Ellos: Hay que revisar bien los datos del cliente antes de facturar.",
     "Sí, sobre todo el número de identificación fiscal.",
     "Dato útil: sin el identificador fiscal correcto el comprobante no sirve para crédito fiscal, "
     "así que conviene validarlo al crear el contacto."),
    ("(ninguno)", "Cuando confirmen la factura ya queda registrada en contabilidad.",
     "Menciona que al confirmar se genera el asiento y el número fiscal, y que ya no se edita: "
     "si hay un error se corrige con una nota de crédito."),
    ("(ninguno)", "Aquí está la lista de proveedores con sus datos.",
     "Buen momento para recordar que las retenciones (ISR/ITBIS) se marcan en la factura del proveedor "
     "y alimentan el reporte de compras."),
]

UNVERIFIED_MARK = re.compile(r"\s*\(⚠ sin verificar:[^)]*\)")

# Solo se piden notas cuando el usuario habla de estos temas (evita llamar al LLM por cada frase).
# El modo nota no admite NO_ACTION: este filtro es quien decide cuándo comentar.
NOTE_TOPICS = re.compile(
    r"factur|fatur|electr[oó]nic|e-?cf|contab|asiento|diario|ncf|comprobante|itbis|impuesto|retenc|isr|dgii|60[678]|nota de cr[eé]dito|"
    r"cuenta|pago|cobr|proveedor|cliente|banco|concilia|extracto|n[oó]mina|quincena|tss|afp|sfs|"
    r"documento|secuencia|tercero|rnc|registr|valid|serie|"
    r"balance|estado de resultados|anal[ií]tic|plan de cuentas|t[eé]rmino|venc|anular|cancelar|borrar",
    re.IGNORECASE)


def build_user_message(context: str, newest: str, previous: str = "") -> str:
    msg = f"CONTEXTO:\n{context}\n\nFRASE NUEVA: {newest}"
    if previous:
        msg += f"\n\nYa sugeriste: {previous}"
    return msg


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.llm_base_url, api_key="lm-studio")
        knowledge = load_knowledge(cfg.context_path, cfg.context_max_chars)
        self.knowledge = knowledge
        # El conocimiento va al principio y nunca cambia: LM Studio reutiliza ese prefijo ya procesado
        # en cada llamada, así que solo la primera paga el costo de leerlo.
        if knowledge:
            self.system_prompt = (SYSTEM_PROMPT.format(missing_data_rule=MISSING_DATA_KNOWLEDGE)
                                  + KNOWLEDGE_HEADER + knowledge + KNOWLEDGE_FOOTER)
            self.few_shot = FEW_SHOT + FEW_SHOT_KNOWLEDGE
        else:
            self.system_prompt = SYSTEM_PROMPT.format(missing_data_rule=MISSING_DATA_GENERIC)
            self.few_shot = FEW_SHOT
        if knowledge:
            print(f"[llm] Conocimiento cargado: {len(knowledge):,} caracteres (~{len(knowledge) // 4:,} tokens)")

    def check_connection(self) -> None:
        models = [m.id for m in self.client.models.list().data]
        print(f"[llm] LM Studio OK. Modelos cargados: {models}")
        if self.cfg.llm_model not in models:
            print(f"[llm] Aviso: '{self.cfg.llm_model}' no aparece en la lista; "
                  "usa COPILOT_LLM_MODEL con uno de los anteriores.")

    def _stream(self, user_message: str) -> Iterable[str]:
        messages = [{"role": "system", "content": self.system_prompt}]
        for ex_context, ex_newest, ex_answer in self.few_shot:
            messages.append({"role": "user", "content": build_user_message(ex_context, ex_newest)})
            messages.append({"role": "assistant", "content": ex_answer})
        if self.knowledge:
            for ex_context, ex_said, ex_note in FEW_SHOT_NOTE:
                example = NOTE_MESSAGE.format(context=f"{ex_context}\nYo: {ex_said}", newest=ex_said, avoid="")
                messages.append({"role": "user", "content": example})
                messages.append({"role": "assistant", "content": ex_note})
        messages.append({"role": "user", "content": user_message})
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

    def suggest(self, lines: list[tuple[str, str]], previous: str, on_start: Callable[[], None],
                on_token: Callable[[str], None]) -> str | None:
        """Respuesta para decir en voz alta a la última frase de los otros (`lines` = [(quién, texto)]).
        Devuelve None si el modelo respondió NO_ACTION."""
        last_them = max((i for i, (speaker, _) in enumerate(lines) if speaker == THEM), default=None)
        if last_them is None:
            return None
        context = "\n".join(f"{speaker}: {text}" for speaker, text in lines[:last_them]) or "(ninguno)"
        newest = lines[last_them][1]
        message = build_user_message(context, newest, previous)
        if "?" in newest or "¿" in newest:
            # Una pregunta explícita siempre merece propuesta: el 7B tiende a NO_ACTION con mucho contexto.
            message += "\n\nLa FRASE NUEVA es una pregunta: responde siempre (aquí no uses NO_ACTION)."
        return self._complete(message, on_start, on_token)

    def note(self, lines: list[tuple[str, str]], previous: str, on_start: Callable[[], None],
             on_token: Callable[[str], None]) -> str | None:
        """Comentario para que el usuario lo lea sobre lo que se está hablando (diga quien lo diga).
        Solo con conocimiento cargado y cuando las últimas frases tocan temas que el conocimiento cubre."""
        window = lines[-6:]
        if not self.knowledge or not window or not any(NOTE_TOPICS.search(t) for _, t in window[-2:]):
            return None
        context = "\n".join(f"{speaker}: {text}" for speaker, text in window)
        previous = UNVERIFIED_MARK.sub("", previous).strip()
        avoid = f"\nYa comentaste esto, di algo distinto: «{previous}»" if previous else ""
        message = NOTE_MESSAGE.format(context=context, newest=window[-1][1], avoid=avoid)
        # Se genera sin mostrar para poder descartar repeticiones (el 7B copia el comentario anterior).
        held: list[str] = []
        text = self._complete(message, lambda: None, held.append)
        if not text or (previous and SequenceMatcher(None, UNVERIFIED_MARK.sub("", text), previous).ratio() > 0.7):
            return None
        on_start()
        on_token(text)
        return text

    def section(self, title: str, path: str, on_start: Callable[[], None],
                on_token: Callable[[str], None]) -> str | None:
        """Nota sobre la pantalla de Odoo que el usuario acaba de abrir (solo con conocimiento)."""
        if not self.knowledge:
            return None
        return self._complete(SECTION_MESSAGE.format(title=title, path=path), on_start, on_token)

    def warm_up(self) -> None:
        """Procesa el prompt del sistema una vez para que la primera pregunta real sea rápida."""
        t0 = time.time()
        for _ in self._stream(build_user_message("(ninguno)", "Hola a todos.")):
            pass
        print(f"[llm] Prompt precalentado en {time.time() - t0:.1f}s")

    def _complete(self, user_message: str, on_start: Callable[[], None],
                  on_token: Callable[[str], None]) -> str | None:
        """Retiene los primeros tokens hasta saber si es NO_ACTION, para que el HUD no parpadee."""
        tokens = filter_no_action(self._stream(user_message))
        if self.knowledge:
            # Con conocimiento se verifica la respuesta completa antes de mostrarla: un 7B inventa
            # porcentajes y rutas de menú aunque el prompt lo prohíba. Se muestra igual, marcada.
            text = "".join(tokens).strip()
            if not text:
                return None
            problem = unsupported_claim(text, self.knowledge)
            if problem:
                print(f"[llm] Sin verificar (no está en el conocimiento: {problem}): {text}")
                text = f"{text}  (⚠ sin verificar: {problem})"
            on_start()
            on_token(text)
            return text

        text = ""
        started = False
        for token in tokens:
            if not started:
                on_start()
                started = True
            text += token
            on_token(token)
        return text.strip() or None


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos y con espacios simples: el conocimiento a veces viene sin tildes."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.replace("›", ">"))


def _number_supported(number: str, concepts: set[str], sentences: list[str]) -> bool:
    """La cifra debe aparecer en una oración del conocimiento que hable del mismo concepto (SFS, ITBIS…)."""
    pattern = re.compile(r"(?<![\w.,])" + re.escape(number.rstrip("%").strip())
                         + (r"\s*%" if number.endswith("%") else r"(?![\w])"))
    candidates = [sent for sent in sentences if pattern.search(sent)]
    if concepts:
        return any(any(c in sent for c in concepts) for sent in candidates)
    return bool(candidates)


def _path_supported(steps: list[str], kb: str) -> bool:
    """La ruta debe existir en el conocimiento empezando en su raíz (no a mitad de otra ruta).
    Se toleran palabras sobrantes antes del primer tramo ("mira en Contabilidad") y después del último."""
    first, last = steps[0].split(), steps[-1].split()
    for i in range(len(first)):
        for j in range(len(last), 0, -1):
            candidate = " > ".join([" ".join(first[i:]), *steps[1:-1], " ".join(last[:j])])
            if re.search(r"(?<!> )(?<![\w])" + re.escape(candidate), kb):
                return True
    return False


def unsupported_claim(answer: str, knowledge: str) -> str | None:
    """Devuelve el primer dato de `answer` que el conocimiento no respalda (cifra o ruta de menú), o None."""
    kb = _normalize(knowledge)
    ans = _normalize(answer)
    sentences = re.split(r"(?<=[.!?])\s+|\n", kb)
    concepts = {c.lower() for c in re.findall(r"\b[A-Z]{2,}\b", answer)} & set(re.findall(r"\w+", kb))
    for number in re.findall(r"(?<![\w.,])\d+(?:[.,]\d+)?(?:\s*%)?", ans):
        if not _number_supported(number.strip(), concepts, sentences):
            return f"«{number.strip()}»"
    for code in re.findall(r"\b[be]\d{2}\b", ans):  # tipos de comprobante: B01, B04, E31…
        if not re.search(rf"\b{code}\b", kb):
            return f"«{code.upper()}»"
    for path in re.findall(r"[^.;:,()>]+(?:>[^.;:,()>]+)+", ans):
        steps = [step.strip(" «»\"'") for step in path.split(">")]
        if all(steps) and not _path_supported(steps, kb):
            return f"«{' > '.join(steps[-2:])}»"
    return None


def filter_no_action(tokens: Iterable[str]) -> Iterable[str]:
    """Pasa los tokens solo si la respuesta NO empieza con NO_ACTION."""
    held = ""
    deciding = True
    for token in tokens:
        if not deciding:
            yield token
            continue
        held += token
        probe = held.lstrip().strip("\"'`*").upper()  # el modelo a veces escribe "No_ACTION"
        if probe.startswith(NO_ACTION):
            return  # descartamos el resto
        if NO_ACTION.startswith(probe):
            continue  # todavía puede ser NO_ACTION; seguimos reteniendo
        deciding = False
        yield held.lstrip()
