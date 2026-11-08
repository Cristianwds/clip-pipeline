# Instrucciones para trabajar en clip-pipeline

Detecta los mejores momentos de un VOD de stream usando **el chat como señal**, y los
deja como clips verticales subtitulados listos para publicar. Responder en español
rioplatense.

**Si venís a hacer un cambio puntual, buscá el archivo en el mapa de abajo y leé solo
ese.** Los módulos no se conocen entre sí salvo por lo que dice el grafo.

## Entorno

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q          # 81 tests, corren en ~1,5 s
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --top 5 --cortar 3 \
    --formato dividido --facecam 4,268,320,188 --subtitulos --streamer NOMBRE
```

Binarios del sistema, no son dependencias pip: `ffmpeg`, `ffprobe`, `yt-dlp` y
`TwitchDownloaderCLI` (este último en `~/.local/opt/twitchdownloader/`, ver README).

Los tests **no bajan nada ni tocan la red**: todo lo que se testea son funciones puras
con datos sintéticos. Si un cambio necesita video o audio real para probarse, es señal
de que la lógica está mezclada con la E/S y conviene separarla.

## Mapa de archivos

| Archivo | Qué hace | Tocalo si querés cambiar |
| :--- | :--- | :--- |
| `texto.py` | Tokenizar, sacar tildes, listas de conectores y palabras funcionales | Cómo se normaliza el texto, qué palabras cuentan como "de relleno" |
| `chat.py` | Baja el chat del VOD y lo cachea en JSONL propio | Soportar otra plataforma, cambiar el cache |
| `picos.py` | Detecta los picos de actividad y los **rankea** | El umbral, la ventana, la fórmula de prioridad |
| `vocabulario.py` | De qué habla el chat en cada pico: tema, reacción, foco, etiqueta | Cómo se separa "el asunto" de "cómo reacciona ese chat" |
| `cortar.py` | Baja solo el tramo de cada pico con `--download-sections` | Calidad del corte, formato de descarga |
| `reencuadre.py` | Pasa de 16:9 a 9:16: `desenfoque`, `recorte`, `dividido` | Layouts, tracking del sujeto, posición de la facecam |
| `subtitulos.py` | Transcribe con faster-whisper y quema los subtítulos en ASS | Modelo de ASR, dónde cortan las líneas, estilo del subtítulo |
| `estilos.py` | Las variantes de formato que rotan entre clips | Agregar una variante, cambiar largos o colores |
| `metadatos.py` | Título, gancho, cita y hashtags desde el chat + el audio | Cómo se arman los textos del post |
| `publicar.py` | Valida cada clip por plataforma y escribe el manifiesto | Límites de plataforma, horarios, payload de Postiz |
| `cuentas.py` | Registro de cuentas y estado de monetización. CLI propia | Agregar una plataforma, actualizar qué paga cada una |
| `cli.py` | Orquesta todo. No tiene lógica propia | El orden de las etapas, flags nuevos |

### Grafo de dependencias

```
texto ──┬── vocabulario ── picos ──┬── cortar
        ├── subtitulos             └── publicar ── metadatos
        └── metadatos
chat ───┴── vocabulario, picos
estilos, reencuadre, cuentas: sin dependencias internas
cli: usa todos, no lo usa nadie
```

`texto.py` es hoja y no importa nada del proyecto. Si dos módulos necesitan lo mismo,
va ahí: antes `metadatos` importaba una función privada de `subtitulos` y eso ataba dos
etapas que no tienen nada que ver.

## Decisiones ya tomadas (no re-litigar sin motivo)

Cada una salió de un problema real, y están explicadas largo en el docstring de su
módulo y en la nota del vault.

- **Anscombe + umbral por Bonferroni** (`picos.py`). Un umbral ingenuo de 3 sigmas
  devolvía 10 "highlights" en un chat sintético donde no pasaba nada. Un chat es un
  proceso de conteo y su ruido crece con `sqrt(n)`.
- **Especificidad contra los otros picos** (`vocabulario.py`). Comparar contra todo el
  stream no alcanza: durante *cualquier* pico sube la proporción de emotes. Un token
  que se eleva en muchos picos es reacción, en uno solo es el asunto.
- **El ranking usa score y foco** (`picos.Pico.prioridad`). El score mide cuánto chat
  hay; el foco, cuánto se ponen de acuerdo. El pico con el score más alto del VOD de
  prueba tenía foco 0.02 y era basura.
- **El borde del final NO se filtra por tiempo** (`picos.detectar`). Filtrar los últimos
  5 minutos mataba la despedida pero también el climax, que estaba 2 min antes. La
  despedida se descarta por etiqueta `cierre`.
- **No hay detección de caras** (`reencuadre.py`). OpenCV 5 sacó las cascadas de Haar y
  `CascadeClassifier`; el detector que queda necesita bajar un ONNX. El sujeto se estima
  por energía de movimiento, que no depende de ningún modelo descargado.
- **La facecam se pasa a mano** (`--facecam x,y,w,h`). Es una constante por streamer y
  detectarla sin modelo es poco confiable. En el VOD de prueba ni siquiera estaba en una
  esquina.
- **Subtítulos en ASS, no SRT** (`subtitulos.a_ass`). Un SRT quemado sale con el estilo
  por defecto de libass: chico, sin contorno, ilegible sobre imagen clara.
- **El gancho se arma DESPUÉS de transcribir** (`cli.py`). Si no, sale con la falta de
  ortografía del chat quemada en pantalla. Por eso `subtitular` está partido en
  `preparar` y `rematar`.
- **La etapa de publicación no publica** (`publicar.py`). Arma el manifiesto y para.
  `paquete_postiz()` devuelve el payload sin hacer ninguna llamada. Es a propósito: son
  clips de contenido de terceros y publicar es una decisión del usuario, no un paso.

## Trampas conocidas

- **`yt-dlp` puede terminar en 0 y no dejar el archivo que pediste.** Elige el contenedor
  y le pega su extensión al nombre: `pico_01.mp4` sale como `pico_01.mp4.webm`. En
  `cortar.py` el éxito se decide mirando el disco, no el returncode.
- **El nombre del clip incluye el largo** (`pico_01_1h48m27s_18s.mp4`). Si no, al cambiar
  de variante se reusa el corte anterior porque "el archivo ya existe".
- **`chat-downloader` está muerto** (última versión de 2022, falla en las dos
  plataformas). No volver a agregarlo.
- **faster-whisper no decodifica bien el audio acá** (incompatibilidad con PyAV). El
  audio se extrae con ffmpeg y se le pasa como array.
- **El chat escribe mal y el audio no.** `metadatos.consolidar` corrige la ortografía
  cruzando los dos, con un límite: 2 ediciones si el chat escribió la palabra de varias
  formas, 1 sola si fue consistente. Sin ese límite convertía `malas` en `mata`.

## Reglas

- **Nada de claves en el repo.** En `cuentas.py` se guarda el *nombre* de la variable de
  entorno o la entrada del llavero, nunca el valor; hay un chequeo que lo rechaza. El
  registro vive en `~/.config/clip-pipeline/`, con permisos 600.
- **`datos/` y `salida/` no van al repo**: son VODs, chats y clips de terceros.
- **Todo cambio de lógica viene con test.** Los tests de este proyecto encontraron, ellos
  solos: los falsos positivos de Poisson, que mover palabras rompía el largo máximo de
  una línea, que unir la línea huérfana pegaba frases separadas por silencios, y una
  variante de formato que ponía el payoff después de la mitad del clip.
- Comentarios y docstrings en español, explicando **por qué** y no qué.

## Estado

Ingesta ✅ · detección ✅ · ranking ✅ · corte ✅ · reencuadre ✅ · subtítulos ✅ ·
plan de publicación ✅ · publicar ⬜ (decisión pendiente del usuario).

La nota del proyecto, con las capturas y el historial de cada decisión, está en el vault:
`40_Proyectos/Pipeline de clips/Pipeline de clips.md`.
