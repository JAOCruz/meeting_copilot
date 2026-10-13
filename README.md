# Meeting Copilot (HUD local para macOS)

Escucha el audio de una llamada (Zoom/Meet/Teams), lo transcribe con `faster-whisper`,
le pide a un LLM local en LM Studio una sugerencia cuando alguien te hace una pregunta,
y la muestra en una ventana flotante siempre visible. Todo corre local.

```
BlackHole 2ch → audio_capturer.py → transcriber.py (Whisper + búfer) → llm_client.py (LM Studio) → hud_gui.py
```

| Archivo | Función |
|---|---|
| `config.py` | Parámetros (modelo, umbrales, URL de LM Studio). Sobrescribibles por env vars. |
| `audio_capturer.py` | Captura del dispositivo, mezcla a mono y remuestrea a 16 kHz. |
| `transcriber.py` | Corta frases por silencio, transcribe y guarda las últimas N en un búfer circular. |
| `llm_client.py` | Prompt del sistema, streaming y filtro de `NO_ACTION`. |
| `hud_gui.py` | Ventana PyQt6 sin bordes, semitransparente, arrastrable, siempre encima. |
| `main.py` | Orquesta los hilos y conecta todo al HUD. |

## 1. Instalar BlackHole y enrutar el audio

```bash
brew install blackhole-2ch     # pide reiniciar el audio / la Mac
```

Para **escuchar la llamada tú y a la vez enviarla a BlackHole**:

1. Abre **Configuración de Audio MIDI** (`/Applications/Utilities/Audio MIDI Setup.app`).
2. Botón **+** → **Crear dispositivo de salida múltiple**.
3. Marca **tus audífonos/bocinas** y **BlackHole 2ch**. Pon tus audífonos como *dispositivo principal*
   y activa *Corrección de deriva* en BlackHole.
4. En Zoom/Meet/Teams (o en Ajustes del Sistema → Sonido → Salida) elige ese **Dispositivo de salida múltiple** como altavoz.
   Tu micrófono sigue siendo el normal.

Así el copiloto solo oye a los demás participantes, no a ti — justo lo que necesita para detectar preguntas dirigidas a ti.

> Nota: con el dispositivo de salida múltiple, el volumen del Mac no se controla con las teclas; ajústalo en la app de la llamada.

## 2. LM Studio

1. Descarga y carga `qwen2.5-7b-instruct` (o `qwen2.5-coder-7b-instruct`).
2. Pestaña **Developer** → **Start Server** (puerto 1234).
3. Verifica: `curl http://localhost:1234/v1/models`

El `id` que aparezca ahí es el que va en `COPILOT_LLM_MODEL` (por defecto `qwen2.5-coder-7b-instruct`).

## 3. Instalar y ejecutar

```bash
cd meeting_copilot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py --list-devices     # confirma que aparece "BlackHole 2ch"
python main.py                    # usa BlackHole, Whisper "small", español
```

La primera vez descarga el modelo de Whisper (~480 MB para `small`).

Opciones:

```bash
python main.py --model base           # más rápido, menos preciso
python main.py --language ""          # detección automática de idioma
python main.py --device 3             # por índice
COPILOT_LLM_MODEL=qwen2.5-7b-instruct python main.py
COPILOT_SILENCE_RMS=0.004 python main.py   # si el audio entra bajo y no detecta frases
```

En el HUD: **arrastra** para moverlo, **doble clic** para cerrarlo. `Ctrl+C` en la terminal también lo cierra.

Antes de una llamada real, prueba poniendo un video de YouTube con alguien haciendo preguntas
y la salida en el dispositivo múltiple.

## Ajuste fino

- **Latencia**: `faster-whisper` corre en CPU en Apple Silicon (CTranslate2 no usa Metal); con `int8`
  el modelo `small` transcribe una frase en ~0.5–1.5 s en un M3. Si va atrasado, usa `base`.
- **Frases cortadas o pegadas**: ajusta `silence_duration` y `max_segment_seconds` en `config.py`.
- **Demasiadas/pocas sugerencias**: edita `SYSTEM_PROMPT` en `llm_client.py`.
