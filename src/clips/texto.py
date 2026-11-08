"""Utilidades de texto compartidas. No depende de ningún otro módulo del proyecto.

Está acá porque tres módulos necesitaban lo mismo y se estaban importando cosas
privadas entre sí (`metadatos` usaba `subtitulos._es_funcional`). Con esto cada uno
depende de este archivo y no del otro.

- `vocabulario` lo usa para tokenizar el chat
- `subtitulos` para decidir dónde cortar una línea
- `metadatos` para armar títulos y citas
"""
from __future__ import annotations

import re
import unicodedata

TOKEN = re.compile(r"[\wáéíóúñü<3]+", re.UNICODE)
ALARGUE = re.compile(r"(.)\1{2,}")   # "siiii" -> "si", "romanceee" -> "romance"
PUNTUACION = ".,!?¡¿\"';:…"

# Conectores del castellano, para que no se cuelen como "tema" de un pico.
CONECTORES = {
    "de", "que", "el", "la", "los", "las", "un", "una", "y", "a", "en", "es", "lo",
    "al", "del", "por", "con", "para", "me", "te", "se", "su", "sus", "mi", "tu",
    "no", "si", "sí", "ya", "más", "mas", "pero", "como", "muy", "bien", "está",
    "esta", "este", "eso", "esa", "ese", "qué", "qui", "hay", "ha", "he", "son",
    "fue", "ser", "va", "ver", "le", "les", "nos", "yo", "él", "ella", "todo",
    "toda", "todos", "cuando", "donde", "porque", "aqui", "aquí", "ahi", "ahí",
    "hola", "the", "to", "is", "it", "of", "and",
}

# Palabras que no pueden quedar colgadas al final de una línea de subtítulo: el ojo
# espera lo que viene después. Es la diferencia entre "le puedo decir | que se vaya"
# y el corte feo "le puedo decir que se | vaya".
FUNCIONALES = CONECTORES | {
    "ante", "bajo", "contra", "desde", "entre", "hacia", "hasta", "según", "segun",
    "sin", "sobre", "tras", "e", "o", "u", "ni", "aunque", "mientras", "cual",
    "cuál", "quien", "quién", "os", "mis", "tus", "tan", "hemos", "han", "has",
    "voy", "vas", "vamos", "van", "soy", "era",
}

# Una cita que arrastra "... y encima" queda a medias: se corta en la conjunción.
CONJUNCIONES = {"y", "e", "o", "u", "ni", "pero", "que", "porque", "aunque", "si"}


def sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


def limpio(w: str) -> str:
    """Palabra sin puntuación alrededor y en minúscula."""
    return w.strip(PUNTUACION).lower()


def es_funcional(w: str) -> bool:
    return limpio(w) in FUNCIONALES


def tokenizar(texto: str) -> list[str]:
    """Normaliza el alargue de letras, que en el chat es masivo.

    Sin esto, 'siiii', 'siii' y 'si' cuentan como tres tokens distintos y el conteo
    del pico se parte en pedazos: en la prueba real el chat escribió 'romance' de
    cinco formas y ninguna llegaba al mínimo de apariciones.
    """
    return [ALARGUE.sub(r"\1", t) for t in TOKEN.findall(texto.lower()) if len(t) > 1]
