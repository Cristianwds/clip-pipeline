# clip-pipeline

Detecta momentos destacados en VODs de streams usando **el chat como señal**, para
cortar clips sin mirar el VOD entero.

La idea de fondo: el chat ya es un anotador humano de highlights trabajando gratis
y en tiempo real. Cuando pasa algo bueno, se acelera. Si esa señal funciona, no hace
falta ni mirar el video ni correr un modelo de visión para encontrar los momentos.

Es la etapa 2 de un pipeline más largo (ingesta → **detección** → reencuadre 9:16 →
subtítulos → publicación). Se construye en ese orden porque si la etapa 2 no funciona,
el resto no tiene sentido.

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Necesita `yt-dlp` y `ffmpeg` en el sistema. `yt-dlp` conviene mantenerlo actualizado:
YouTube cambia de formato seguido y es la dependencia que más se rompe.

## Uso

```bash
# listar los picos y dar los links para validarlos a ojo
PYTHONPATH=src .venv/bin/python -m clips.cli "https://www.youtube.com/watch?v=XXXX"

# ademas cortar los 5 mejores a salida/
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --cortar 5
```

Imprime, por cada pico: el momento, el score en sigmas, los mensajes por segundo, el
**foco** y el **tipo** de momento:

```
 #    momento   prior   score   foco   msg/s  tipo        que dice el chat
 1   0h36m27s     5.5     5.0   0.60    10.6  épico      clap×45, footyellow, ibaipeepokoi
 2   0h58m52s     4.2     5.1   0.33     7.0  gracioso   spitthetruth×5, opa×19  [xdd, om]
 3   3h19m12s     4.0     4.9   0.32     8.4             t1×24, traela, anti
 6   2h01m37s     3.4     6.4   0.02     8.8  gracioso   kappa×3  [xd×8, om×7, xdd×6]
```

Con `--ordenar score|foco|prioridad` se puede comparar. Fijate el último: tenía **el
score más alto del VOD** y es el peor clip, porque no hay ningún tema (foco 0.02) — es
chat spameando emotes.

## Cómo decide qué es un pico

Dos correcciones que hacen la diferencia entre algo que funciona y algo que no:

**1. Normalizar contra lo normal de ese momento.** La tasa cruda de mensajes depende
de cuánta gente está mirando, y eso varía a lo largo del stream. Se compara cada
ventana contra una mediana móvil de 10 minutos: importa cuántos mensajes *más que lo
habitual*, no cuántos mensajes.

**2. Descontar el ruido de conteo.** Un chat es un proceso de conteo: aun sin que pase
nada, la cantidad de mensajes por ventana fluctúa con desvío `sqrt(n)`. La primera
versión usaba un umbral de 3 sigmas sobre el MAD y **devolvía 10 "highlights" en un
chat sintético donde no había ninguno**. Se arregla con la transformación de Anscombe
(deja el ruido con varianza 1 para cualquier tasa base) y con un umbral corregido por
la cantidad de ventanas: si mirás 1500 ventanas, algunas pasan 3 sigmas por puro azar.
El umbral automático queda en ~4.2–4.6 sigmas según la duración.

**3. Mirar de qué habla el chat, no cuánto habla.** Listar las palabras más repetidas de
un pico no sirve: lo más frecuente de una ventana es lo más frecuente del chat en general
(`xdd` en Twitch, `de`/`que` en YouTube). Se mide **especificidad**: la frecuencia del
token dentro del pico contra su frecuencia en todo el stream.

```
                 veces en el pico / total en el pico
especificidad = -------------------------------------
                 veces en el stream / total del stream
```

Alta → nombra el momento (`faker`, `romance`). Cerca de 1 → es cómo habla ese chat.

Eso solo no alcanza, porque durante *cualquier* pico la proporción de emotes sube y `xdd`
también da especificidad alta. Hace falta un segundo nivel: **en cuántos picos se eleva
cada token**. Si se eleva en muchos, es vocabulario de reacción; si se eleva en uno solo,
es el asunto de ese momento. Es TF-IDF con los picos como documentos.

De ahí sale el **foco**: qué fracción del chat converge en algo propio de ese momento. No
hay ningún diccionario de emotes que mantener: se adapta solo a cada streamer e idioma.

**6. Ordenar por volumen y acuerdo, no por volumen.**

```
prioridad = score x (0.5 + foco)
```

El score mide cuánto chat hay; el foco, cuánto se ponen de acuerdo. No se multiplica por
el foco directo porque un pico sin tema identificable igual puede ser un clip (un minuto
de risas no tiene "asunto"), así que el foco ajusta pero no anula. Los artefactos
(`cierre`) van siempre al final.

**4. Filtrar el arranque por tiempo y el final por contenido.** El `¿se escucha?` del
principio nunca es un clip, así que se descartan los primeros 10 minutos. El final **no**
se descarta por tiempo: en las pruebas, filtrar los últimos 5 minutos mataba la despedida
pero también el climax, que estaba 2 minutos antes. La despedida se detecta por lo que dice
el chat (etiqueta `cierre`) y el CLI avisa.

**5. Normalizar el alargue de letras.** El chat escribe `siiii` y `romanceeee`; sin
colapsar las repeticiones el conteo se parte y ningún token llega al mínimo.

Todo esto está cubierto por tests: que encuentre picos inyectados a mano, que **no invente
ninguno en un chat plano**, que el tema sea lo específico y no lo frecuente, que el foco
baje cuando el pico no tiene tema propio, y que descarte el arranque sin perder el final.

## Estado

| Etapa | Estado |
| :--- | :--- |
| Ingesta de chat (YouTube) | ✅ anda, vía yt-dlp |
| Ingesta de chat (Twitch) | ✅ anda, vía TwitchDownloaderCLI |
| Detección de picos | ✅ con tests |
| Corte del segmento | ✅ baja solo el tramo, no el VOD entero |
| Reencuadre 9:16 | ✅ `desenfoque`, `recorte` y `dividido` |
| Subtítulos | ✅ faster-whisper + ASS quemado |
| Plan de publicación | ✅ metadatos y manifiesto (no publica) |

### Twitch

Twitch necesita una herramienta aparte, porque:

- `yt-dlp` baja el **video** de un VOD de Twitch, pero no el chat: en Twitch el chat
  no es una pista de subtítulos como en YouTube.
- La **API oficial de Twitch (Helix) no tiene endpoint de chat de VODs**. No es que
  falte la credencial: el dato no está expuesto.
- La única vía es la API interna de GQL.

Se usa [TwitchDownloaderCLI](https://github.com/lay295/TwitchDownloader), que encapsula
esa API y la mantiene actualizada. Es un binario de terceros, así que **vive afuera del
repo** y no es una dependencia instalable:

```bash
# release oficial, verificando el sha256 que publica la API de GitHub
mkdir -p ~/.local/opt/twitchdownloader && cd ~/.local/opt/twitchdownloader
curl -fsSL -o td.zip https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/TwitchDownloaderCLI-1.56.5-Linux-x64.zip
sha256sum td.zip   # f2bcee00d0e6da759d97c23767345634cb9cdb10cdff96cfa96f97983d51bf55
unzip td.zip && chmod +x TwitchDownloaderCLI
```

La ruta se puede sobreescribir con la variable `TWITCHDOWNLOADER_CLI`.

> Riesgo asumido: la API interna de Twitch no es oficial y puede cambiar sin aviso. La
> ventaja de usar TwitchDownloaderCLI en vez de pegarle a mano es que cuando se rompa,
> lo arregla el que mantiene el proyecto.

## Reencuadre a 9:16

Dos modos, porque no hay uno bueno para todo:

```bash
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --cortar 3 --formato desenfoque
```

- **`desenfoque`** (default): el 16:9 entero, escalado al ancho, sobre una copia ampliada
  y desenfocada de sí mismo. No se pierde nada de imagen, pero todo queda chico.
- **`recorte`**: recorte vertical a altura completa que se mueve siguiendo la acción. Se
  ve grande, pero deja afuera los costados.
- **`dividido`**: la facecam arriba y la acción abajo, cada una con su recorte. Es el
  formato que usan los clippers y el único que deja ver las dos cosas a la vez. Necesita
  saber dónde está la facecam:

  ```bash
  PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --cortar 3 \
      --formato dividido --facecam 4,268,320,188
  ```

  **No se detecta sola.** Es una constante por streamer (se mide una vez sacando un frame
  y mirando las coordenadas) y detectarla automáticamente sin un modelo es poco confiable.
  Dos detalles que importan y no son obvios:

  - La facecam se **excluye del análisis de movimiento**. Es lo que más se mueve en
    pantalla, y si no se la saca el encuadre se va siempre hacia ella en vez de seguir la
    acción. Medido: sacarla corrió el encuadre de 338–494 px a 422–554.
  - El recorte de abajo se **limita para no incluir la facecam**, que si no aparece dos
    veces en el mismo clip. Si no hay ancho suficiente para evitarla, se repite y listo.

- **`nada`**: corta y no reencuadra.

### El tracking es sin modelo

**No hay detección de caras.** OpenCV 5 dejó de incluir las cascadas de Haar y
`CascadeClassifier` ya no existe en el wheel; el detector que sí trae (`FaceDetectorYN`)
necesita bajar un ONNX aparte. Así que el sujeto se estima por **energía de movimiento**:
se compara cada frame con el anterior, se suma la diferencia por columna y se busca la
franja vertical donde más cambia la imagen.

Para un stream es una aproximación razonable —lo que se mueve es la persona o la acción—
y no depende de ningún modelo descargado. La trayectoria pasa por tres filtros para que
no maree: media móvil, zona muerta (si el objetivo se movió poco, la cámara no se mueve) y
límite de velocidad. El movimiento se aplica con `sendcmd` de ffmpeg, que cambia la `x` del
`crop` en cada timestamp, así que **no hay que decodificar y recodificar frame por frame**.

## Subtítulos

```bash
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --cortar 3 \
    --formato dividido --facecam 4,268,320,188 --subtitulos
```

Ese comando hace el pipeline entero: detecta los picos, corta los mejores, los pasa a
9:16 y les quema los subtítulos.

Se transcriben con **faster-whisper** en CPU con cuantización int8. El modelo `small`
(~480 MB, se baja una vez) transcribió 35 s de audio en 11 s en 16 núcleos. Se cambia con
`--modelo tiny|base|small|medium` y el idioma se detecta solo (`--idioma es` lo fuerza).

Tres decisiones que no son obvias:

- **El audio se extrae con ffmpeg**, no se deja que lo abra faster-whisper. Su decodificador
  usa PyAV y la combinación de versiones falla
  (`av.open() got an unexpected keyword argument 'metadata_errors'`). Pasándole el array ya
  decodificado nos sacamos PyAV de encima, y ffmpeg ya era dependencia.
- **La salida es ASS, no SRT.** Un SRT quemado sale con el estilo por defecto de libass:
  chico y sin contorno, ilegible sobre imagen clara. El ASS se genera dimensionado para
  1080x1920, con contorno de 5 px.
- **Líneas cortas** (22 caracteres). El orden de preferencia para cortar es: puntuación de
  fin de frase, pausa (coma) si la línea ya está llena a medias, silencio largo en el
  audio, y recién después ancho o duración.

  Cuando el corte lo fuerza el ancho, el punto de corte **retrocede hasta la última
  palabra con contenido**: las funcionales que quedaban colgando arrancan la línea
  siguiente en vez de cerrar la anterior. En el clip de prueba eso bajó las líneas
  terminadas en palabra funcional de **8 sobre 19 a 1 sobre 25**: `le puedo decir que se`
  pasó a `le puedo decir` + `que se vaya`.

  Retroceder el corte es mejor que mover las palabras en una segunda pasada: la línea de
  destino todavía está vacía y siempre hay lugar. La primera versión lo hacía moviendo, y
  la mitad de los casos quedaban sin arreglar porque la línea siguiente ya estaba llena.

  El retroceso **solo se aplica a los cortes por ancho o duración**. En una pausa o un
  silencio el corte ya cae donde corresponde, y arrastrar palabras al otro lado de un
  silencio deja el subtítulo colgado en pantalla.

## Que no salgan todos iguales

Un canal donde los 30 clips tienen el mismo encuadre, la misma tipografía y el mismo
texto es lo que las plataformas leen como contenido producido en masa, y además cansa.
Tres cosas lo evitan:

**1. Variantes de formato que rotan.** Cada clip usa una de cuatro variantes, que
cambian el largo, el tamaño y la posición de los subtítulos, el color del resaltado y
si lleva gancho. La elección **rota con el índice**, no es al azar: es reproducible y
dos clips seguidos nunca salen iguales.

| Variante | Largo | Payoff a los | Cuerpo | Gancho |
| :--- | ---: | ---: | ---: | :--- |
| directo | 18s | 22% | 68 | sí |
| contexto | 26s | 31% | 60 | sí |
| crudo | 21s | 24% | 72 | no |
| desarrollo | 28s | 32% | 62 | sí |

Los largos salen de las guías de retención de formato corto, que ubican el punto dulce
entre 15 y 30 segundos. Antes se cortaba 20 s antes del pico y 15 después: 35 segundos
con la parte buena recién a la mitad.

El `antes` es corto a propósito. ==El pico del chat no es el instante del hecho: el chat
reacciona uno o dos segundos después==, así que el momento ya está un poco antes del
centro de la ventana.

**2. Gancho en los primeros 2,5 segundos.** Un cartel grande arriba, de 8 palabras como
máximo, armado con las mismas palabras que el chat repitió: `¿Mátala o romance?`. Las
guías coinciden en que el primer par de segundos decide la distribución, y en que el
gancho tiene que leerse **sin sonido**, porque mucha gente mira en mudo.

**3. Palabras clave resaltadas.** Dentro del subtítulo, las palabras del tema van en otro
color. Son justo las que explican el momento, y el color cambia según la variante.

```bash
# rotación automática (default)
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --cortar 5 ...
# o forzar una
... --variante crudo --sin-gancho
```

## Cuentas y monetización

```bash
PYTHONPATH=src .venv/bin/python -m clips.cuentas estado
PYTHONPATH=src .venv/bin/python -m clips.cuentas agregar youtube_shorts --handle @micanal
PYTHONPATH=src .venv/bin/python -m clips.cuentas info tiktok
```

Lleva el registro de las cuentas de cada plataforma y lo cruza con lo que paga cada una.
Estado **verificado el 2026-11-08** (las plataformas lo cambian seguido):

| Plataforma | Programa | ¿Paga en Argentina? |
| :--- | :--- | :--- |
| YouTube Shorts | Partner Program | **Sí**, acceso abierto. Es la única con reglas claras |
| Facebook | Estrellas / monetización | Por invitación, pero el umbral de Estrellas es el más bajo |
| Instagram Reels | Bonos de Reels | Por invitación; sirve para alcance, no para cobrar por vistas |
| TikTok | Creator Rewards | **No.** Solo US, UK, DE, JP, KR, FR, MX y BR |
| X (Twitter) | Original Content Rewards | Argentina está en la lista, pero el programa está hecho para no pagar contenido copiado, y hay que pagar Premium antes |

> En el registro **no van contraseñas, tokens ni API keys**: se guarda el *nombre* de la
> variable de entorno o la entrada del llavero donde vive cada credencial. Hay un chequeo
> que rechaza cualquier valor que parezca un token de verdad, y el archivo se guarda con
> permisos 600.

## Plan de publicación

Al terminar, el pipeline escribe `salida/plan_publicacion.md` (para leer) y
`plan_publicacion.json` (para consumir). **No publica nada**: deja cada clip con su
título, descripción, hashtags, horario sugerido y los problemas que tendría en cada
plataforma.

```bash
PYTHONPATH=src .venv/bin/python -m clips.cli "<url>" --cortar 3 \
    --formato dividido --facecam 4,268,320,188 --subtitulos \
    --streamer AuronPlay --juego "AC Odyssey"
```

### De dónde sale el título

De cruzar las dos señales que el pipeline ya tiene. El `tema` del pico son las palabras
que el chat repitió ahí, y la transcripción es lo que se dijo. El cruce resuelve que
**el chat escribe mal**: en el clip de prueba escribió "romance" de dos formas
(`romanse`, `romanze`). Las variantes se agrupan por distancia de edición, los conteos se
suman y para elegir la ortografía gana la del audio.

La corrección tiene un límite que importa: si el chat escribió la palabra de **varias**
formas se aceptan hasta 2 ediciones de diferencia con el audio; si la escribió de **una
sola** forma, 1 edición. Sin ese límite la corrección convertía `malas` —bien escrito—
en `mata`, que es otra palabra que estaba en el audio a dos ediciones.

La **cita** se arma juntando hasta cuatro líneas consecutivas de subtítulo, porque las
líneas sueltas son fragmentos de 22 caracteres. Después se le recorta la cola que
arranque en una conjunción, para que no termine en "...y encima".

Las plataformas de destino son cuatro: TikTok, Instagram Reels, YouTube Shorts y X.

### Texto listo para pegar

Además del manifiesto, se escribe un `.txt` por clip y plataforma en `salida/textos/`:

```
pico_01_1h48m27s_18s_9x16_sub.tiktok.txt
pico_01_1h48m27s_18s_9x16_sub.x.txt
...
```

Publicar a mano es abrir el archivo, copiar todo y pegarlo. En X el texto va en uno solo
(280 caracteres sin Premium); en el resto el título va aparte del cuerpo.

### Validación por plataforma

Cada clip se mide con ffprobe y se compara contra los límites de cada plataforma
(duración, peso, aspecto). El manifiesto dice `✅ listo` o qué le falta. Los límites
están todos en `PLATAFORMAS`, en un solo lugar, porque **las plataformas los cambian**.

### El horario

Un clip por día, y las plataformas escalonadas dentro del día. Publicar cinco clips de
golpe en tres plataformas es exactamente el patrón que YouTube marca como contenido
producido en masa; separarlos no cuesta nada.

### Conectar Postiz

`paquete_postiz()` devuelve el payload que habría que mandarle, sin hacer ninguna llamada.
Falta el id del canal (sale de la cuenta de Postiz) y subir el archivo. Está así a
propósito: son clips de contenido de otra persona y publicar es una decisión, no un paso
automático.

## Estructura

| Archivo | Qué hace |
| :--- | :--- |
| `src/clips/texto.py` | Tokenizar, sacar tildes, listas de conectores y palabras funcionales |
| `src/clips/chat.py` | Baja el chat del VOD y lo cachea |
| `src/clips/picos.py` | Detecta los picos de actividad y los rankea |
| `src/clips/vocabulario.py` | De qué habla el chat en cada pico |
| `src/clips/cortar.py` | Baja solo el tramo de cada pico |
| `src/clips/reencuadre.py` | De 16:9 a 9:16, con tracking por movimiento |
| `src/clips/subtitulos.py` | Transcribe y quema los subtítulos |
| `src/clips/estilos.py` | Las variantes de formato |
| `src/clips/metadatos.py` | Título, gancho, cita y hashtags |
| `src/clips/publicar.py` | Valida por plataforma y escribe el manifiesto |
| `src/clips/cuentas.py` | Registro de cuentas y monetización |
| `src/clips/cli.py` | Orquesta todo |

`datos/` guarda el cache de chats y `salida/` los clips: ninguno va al repo.

Hay un [CLAUDE.md](CLAUDE.md) con el mapa de dependencias, las decisiones ya tomadas y
las trampas conocidas, para no tener que leer todo el código antes de un cambio chico.

