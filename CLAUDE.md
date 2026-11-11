# Instrucciones para trabajar en clip-pipeline

Detecta los mejores momentos de un VOD de stream usando **el chat como señal**, y los
deja como clips verticales subtitulados listos para publicar. Responder en español
rioplatense.

**Si venís a hacer un cambio puntual, buscá el archivo en el mapa de abajo y leé solo
ese.** Los módulos no se conocen entre sí salvo por lo que dice el grafo.

## Entorno

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q          # 212 tests, corren en unos segundos
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --top 5 --cortar 3 \
    --formato dividido --facecam 4,268,320,188 --subtitulos --streamer NOMBRE
```

Para varios VODs: `python -m clips.lote` (cola) y `python -m clips.panel` (panel web en
http://localhost:8765; también está como `panel` en `.claude/launch.json`).

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
| `publicar.py` | Valida cada clip por plataforma (TikTok, Reels, Shorts, X), escribe el manifiesto y un .txt por plataforma listo para pegar | Límites de plataforma, horarios, payload de Postiz |
| `cuentas.py` | Registro de cuentas y estado de monetización. CLI propia | Agregar una plataforma, actualizar qué paga cada una |
| `entrega.py` | El paquete para el streamer: una carpeta y un zip por directo con los clips que pasaron el control, el texto de cada uno y un LEEME. No manda nada | Qué entra en el paquete, cómo se llaman los archivos, qué dice el LEEME |
| `pipeline.py` | Del pico al clip terminado: corte, reencuadre, subtítulos. Un pico a la vez. Se testea reemplazando las tres etapas (`tests/test_pipeline.py`) | El orden de las etapas, qué pasa cuando una falla |
| `perfiles.py` | Perfil por streamer: canales, facecam, formato, permiso. CLI propia | Agregar un dato que es constante por streamer |
| `lote.py` | La cola de VODs y su estado (`datos/lote.json`): picos, veredictos, clips, publicaciones. CLI propia | Qué se recuerda de cada VOD, cómo se descubren VODs nuevos |
| `revision.py` | Precisión del detector contra los veredictos. Solo funciones puras | Qué cuenta como acierto, por qué cortes se abre la medición |
| `panel.py` + `panel.html` | Panel web local, con la biblioteca estándar. El HTML es un solo archivo sin build | Rutas, pestañas, qué se muestra |
| `cli.py` | Un VOD desde la terminal. No tiene lógica propia | Flags nuevos, lo que se imprime |

### Grafo de dependencias

```
texto ──┬── vocabulario ── picos ──┬── cortar
        ├── subtitulos             └── publicar ── metadatos
        └── metadatos
chat ───┴── vocabulario, picos
estilos, reencuadre, cuentas, perfiles, revision: sin dependencias internas
entrega: texto (trabaja sobre los dicts del estado, no importa lote)
pipeline: cortar, reencuadre, subtitulos, estilos, metadatos
lote: chat, picos, perfiles, revision, pipeline, publicar
cli y panel: arriba de todo, no los usa nadie (panel usa lote, perfiles, revision,
entrega, estilos)
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
- **La ventana se ensancha para chats chicos, el umbral no se toca**
  (`picos.ventana_sugerida`). Con 5 s fijos, cinco VODs de canales medianos (0,13 a
  0,46 msg/s) dieron 0 o 1 pico: hay menos de un mensaje por bin y Anscombe deja de
  valer. Se apunta a ~4 mensajes por ventana, con tope de 30 s. En chats sintéticos
  la detección pasó de 6/40 a 40/40 sin un solo falso positivo en 80 chats planos.
  Para más de 0,8 msg/s da 5 s: lo validado con chats grandes no cambió.
- **La línea de base tiene un mínimo de 60 bins** (`picos.BINS_BASE_MINIMOS`). Con
  ventanas de 30 s, 10 minutos son 20 bins y el MAD salía inflado.
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
- **La facecam del VOD manda sobre la del perfil** (`lote.encuadre`). Es constante por
  streamer casi siempre, pero cambia con el juego o la escena. Se mide desde el mismo
  medidor, con «Guardar solo para este VOD». No cambia el formato: eso lo decide el
  perfil.
- **Subtítulos en ASS, no SRT** (`subtitulos.a_ass`). Un SRT quemado sale con el estilo
  por defecto de libass: chico, sin contorno, ilegible sobre imagen clara.
- **El gancho se arma DESPUÉS de transcribir** (`cli.py`). Si no, sale con la falta de
  ortografía del chat quemada en pantalla. Por eso `subtitular` está partido en
  `preparar` y `rematar`.
- **La etapa de publicación no publica** (`publicar.py`). Arma el manifiesto, deja un
  `.txt` por plataforma listo para pegar, y para. `paquete_postiz()` devuelve el payload
  sin hacer ninguna llamada. El usuario decidió publicar a mano en las cuatro
  plataformas; automatizar el envío quedó pendiente de decisión.

- **Se produce de a un pico** (`pipeline.producir`). La versión por etapas emparejaba
  clips y picos con `zip`: si un corte fallaba, el clip siguiente salía con el título y
  el gancho del pico anterior.
- **La E/S del lote no toca el estado** (`lote.py`). `analizar_url` y `producir_pico`
  devuelven un resultado y una función pura lo aplica. Es lo que permite testear sin red
  y que el panel no tenga el estado tomado durante un análisis de varios minutos.
- **Reanalizar conserva los veredictos** (`lote.fusionar_picos`). Son el único dato del
  proyecto que no se regenera: cuestan un humano mirando el video.
- **El estado se respalda solo al guardarlo** (`lote.respaldar`), en
  `~/.config/clip-pipeline/respaldos/`, afuera del repo. Una copia por día y, aparte,
  una cada vez que el estado nuevo trae menos veredictos que el de disco: la diaria no
  cubre un bug que pise a la tarde lo revisado a la mañana. Las `perdida_*` no se
  borran nunca.
- **Sin permiso concedido no se registra una publicación** (`lote.exigir_permiso`).
  Producir sí se puede: los clips hechos son lo que se manda para pedir el permiso.
  El chequeo está en la acción del panel, no solo en el botón.
- **Hay dos veredictos y no se mezclan** (`lote.poner_control`). El del pico se pone
  mirando el VOD embebido, antes de producir, y dice que el momento vale. El control
  se pone sobre el clip terminado. Rehacer un clip le saca el visto bueno: era de otro
  archivo. Solo los clips con control «ok» entran en una entrega.
- **El corrimiento al rehacer no cambia el `t` del pico** (`lote.producir_pico`). El
  `t` es la identidad del pico en el estado; el corrimiento se aplica a la copia que se
  produce y queda anotado en `ajuste`.
- **La entrega es por directo y sí se arma sin permiso concedido** (`entrega.armar`).
  Los clips hechos son lo que se manda para pedirlo; el LEEME lo presenta como muestra.
  Solo «negado» la frena. Registrar una publicación sigue exigiendo «concedido».
- **Los textos tienen voz** (`metadatos.VOCES`, campo `voz` del perfil). «tercero» es
  un canal de clips hablando del streamer; «propia» es el streamer en su cuenta: no se
  nombra, no se da crédito y no lleva `#clips`. El valor por defecto es «tercero» para
  que el CLI y los perfiles viejos no cambien; a un cliente del servicio hay que
  ponerle «propia» a mano.
- **Quién publica** (decidido el 2026-11-11). Por ahora el usuario, en su propio canal
  de clips (voz `tercero`). Con un cliente, en la cuenta del cliente (voz `propia`): el
  usuario, o el cliente mismo si lo prefiere. La entrega cubre ese segundo caso, además
  de la muestra del primer mensaje y la vista previa.
- **«Dudoso» cuenta como error y la precisión va con intervalo de Wilson**
  (`revision.py`). Con 50 veredictos como mucho, un porcentaje suelto invita a creerle
  de más.
- **La revisión mira el VOD embebido, no un clip cortado** (`panel.html`). Validar 50
  picos cortando 50 clips costaba disco y tiempo; embebido cuesta cero y solo se corta
  lo aprobado.
- **El panel es biblioteca estándar, sin framework** (`panel.py`). Una docena de rutas;
  lo único que faltaba era servir video con `Range`. Escucha solo en 127.0.0.1 y
  rechaza POST de otro origen, porque lanza descargas y escribe en disco.
- **El panel tampoco publica.** Copiar texto, bajar el archivo, abrir la subida y anotar
  la URL. Misma decisión que `publicar.py`.

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
- **Los espectadores no predicen el chat.** Un canal de 363 espectadores promedio
  tenía 0,88 msg/s y otro de 790, 0,13. Lo que decide si el detector sirve para un
  streamer es la tasa de mensajes; por debajo de ~0,4 msg/s casi no salen picos, con
  cualquier ventana.
- **El `<iframe>` de YouTube necesita `referrerpolicy`.** Sin mandar el origen, el
  reproductor embebido queda en negro (error 153). Y Twitch exige `parent=` igual al
  host: por eso el panel se abre como `localhost`, que es el único host que acepta sin https.
- **La cabecera del panel no se redibuja si no cambió.** Se actualiza cada 2,5 s;
  reemplazar un botón entre el mousedown y el mouseup se comía el click.
- **TwitchDownloaderCLI no escribe nada hasta terminar.** El archivo de salida queda en
  0 bytes durante toda la descarga: no es señal de que esté colgado. Hay un tope de
  tiempo en `chat.TIEMPO_MAXIMO`.
- **Un test que guarde el estado en la ruta por defecto escribiría respaldos en la
  config real.** `tests/conftest.py` redirige `lote.RESPALDOS` a una carpeta temporal
  para todos; no sacarlo.
- **`perfiles.VOCES` repite `metadatos.VOCES`** para que `perfiles` siga sin importar
  nada del proyecto. Hay un test que exige que coincidan.
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
plan de publicación ✅ · perfiles ✅ · lote ✅ · panel ✅ · control del clip ✅ ·
entrega ✅ (sin probar todavía con un clip real) ·
**precisión medida ⬜** (faltan los veredictos del usuario en el panel) ·
publicar ⬜ (decisión pendiente del usuario).

La nota del proyecto, con las capturas y el historial de cada decisión, está en el vault:
`40_Proyectos/Pipeline de clips/Pipeline de clips.md`.
