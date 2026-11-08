"""Etapa 4: subtítulos quemados, generados desde el audio.

Por qué generarlos y no usar los del juego: en el modo dividido el recorte corta los
subtítulos que el juego dibuja a un costado, y además la mayoría del contenido no tiene
texto en pantalla. Transcribiendo el audio sirve para todo igual.

Se usa faster-whisper (CTranslate2) en CPU con cuantización int8: el modelo `small`
ocupa ~480 MB y alcanza para castellano. El modelo se baja una sola vez a la cache de
HuggingFace y después queda local.

El audio se extrae con ffmpeg y se le pasa a faster-whisper como array, en vez de
dejar que lo abra él: su decodificador usa PyAV y la combinación de versiones se rompe
(`av.open() got an unexpected keyword argument 'metadata_errors'`). Pasando el array
nos sacamos PyAV de encima, y ffmpeg ya es dependencia del proyecto.

El formato de salida es ASS y no SRT porque hace falta controlar tipografía, contorno y
posición: un SRT quemado sale con el estilo por defecto de libass, que en vertical queda
chico y sin contorno, ilegible sobre imagen clara.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .texto import es_funcional, limpio, sin_tildes

MODELO_POR_DEFECTO = "small"


@dataclass
class Palabra:
    t0: float
    t1: float
    texto: str


@dataclass
class Linea:
    palabras: list[Palabra] = field(default_factory=list)

    @property
    def t0(self) -> float:
        return self.palabras[0].t0

    @property
    def t1(self) -> float:
        return self.palabras[-1].t1

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palabras)


def audio_16k(video: Path):
    """Audio mono a 16 kHz como float32 en [-1, 1], que es lo que espera el modelo."""
    import numpy as np

    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-f", "s16le", "-"],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"no pude extraer audio: {(r.stderr or b'').decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def transcribir(
    video: Path, modelo: str = MODELO_POR_DEFECTO, idioma: str | None = None,
) -> tuple[list[Palabra], str]:
    """Devuelve las palabras con sus tiempos y el idioma detectado."""
    from faster_whisper import WhisperModel

    m = WhisperModel(modelo, device="cpu", compute_type="int8")
    segmentos, info = m.transcribe(
        audio_16k(video), language=idioma, word_timestamps=True,
        vad_filter=True,  # saca los silencios: evita que invente texto donde no hay voz
    )
    palabras: list[Palabra] = []
    for seg in segmentos:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                palabras.append(Palabra(float(w.start), float(w.end), t))
    return palabras, info.language


FIN_FRASE = ".?!…"
PAUSA = ",;:"


def agrupar(
    palabras: list[Palabra], max_chars: int = 22, max_dur: float = 2.5,
    corte_silencio: float = 0.6,
) -> list[Linea]:
    """Junta palabras en lineas cortas, como se usa en vertical.

    El orden de preferencia para cortar es: puntuacion de fin de frase, pausa (coma)
    si la linea ya esta llena a medias, silencio largo en el audio, y recien despues
    ancho o duracion.

    Cuando el corte lo fuerza el ancho o la duracion, el punto de corte **retrocede**
    hasta la ultima palabra con contenido: las funcionales que quedaban colgando
    ("...le puedo decir que se") arrancan la linea siguiente en vez de cerrar la
    anterior. Retroceder es mejor que mover las palabras despues, porque la linea de
    destino todavia esta vacia y siempre hay lugar.
    """
    lineas: list[Linea] = []
    actual: list[Palabra] = []
    for p in palabras:
        if actual:
            texto = " ".join(q.texto for q in actual)
            previa = actual[-1].texto.rstrip()
            if previa[-1:] in FIN_FRASE:
                razon = "frase"
            elif previa[-1:] in PAUSA and len(texto) >= 0.6 * max_chars:
                razon = "pausa"
            elif p.t0 - actual[-1].t1 >= corte_silencio:
                razon = "silencio"
            elif len(texto) + 1 + len(p.texto) > max_chars:
                razon = "ancho"
            elif p.t1 - actual[0].t0 > max_dur:
                razon = "duracion"
            else:
                razon = ""

            if razon:
                # solo se retrocede en los cortes arbitrarios: en una pausa o un
                # silencio el corte ya cae donde corresponde, y arrastrar palabras
                # al otro lado de un silencio deja el subtitulo colgado en pantalla
                arrastre = _colgantes(actual) if razon in ("ancho", "duracion") else 0
                corte = len(actual) - arrastre
                lineas.append(Linea(actual[:corte]))
                actual = actual[corte:]
        actual.append(p)
    if actual:
        lineas.append(Linea(actual))
    return _unir_huerfana(lineas, max_chars, max_dur, corte_silencio)


def _colgantes(palabras: list[Palabra], min_ratio: float = 0.3) -> int:
    """Cuantas palabras funcionales finales conviene pasar a la linea siguiente.

    Nunca tantas como para dejar la linea anterior pelada: si sacarlas la reduce a
    menos del 30% de su texto, se deja el corte feo y listo.
    """
    k = 0
    while k < len(palabras) - 1 and es_funcional(palabras[-1 - k].texto):
        k += 1
    if not k:
        return 0
    entero = len(" ".join(q.texto for q in palabras))
    queda = len(" ".join(q.texto for q in palabras[:-k]))
    return k if queda >= min_ratio * entero else 0


def _unir_huerfana(
    lineas: list[Linea], max_chars: int, max_dur: float, corte_silencio: float
) -> list[Linea]:
    """Una ultima linea de una sola palabra se ve mal: se pega a la anterior si entra.

    "Si entra" es por ancho, por duracion Y porque no haya un silencio en el medio:
    pegar dos frases separadas por varios segundos deja el subtitulo colgado en
    pantalla durante el silencio.
    """
    if len(lineas) < 2 or len(lineas[-1].palabras) != 1:
        return lineas
    a, b = lineas[-2], lineas[-1]
    if (len(a.texto) + 1 + len(b.texto) <= max_chars
            and b.t1 - a.t0 <= max_dur
            and b.t0 - a.t1 < corte_silencio):
        a.palabras = a.palabras + b.palabras
        return lineas[:-1]
    return lineas


def _tiempo_ass(s: float) -> str:
    s = max(0.0, s)
    h, resto = divmod(s, 3600)
    m, seg = divmod(resto, 60)
    return f"{int(h)}:{int(m):02d}:{seg:05.2f}"


def a_ass(
    lineas: list[Linea], ancho: int = 1080, alto: int = 1920,
    cuerpo: int = 64, margen_abajo: int = 360,
    claves: set[str] | None = None, color_clave: str = "&H0000D7FF",
    gancho: str = "", dur_gancho: float = 2.5,
) -> str:
    """Genera el ASS, dimensionado para el video vertical.

    claves:  palabras del tema, que se pintan de otro color dentro del subtítulo.
             Son las que el chat repitió, o sea las que explican el momento: que
             salten a la vista es la diferencia entre leer y entender.
    gancho:  texto grande arriba durante los primeros segundos. Lo que decide si el
             clip se ve es el primer segundo y medio, y mucha gente mira en mudo.
    """
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ancho}
PlayResY: {alto}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Clip,DejaVu Sans,{cuerpo},&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,3,2,60,60,{margen_abajo},1
Style: Gancho,DejaVu Sans,{int(cuerpo * 1.25)},&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,6,0,8,70,70,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    filas = []
    if gancho:
        filas.append(f"Dialogue: 1,{_tiempo_ass(0)},{_tiempo_ass(dur_gancho)},"
                     f"Gancho,,0,0,0,,{_escapar(gancho)}")
    for l in lineas:
        filas.append(f"Dialogue: 0,{_tiempo_ass(l.t0)},{_tiempo_ass(l.t1)},Clip,,0,0,0,,"
                     f"{_resaltar(l.texto, claves, color_clave)}")
    return cab + "\n".join(filas) + "\n"


def _resaltar(texto: str, claves: set[str] | None, color: str) -> str:
    """Pinta de otro color las palabras del tema dentro de la línea."""
    escapado = _escapar(texto)
    if not claves:
        return escapado
    salida = []
    for w in escapado.split():
        if sin_tildes(limpio(w)) in claves:
            salida.append(f"{{\\c{color}&}}{w}{{\\c&H00FFFFFF&}}")
        else:
            salida.append(w)
    return " ".join(salida)


def _escapar(t: str) -> str:
    """En ASS las llaves abren códigos de estilo y los saltos rompen la línea del evento."""
    return t.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def quemar(video: Path, ass: Path, salida: Path) -> Path:
    # en el filtro, ':' y ',' separan argumentos: hay que escaparlos en la ruta
    ruta = str(ass).replace("\\", "/").replace(":", r"\:").replace(",", r"\,")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"ass='{ruta}'",
         "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-c:a", "copy",
         str(salida)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not salida.exists():
        raise RuntimeError(f"ffmpeg falló al quemar: {(r.stderr or '').strip()[-400:]}")
    return salida


def preparar(
    video: Path, modelo: str = MODELO_POR_DEFECTO, idioma: str | None = None,
    max_chars: int = 22,
) -> tuple[list[Linea], str]:
    """Transcribe y agrupa, sin tocar el video todavía.

    Está separado de `rematar` porque el gancho y las palabras a resaltar se arman
    **con la transcripción**: el chat escribe mal y el audio corrige la ortografía.
    Si se hiciera todo de una, el gancho saldría con el error del chat quemado en
    pantalla (pasó: decía «¿Romanse o malas?»).
    """
    palabras, detectado = transcribir(video, modelo=modelo, idioma=idioma)
    if not palabras:
        raise RuntimeError("la transcripción no devolvió palabras (¿el clip tiene audio?)")
    return agrupar(palabras, max_chars=max_chars), detectado


def rematar(video: Path, lineas: list[Linea], sufijo: str = "_sub", **kw_estilo) -> Path:
    """Escribe el ASS y lo quema."""
    ass = video.with_suffix(".ass")
    ass.write_text(a_ass(lineas, **kw_estilo), encoding="utf-8")
    salida = video.with_name(video.stem + sufijo + ".mp4")
    return quemar(video, ass, salida)


def subtitular(
    video: Path, modelo: str = MODELO_POR_DEFECTO, idioma: str | None = None,
    sufijo: str = "_sub", max_chars: int = 22, **kw_estilo,
) -> tuple[Path, list[str]]:
    """Camino corto: transcribe, arma el ASS y lo quema, sin gancho dependiente."""
    lineas, detectado = preparar(video, modelo, idioma, max_chars)
    salida = rematar(video, lineas, sufijo, **kw_estilo)
    print(f"  idioma detectado: {detectado} | {len(lineas)} líneas")
    return salida, [l.texto for l in lineas]
