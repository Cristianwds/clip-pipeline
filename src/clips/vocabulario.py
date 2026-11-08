"""Qué está diciendo el chat en un pico, y si eso lo convierte en clip.

El problema que resuelve: mirar las palabras más repetidas de un pico casi no sirve.
En Twitch casi todos los picos dan `om, xdd, opa` y en YouTube dan `de, que, el`,
porque lo más frecuente de una ventana es simplemente lo más frecuente del chat.

Se mide en dos niveles, y la clave está en el segundo:

1. **Especificidad contra el stream.** Compara la frecuencia del token dentro del pico
   contra su frecuencia en todo el stream. Saca los conectores y lo rutinario.

       especificidad = (n_pico / N_pico) / (n_stream / N_stream)

2. **Frecuencia entre picos.** El nivel 1 no alcanza: durante *cualquier* pico la
   proporción de emotes sube, así que `xdd` también da especificidad alta y termina
   contándose como si fuera el asunto del momento. La corrección es mirar en cuántos
   picos se eleva cada token:

   - se eleva en **muchos** picos  -> es vocabulario de reacción (así reacciona ese chat)
   - se eleva en **uno solo**      -> es el asunto de ese momento

Es la misma idea que TF-IDF, con los picos como documentos. Y no hay ningún diccionario
de emotes que mantener: se adapta solo a cada streamer, idioma y comunidad.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .chat import Mensaje
from .texto import CONECTORES, tokenizar

# Mapa de emotes conocidos a un tipo de momento. Es a proposito PARCIAL: solo los mas
# universales, y sirve para ETIQUETAR, no para filtrar (de eso se encarga la frecuencia
# entre picos). Lo que no esta en el mapa se reporta con el token tal cual.
TIPO_POR_EMOTE = {
    "kekw": "gracioso", "lul": "gracioso", "lmao": "gracioso", "omegalul": "gracioso",
    "xdd": "gracioso", "xd": "gracioso", "jajaja": "gracioso", "kek": "gracioso",
    "pepelaugh": "gracioso", "haha": "gracioso", "jaja": "gracioso",
    "pog": "épico", "poggers": "épico", "pogchamp": "épico", "clap": "épico",
    "based": "épico", "gigachad": "épico", "hype": "épico", "ez": "épico",
    "sadge": "bajón", "pepehands": "bajón", "feelsbadman": "bajón", "f": "bajón",
    "monkas": "tensión", "stare": "tensión", "wtf": "tensión", "omg": "tensión",
    "what": "tensión", "wat": "tensión",
    "gg": "cierre", "bye": "cierre", "chau": "cierre", "adios": "cierre",
    "adiós": "cierre",
}


@dataclass
class Analisis:
    tema: list[str] = field(default_factory=list)       # el asunto de ESTE momento
    reaccion: list[str] = field(default_factory=list)   # como reacciona ese chat siempre
    foco: float = 0.0                                   # cuanto del chat converge en el tema
    etiqueta: str = ""                                   # tipo de momento, si se reconoce

    def resumen(self) -> str:
        partes = []
        if self.tema:
            partes.append("tema: " + ", ".join(self.tema))
        if self.reaccion:
            partes.append("reacción: " + ", ".join(self.reaccion))
        return " | ".join(partes) or "nada claro"


def perfil_global(mensajes: list[Mensaje]) -> tuple[Counter[str], int]:
    """Cuenta todo el vocabulario del stream: es la referencia del nivel 1."""
    c: Counter[str] = Counter()
    for m in mensajes:
        c.update(tokenizar(m.texto))
    return c, max(sum(c.values()), 1)


def _contar_ventanas(
    mensajes: list[Mensaje], centros: list[float], ventana: float
) -> list[Counter[str]]:
    """Una sola pasada por el chat, repartiendo cada mensaje en las ventanas que lo contienen."""
    locales: list[Counter[str]] = [Counter() for _ in centros]
    rangos = [(c - ventana, c + ventana) for c in centros]
    for m in mensajes:
        for i, (lo, hi) in enumerate(rangos):
            if lo <= m.t <= hi:
                locales[i].update(tokenizar(m.texto))
    return locales


def analizar_picos(
    mensajes: list[Mensaje],
    centros: list[float],
    ventana: float,
    min_apariciones: int = 3,
    umbral_especificidad: float = 3.0,
    fraccion_reaccion: float = 0.34,
    n: int = 4,
) -> list[Analisis]:
    """Analiza todos los picos juntos, que es lo que permite separar reacción de tema.

    fraccion_reaccion: en qué fracción de los picos tiene que elevarse un token para
    considerarlo vocabulario de reacción. Hay un trade-off medido con datos reales:
    bajarlo a 0.20 etiqueta mejor (manda `opa` y el nombre del streamer a reacción,
    donde van) pero **empeora el ranking**, porque en una watch-party el equipo del
    que se habla (`t1`) también aparece en varios picos y termina tratado como
    reacción. Con 0.34 el orden sale mejor, que es para lo que se usa el foco.
    """
    if not centros:
        return []
    global_c, global_n = perfil_global(mensajes)
    locales = _contar_ventanas(mensajes, centros, ventana)

    # Nivel 1: que tokens se elevan en cada pico
    elevados: list[dict[str, tuple[float, int]]] = []
    for local in locales:
        local_n = max(sum(local.values()), 1)
        d: dict[str, tuple[float, int]] = {}
        for w, k in local.items():
            if k < min_apariciones or w in CONECTORES:
                continue
            esp = (k / local_n) / max(global_c.get(w, 0) / global_n, 1e-9)
            if esp >= umbral_especificidad:
                d[w] = (esp, k)
        elevados.append(d)

    # Nivel 2: un token elevado en muchos picos es reaccion, no tema
    df: Counter[str] = Counter()
    for d in elevados:
        df.update(d.keys())
    # con un solo pico no hay con que comparar: se cae al nivel 1 solo
    umbral_df = max(2, math.ceil(fraccion_reaccion * len(centros))) if len(centros) > 1 else len(centros) + 1

    salida: list[Analisis] = []
    for local, d in zip(locales, elevados):
        local_n = max(sum(local.values()), 1)
        tema: list[tuple[float, str, int]] = []
        reaccion: list[tuple[int, str]] = []
        masa_tema = 0
        for w, (esp, k) in d.items():
            if df[w] >= umbral_df:
                reaccion.append((k, w))
            else:
                tema.append((esp, w, k))
                masa_tema += k
        # lo frecuente que no se elevo tambien es parte de como habla el chat
        for w, k in local.most_common():
            if k >= min_apariciones and w not in d and w not in CONECTORES:
                reaccion.append((k, w))
        tema.sort(reverse=True)
        reaccion.sort(reverse=True)

        etiqueta = ""
        for w in [w for _, w, _ in tema] + [w for _, w in reaccion]:
            if w in TIPO_POR_EMOTE:
                etiqueta = TIPO_POR_EMOTE[w]
                break
        if not etiqueta and reaccion:
            etiqueta = reaccion[0][1]

        salida.append(Analisis(
            tema=[f"{w}×{k}" for _, w, k in tema[:n]],
            reaccion=[f"{w}×{k}" for k, w in reaccion[:n]],
            foco=masa_tema / local_n,
            etiqueta=etiqueta,
        ))
    return salida


def analizar(
    mensajes: list[Mensaje], centro: float, ventana: float,
    global_c: Counter[str] | None = None, global_n: int | None = None, **kw,
) -> Analisis:
    """Un pico solo. Sin otros picos con los que comparar, solo corre el nivel 1."""
    return analizar_picos(mensajes, [centro], ventana, **kw)[0]
