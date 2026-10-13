# Meeting Copilot (HUD local para macOS)

Escucha la llamada (Zoom/Meet/Teams en Chrome o en app) y tu micrófono, transcribe ambos con
`faster-whisper`, le pide a un LLM local en LM Studio una sugerencia cuando alguien te hace una
pregunta, y la muestra en una ventana flotante visible en todos los escritorios y sobre apps en
pantalla completa. Todo corre local.

```
llamada → BlackHole ─┐
                     ├→ transcriber.py (Whisper, "Ellos"/"Yo") → llm_client.py (LM Studio) → hud_gui.py
tu mic ──────────────┘
```

| Archivo | Función |
|---|---|
| `config.py` | Parámetros (modelo, umbrales, URL de LM Studio). Sobrescribibles por env vars. |
| `audio_capturer.py` | Captura de un dispositivo, mezcla a mono y remuestrea a 16 kHz. Se enciende/apaga. |
| `audio_route.py` + `tools/audio_route.swift` | Crea el dispositivo de salida múltiple (tu salida + BlackHole) y lo activa/desactiva. |
| `transcriber.py` | Corta frases por silencio en cada fuente, transcribe, descarta el eco del mic y guarda las últimas N. |
| `llm_client.py` | Prompt del sistema, ejemplos, streaming y filtro de `NO_ACTION`. |
| `hud_gui.py` | HUD PyQt6 con controles + icono en la barra de menú. |
| `main.py` | Orquesta los hilos y conecta todo al HUD. |

## 1. BlackHole

```bash
brew install blackhole-2ch
```

No hace falta configurar nada más: al encender **🎧 Llamada**, el copiloto crea un dispositivo
"Copilot (salida + BlackHole)" con tu salida actual (audífonos o bocinas) y lo pone como salida del
sistema. Sigues oyendo la llamada y a la vez llega al copiloto. Al apagar el botón o salir, vuelve a tu
salida normal. También puedes manejarlo a mano:

```bash
swift tools/audio_route.swift on|off   # sin argumento muestra el estado
```

Si Zoom/Meet tienen elegido un altavoz concreto en sus ajustes, ponlo en "Igual que el sistema".
Mientras está activo, el volumen se ajusta en la app de la llamada, no con las teclas.

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
python main.py                    # llamada + tu mic, Whisper "small", español
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

### Controles

En el HUD (arrástralo para moverlo; la esquina inferior derecha lo agranda) y en el icono verde de la
barra de menú. En el HUD los interruptores muestran solo el ícono: **verde = encendido** (el nombre
aparece al pasar el ratón).

| Botón | Qué hace |
|---|---|
| 🎧 Llamada | Escucha la llamada. Activa/desactiva también la ruta de audio por BlackHole. |
| 🎙 Mi mic | Escucha tu micrófono (aparece como **Yo**). Al apagarlo se apaga el indicador naranja de macOS. |
| ✨ Copiloto | Pausa/reanuda la IA (la transcripción sigue). |
| 📍 Pantalla | Notas sobre la pantalla de Odoo abierta en Chrome. |
| ⚙ | Ventana de **Configuración**. |
| 🧹 | Archiva la sesión (`sessions/<fecha>.json`) y empieza una nueva. |
| ✕ | Oculta el HUD; vuelve desde el icono de la barra de menú. Para salir: menú → «Salir del copiloto». |

El historial (frases, 💬 respuestas, 📝/📍 notas) se guarda en `sessions/current.json` y sobrevive a
los reinicios. Debajo hay un campo **Pregunta rápida**; también se pueden mandar preguntas añadiendo
líneas a `inbox.txt` (`/config` abre la Configuración).

### Configuración (⚙)

| Pestaña | Qué se ajusta |
|---|---|
| Audio | Dispositivo de la llamada y micrófono, ruta automática por BlackHole, sensibilidad |
| Transcripción | Modelo Whisper, idioma, pausa que cierra una frase |
| IA | Servidor y modelo de LM Studio (lista los cargados), creatividad, intervalos, qué genera (💬 📝 📍) |
| Conocimiento | Carpeta de conocimiento y **Actualizar desde aiviber** (exporta un proyecto vía epona) |
| HUD | Opacidad, tamaño de las respuestas, ancho |
| Sesiones | Reuniones guardadas, exportar a Markdown, nueva sesión |

Se guarda en `settings.json` (ignorado por git), que tiene prioridad sobre `config.py` y las variables
de entorno; las opciones de línea de comandos tienen prioridad sobre todo. Los ajustes marcados con ↻
se aplican al reiniciar («Guardar y reiniciar»); el resto, al momento.

**Modelo recomendado** en un Mac con 32 GB o más: `Qwen3-30B-A3B-Instruct-2507` (Q4_K_M, ~18 GB):
MoE con ~3B parámetros activos, casi tan rápido como un 7B y mucho más fiel al conocimiento. Si un
modelo "piensa" (`<think>…</think>`), ese bloque se oculta automáticamente.

Opciones de arranque (tienen prioridad sobre la Configuración): `--no-mic`, `--mic "AirPods"`,
`--no-route` (si ya tienes tu propio dispositivo múltiple), `--model base`, `--language en`.

**Usa audífonos si puedes.** Con bocinas, el mic capta la voz de los demás; el copiloto descarta ese
eco cuando lo reconoce, pero con audífonos la transcripción de "Yo" queda limpia.

Antes de una llamada real, prueba poniendo un video de YouTube con alguien haciendo preguntas
y la salida en el dispositivo múltiple.

## Ajuste fino

- **Latencia**: `faster-whisper` corre en CPU en Apple Silicon (CTranslate2 no usa Metal); con `int8`
  el modelo `small` transcribe una frase en ~0.5–1.5 s en un M3. Si va atrasado, usa `base`.
- **Frases cortadas o pegadas**: ajusta `silence_duration` y `max_segment_seconds` en `config.py`.
- **Demasiadas/pocas sugerencias**: edita `SYSTEM_PROMPT` en `llm_client.py`.

## Problemas comunes

**El HUD dice "Sin audio" o no transcribe nada aunque suena la llamada.**
macOS trata cualquier entrada de audio (incluida BlackHole) como micrófono. Si la app de terminal
desde la que ejecutas el copiloto no tiene ese permiso, recibe **silencio absoluto sin dar ningún error**.

1. Ajustes del Sistema → **Privacidad y seguridad → Micrófono** (no "Cámara" ni "Grabación de pantalla")
   → activa tu terminal (Terminal, iTerm, Hyper, VS Code…).
2. Cierra la terminal por completo (Cmd+Q) y vuelve a abrirla: el permiso solo aplica a procesos nuevos.
3. Si tu terminal no aparece en la lista, ejecuta el copiloto desde **Terminal.app**, que sí muestra el aviso.

Prueba rápida: `python -c "import sounddevice as sd, numpy as np; r=sd.rec(48000*2, 48000, 1); sd.wait(); print(np.abs(r).max())"`
mientras hablas. Si imprime `0.0`, el problema es el permiso.
