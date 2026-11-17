"""Variantes de formato, para que los clips no salgan todos iguales.

El problema que resuelve: un canal donde los 30 clips tienen exactamente el mismo
encuadre, la misma tipografía y el mismo texto es lo que las plataformas leen como
contenido producido en masa, y además cansa al que mira.

Cada variante cambia cosas que se notan: el tamaño y la posición de los subtítulos,
el color con que se resaltan las palabras clave, y si lleva gancho arriba o no. La
elección **rota con el índice del clip**, no es al azar: así es reproducible y dos
clips seguidos nunca salen iguales.

Las variantes **no definen el largo**. Lo hacían (de 18 a 28 s, entrando 4 a 9 s antes
del pico) y los clips arrancaban tarde: al transcribir tres picos, lo que había que ver
empezaba entre 12 y 28 s antes del centro. Un momento dura lo que dura, no lo que
diga la plantilla que le tocó. El tramo ahora sale de la ráfaga del chat
(`pipeline.tramo`).
"""
from __future__ import annotations

from dataclasses import dataclass

# En ASS el color va &HAABBGGRR: azul y rojo invertidos respecto de HTML.
AMARILLO = "&H0000D7FF"
VERDE = "&H0000FF7F"
CELESTE = "&H00FFD700"
BLANCO = "&H00FFFFFF"


@dataclass
class Variante:
    nombre: str
    cuerpo: int           # tamaño de la tipografía de los subtítulos
    margen_abajo: int
    color_clave: str      # con qué color se resaltan las palabras del tema
    gancho: bool          # si lleva el cartel de gancho arriba
    max_chars: int        # ancho de línea del subtítulo


# Los nombres vienen de cuando cada variante tenía su largo. Se conservan porque ya
# están guardados en el estado y en los clips producidos.
VARIANTES = [
    Variante("directo", cuerpo=68, margen_abajo=360,
             color_clave=AMARILLO, gancho=True, max_chars=20),
    Variante("contexto", cuerpo=60, margen_abajo=420,
             color_clave=VERDE, gancho=True, max_chars=24),
    # subtítulo grande y bajo, sin gancho: para momentos que se explican solos
    Variante("crudo", cuerpo=72, margen_abajo=300,
             color_clave=CELESTE, gancho=False, max_chars=18),
    Variante("desarrollo", cuerpo=62, margen_abajo=390,
             color_clave=AMARILLO, gancho=True, max_chars=22),
]


def elegir(indice: int, etiqueta: str = "") -> Variante:
    """Rota las variantes por índice, con un sesgo según el tipo de momento.

    El sesgo mueve el punto de partida de la rotación, pero la rotación se mantiene
    para que un lote de clips nunca salga todo con el mismo formato.
    """
    sesgo = {"gracioso": 0, "épico": 2, "tensión": 1, "bajón": 3}.get(etiqueta, 0)
    return VARIANTES[(indice + sesgo) % len(VARIANTES)]
