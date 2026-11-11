"""Título, cita y hashtags de un clip, sacados de lo que ya sabemos de él.

No hace falta inventar nada ni llamar a ningún modelo: el pico ya trae **de qué
habla el chat** (el `tema`, que son las palabras específicas de ese momento) y el
**tipo** de reacción, y la transcripción trae lo que se dijo. El título sale de
cruzar esas dos cosas.

El cruce además resuelve un problema feo: el chat escribe mal. En el clip de prueba
escribió "romance" de cinco formas (`romanse`, `romanze`, `romanceeee`...). Las
variantes se agrupan por distancia de edición y, para elegir con cuál quedarse, gana
la que **también aparece en la transcripción del audio**, que es la que está bien
escrita. Dos señales independientes corrigiéndose entre sí.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .texto import CONECTORES, CONJUNCIONES, es_funcional, limpio, sin_tildes

# Las palabras ya vienen normalizadas sin tildes cuando se comparan.
CONECTORES_SIN_TILDE = {sin_tildes(w) for w in CONECTORES}

HASHTAGS_POR_TIPO = {
    "gracioso": ["humor", "risa", "momentosgraciosos"],
    "épico": ["epic", "clutch", "momentazo"],
    "bajón": ["fail", "momentostristes"],
    "tensión": ["tenso", "nosevenada"],
    "cierre": [],
}
HASHTAGS_BASE = ["clips", "stream", "twitch", "shorts"]

# Quién habla en el post. Los textos nacieron para un canal de clips de terceros:
# «el chat de AuronPlay no se ponía de acuerdo», «Clip del stream de AuronPlay»,
# #clips. El modelo del proyecto terminó siendo otro, que el clip lo suba el propio
# streamer a su cuenta, y ahí esos textos quedan hablando de él en tercera persona.
#   tercero: un canal de clips hablando del streamer (lo que había)
#   propia:  el streamer en su cuenta; no se nombra a sí mismo ni se da crédito
VOCES = ("tercero", "propia")
# En voz propia no va #clips, que es la marca de un canal de recortes, ni #twitch,
# que se ponía aunque el directo fuera de YouTube.
HASHTAGS_BASE_PROPIA = ["stream", "directo"]


def _validar_voz(voz: str) -> str:
    if voz not in VOCES:
        raise ValueError(f"voz debe ser una de {', '.join(VOCES)}, no {voz!r}")
    return voz

# Palabras que no sirven como titulo aunque sean especificas del momento.
RUIDO = {"xd", "xdd", "jaja", "jajaja", "ja", "lol", "lul", "kekw", "omg", "uh", "ah"}


def _distancia(a: str, b: str) -> int:
    """Levenshtein simple: alcanza porque son palabras cortas y son pocas."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        act = [i]
        for j, cb in enumerate(b, 1):
            act.append(min(prev[j] + 1, act[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = act
    return prev[-1]


def _parecidas(a: str, b: str, tope: int | None = None) -> bool:
    a, b = sin_tildes(a), sin_tildes(b)
    if abs(len(a) - len(b)) > 3:
        return False
    if tope is None:
        tope = 1 if max(len(a), len(b)) <= 4 else 2
    return _distancia(a, b) <= tope


@dataclass
class Token:
    palabra: str
    veces: int


def parsear_tema(tema: list[str]) -> list[Token]:
    """De ['romanse×8', 'matala×20'] a tokens con su conteo."""
    out = []
    for t in tema:
        m = re.match(r"^(.*?)×(\d+)$", t)
        if m:
            out.append(Token(m.group(1), int(m.group(2))))
        elif t:
            out.append(Token(t, 1))
    return out


def consolidar(tokens: list[Token], vocabulario_audio: set[str] | None = None) -> list[Token]:
    """Agrupa variantes de la misma palabra y elige la mejor forma de escribirla.

    Gana la variante que aparece en el audio; si ninguna aparece, la más repetida en
    el chat. El conteo del grupo es la suma: así 'romance' escrito de cinco formas
    pesa lo que tiene que pesar en vez de quedar quinto.
    """
    audio = sorted(vocabulario_audio or set())
    grupos: list[list[Token]] = []
    for t in sorted(tokens, key=lambda x: -x.veces):
        for g in grupos:
            if _parecidas(g[0].palabra, t.palabra):
                g.append(t)
                break
        else:
            grupos.append([t])

    salida = []
    for g in grupos:
        veces = sum(t.veces for t in g)
        mas_usada = max(g, key=lambda t: t.veces).palabra
        salida.append(Token(_forma_del_audio(mas_usada, g, audio), veces))
    return sorted(salida, key=lambda t: -t.veces)


def _forma_del_audio(mas_usada: str, grupo: list[Token], audio: list[str]) -> str:
    """La ortografía del audio le gana a la del chat, que escribe como puede.

    Dos niveles, y el segundo es peligroso:

    - **Coincidencia exacta** salvo tildes y mayúsculas: siempre se aplica, porque
      solo corrige la acentuación (`medico` -> `médico`).
    - **Coincidencia parecida**: cuánto se permite depende de si el chat dudó. Si
      escribió la palabra de **varias formas**, se acepta hasta 2 ediciones: esa
      discrepancia ya es la señal de que no sabe cómo se escribe. Si escribió
      **una sola forma**, se acepta 1 sola edición, que es un typo plausible.

    Ese límite es el que separa los dos casos reales: `romanse` -> `romance` es una
    edición y está bien corregirlo, mientras que `malas` -> `mata` son dos y son
    palabras distintas (el chat había escrito `malas` perfectamente y la corrección
    ingenua se lo comía).
    """
    formas = {sin_tildes(t.palabra) for t in grupo}
    for w in audio:
        if sin_tildes(w) in formas:
            return w
    tope = 2 if len(formas) > 1 else 1
    for w in audio:
        if any(_parecidas(w, t.palabra, tope) for t in grupo):
            return w
    return mas_usada


def titular(
    tema: list[str], etiqueta: str = "", vocabulario_audio: set[str] | None = None,
    streamer: str | None = None, max_palabras: int = 3, voz: str = "tercero",
) -> str:
    """Arma el título con las palabras que el chat repitió en ese momento."""
    return titular_desde(consolidar(parsear_tema(tema), vocabulario_audio),
                         etiqueta, streamer, max_palabras, voz)


def titular_desde(
    tokens: list[Token], etiqueta: str = "", streamer: str | None = None,
    max_palabras: int = 3, voz: str = "tercero",
) -> str:
    propia = _validar_voz(voz) == "propia"
    tokens = [t for t in tokens
              if sin_tildes(t.palabra) not in RUIDO and len(t.palabra) > 2]
    claves = [t.palabra for t in tokens[:max_palabras]]
    if not claves:
        base = {"gracioso": "El momento más gracioso",
                "épico": "El momentazo",
                "tensión": "El momento más tenso"}.get(etiqueta, "El mejor momento")
        if propia:
            return f"{base} del directo"
        return f"{base} de {streamer}" if streamer else base

    nucleo = " vs ".join(claves[:2]) if len(claves) >= 2 else claves[0]
    titulo = f"{nucleo.capitalize()}: lo que pedía el chat"
    if propia:
        # «no se ponía de acuerdo» solo si de verdad había dos cosas en disputa
        if len(claves) >= 2:
            titulo = f"{nucleo.capitalize()}: el chat no se ponía de acuerdo"
        return titulo
    if streamer:
        titulo = f"{nucleo.capitalize()} — el chat de {streamer} no se ponía de acuerdo"
    return titulo


def elegir_cita(
    lineas: list[str], tokens: list[Token], maximo: int = 90, max_lineas: int = 4,
    min_diversidad: float = 0.75,
) -> str | None:
    """La frase transcripta que mejor resume el momento.

    No se busca entre las líneas sueltas sino entre **tiradas de líneas
    consecutivas**: las líneas de subtítulo están cortadas a 22 caracteres para que
    se lean en pantalla, así que una sola casi nunca es una frase. Juntando hasta
    cuatro sale algo citable ("le puedo decir que se vaya a la mierda") en vez de un
    pedazo ("a malas le puedo decir").
    """
    claves = {sin_tildes(t.palabra) for t in tokens}
    mejor, puntaje_mejor = None, 0.0
    for i in range(len(lineas)):
        for n in range(1, max_lineas + 1):
            if i + n > len(lineas):
                break
            frase = " ".join(lineas[i:i + n])
            palabras = [sin_tildes(w) for w in frase.split()]
            if len(palabras) < 3 or len(frase) > maximo:
                break
            if _es_coreo(palabras):
                continue
            # claves DISTINTAS, no apariciones: si no, "romance romance" le gana a
            # una frase entera solo por repetir la misma palabra dos veces
            puntaje = len(claves & set(palabras)) + 0.04 * len(palabras)
            if puntaje > puntaje_mejor:
                mejor, puntaje_mejor = frase, puntaje
    return _recortar_colgantes(mejor) if puntaje_mejor >= 1 else None


def _es_coreo(palabras: list[str]) -> bool:
    """¿La frase repite una palabra más de lo que su largo justifica?

    Cuando el chat corea y el streamer lo lee en voz alta, la transcripción queda
    «romance mátala creo que hay más mátala». Eso no se puede usar como cita, pero
    cuesta distinguirlo: su proporción de palabras distintas es 0,86, igual que la de
    una frase normal como «puedo poner a malas le puedo decir que se vaya a la
    mierda», que también repite una palabra.

    Lo que las separa es la **densidad de repetición**: cuántas palabras sobran en
    total, no cuánto se repite la peor. Mirar solo la más repetida dejaba pasar
    «mátala abuela mátala abuela le puedo decir que se vaya a la mierda», donde
    ninguna llega a tres pero hay dos pares.

    Se permite una repetición cada diez palabras. Y se miran solo las palabras con
    contenido: que «que» o «la» se repitan no dice nada del texto.
    """
    con_contenido = [w for w in palabras if w not in CONECTORES_SIN_TILDE]
    sobrantes = len(con_contenido) - len(set(con_contenido))
    return sobrantes > len(palabras) // 10


def _recortar_colgantes(frase: str | None) -> str | None:
    """Saca las palabras funcionales de los extremos de la cita.

    Es la misma idea que el corte de líneas de los subtítulos, con un paso más: no
    alcanza con sacar la última palabra si es funcional, porque "...a la mierda y
    encima" termina en una palabra con contenido y igual queda a medias. Primero se
    corta la cola que arranca en una conjunción, y después se limpian los extremos.
    """
    if not frase:
        return frase
    palabras = frase.split()
    for j in range(len(palabras) - 1, max(2, len(palabras) - 4), -1):
        if sin_tildes(limpio(palabras[j])) in CONJUNCIONES:
            palabras = palabras[:j]
            break
    while len(palabras) > 3 and es_funcional(palabras[-1]):
        palabras.pop()
    while len(palabras) > 3 and es_funcional(palabras[0]):
        palabras.pop(0)
    return " ".join(palabras)


def hashtags(etiqueta: str = "", streamer: str | None = None, juego: str | None = None,
             tope: int = 8, voz: str = "tercero") -> list[str]:
    base = HASHTAGS_BASE_PROPIA if _validar_voz(voz) == "propia" else HASHTAGS_BASE
    etiquetas = HASHTAGS_POR_TIPO.get(etiqueta, [])
    # el nombre del streamer va también en voz propia: es su marca, y es el hashtag
    # por el que lo busca su comunidad
    extra = [re.sub(r"[^a-z0-9]", "", sin_tildes(x)) for x in (streamer, juego) if x]
    vistos, salida = set(), []
    for h in extra + etiquetas + base:
        if h and h not in vistos:
            vistos.add(h)
            salida.append(h)
    return salida[:tope]


@dataclass
class Metadatos:
    titulo: str
    descripcion: str
    hashtags: list[str] = field(default_factory=list)
    cita: str | None = None


def armar(
    tema: list[str], etiqueta: str, url_fuente: str, segundo: int,
    lineas: list[str] | None = None, streamer: str | None = None,
    juego: str | None = None, voz: str = "tercero",
) -> Metadatos:
    propia = _validar_voz(voz) == "propia"
    vocab = {w.strip(".,¿?¡!").lower() for l in (lineas or []) for w in l.split()}
    # se consolida una sola vez y se usa para las dos cosas: asi la cita se busca
    # con la ortografia corregida, que es la que aparece en la transcripcion
    tokens = consolidar(parsear_tema(tema), vocab)
    titulo = titular_desde(tokens, etiqueta, streamer, voz=voz)
    cita = elegir_cita(lineas or [], tokens)

    partes = []
    if cita:
        partes.append(f"«{cita}»")
    # El credito no es decorativo: son clips de contenido de otra persona, y muchos
    # streamers piden atribucion y link a la fuente en sus reglas de canal.
    # En su propia cuenta no hay a quién darle crédito, y el link deja de ser una
    # atribución para ser una invitación a ver el directo.
    if propia:
        partes.append(f"El directo completo: {_con_tiempo(url_fuente, segundo)}")
    else:
        if streamer:
            partes.append(f"Clip del stream de {streamer}.")
        partes.append(f"Fuente: {_con_tiempo(url_fuente, segundo)}")
    return Metadatos(titulo=titulo, descripcion="\n\n".join(partes),
                     hashtags=hashtags(etiqueta, streamer, juego, voz=voz), cita=cita)


def _con_tiempo(url: str, segundo: int) -> str:
    if "twitch.tv" in url:
        s = int(segundo)
        return f"{url.split('?')[0]}?t={s // 3600}h{(s % 3600) // 60:02d}m{s % 60:02d}s"
    return f"{url}{'&' if '?' in url else '?'}t={int(segundo)}s"


# --- Gancho ---------------------------------------------------------------
#
# Lo que decide si el clip se ve es el primer segundo y medio. Las guías de
# retención coinciden en tres cosas: el gancho va en los primeros 3 segundos, tiene
# que leerse SIN SONIDO (mucha gente mira en mudo) y no pasa de 6-8 palabras.
#
# El gancho se arma con las mismas palabras que el chat repitió, que son justo las
# que generaron la reacción. No hay que inventar nada.

GANCHOS = {
    "duda": "¿{a} o {b}?",
    "eleccion": "El chat eligió: {a}",
    "reaccion": "El chat explotó con esto",
    "epico": "Mirá cómo termina",
    "gracioso": "No pudo aguantarse",
    "tension": "Se puso incómodo",
}
TONO_POR_ETIQUETA = {
    "épico": "epico", "gracioso": "gracioso", "tensión": "tension",
    "bajón": "reaccion",
}


def gancho(tema: list[str], etiqueta: str = "",
           vocabulario_audio: set[str] | None = None, max_palabras: int = 8) -> str:
    """Texto corto para el primer segundo y medio del clip."""
    tokens = [t for t in consolidar(parsear_tema(tema), vocabulario_audio)
              if sin_tildes(t.palabra) not in RUIDO and len(t.palabra) > 2]
    if len(tokens) >= 2:
        texto = GANCHOS["duda"].format(a=tokens[0].palabra, b=tokens[1].palabra)
    elif tokens:
        texto = GANCHOS["eleccion"].format(a=tokens[0].palabra)
    else:
        texto = GANCHOS.get(TONO_POR_ETIQUETA.get(etiqueta, ""), GANCHOS["reaccion"])
    palabras = texto.split()
    if len(palabras) > max_palabras:
        texto = " ".join(palabras[:max_palabras])
    return _mayuscula_inicial(texto)


def _mayuscula_inicial(t: str) -> str:
    """Busca la primera letra, salteando los signos de apertura ("¿mátala...").

    Si antes de la letra hay un número, no se toca: el nick "8cho" quedaba "8Cho".
    """
    for i, c in enumerate(t):
        if c.isdigit():
            return t
        if c.isalpha():
            return t[:i] + c.upper() + t[i + 1:]
    return t


def claves_del_tema(tema: list[str], vocabulario_audio: set[str] | None = None) -> set[str]:
    """Las palabras a resaltar en los subtítulos, normalizadas para comparar."""
    return {sin_tildes(t.palabra)
            for t in consolidar(parsear_tema(tema), vocabulario_audio)
            if len(t.palabra) > 2 and sin_tildes(t.palabra) not in RUIDO}
