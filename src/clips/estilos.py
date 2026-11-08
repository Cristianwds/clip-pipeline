"""Variantes de formato, para que los clips no salgan todos iguales.

El problema que resuelve: un canal donde los 30 clips tienen exactamente el mismo
encuadre, la misma tipografía y el mismo texto es lo que las plataformas leen como
contenido producido en masa, y además cansa al que mira.

Cada variante cambia cosas que se notan: el largo, el layout, el tamaño y la
posición de los subtítulos, el color con que se resaltan las palabras clave, y si
lleva gancho arriba o no. La elección **rota con el índice del clip**, no es al azar:
así es reproducible y dos clips seguidos nunca salen iguales.

Los largos salen de las guías de retención de formato corto, que ubican el punto
dulce entre 15 y 30 segundos. Antes cortábamos 20 s antes del pico y 15 después: 35
segundos con la parte buena recién a la mitad.
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
    antes: float          # segundos de clip antes del pico
    despues: float        # y después
    cuerpo: int           # tamaño de la tipografía de los subtítulos
    margen_abajo: int
    color_clave: str      # con qué color se resaltan las palabras del tema
    gancho: bool          # si lleva el cartel de gancho arriba
    max_chars: int        # ancho de línea del subtítulo

    @property
    def duracion(self) -> float:
        return self.antes + self.despues


# El `antes` de cada variante es corto a proposito. El pico del chat NO es el
# instante del hecho: el chat reacciona uno o dos segundos despues, asi que el
# momento bueno ya esta un poco antes del centro de la ventana. Con 4-9 segundos de
# entrada alcanza para el contexto, y mas que eso deja el payoff a mitad del clip,
# que es justo lo que las guias de retencion dicen que no hay que hacer.
VARIANTES = [
    # corto y directo: el momento casi arriba del principio
    Variante("directo", antes=4, despues=14, cuerpo=68, margen_abajo=360,
             color_clave=AMARILLO, gancho=True, max_chars=20),
    # deja respirar un poco mas de contexto, sin pasarse
    Variante("contexto", antes=8, despues=18, cuerpo=60, margen_abajo=420,
             color_clave=VERDE, gancho=True, max_chars=24),
    # subtítulo grande y bajo, sin gancho: para momentos que se explican solos
    Variante("crudo", antes=5, despues=16, cuerpo=72, margen_abajo=300,
             color_clave=CELESTE, gancho=False, max_chars=18),
    # el más largo, para momentos con desarrollo
    Variante("desarrollo", antes=9, despues=19, cuerpo=62, margen_abajo=390,
             color_clave=AMARILLO, gancho=True, max_chars=22),
]


def elegir(indice: int, etiqueta: str = "") -> Variante:
    """Rota las variantes por índice, con un sesgo según el tipo de momento.

    Un momento de risa funciona cortado corto; uno con desarrollo necesita contexto.
    El sesgo mueve el punto de partida de la rotación, pero la rotación se mantiene
    para que un lote de clips nunca salga todo con el mismo formato.
    """
    sesgo = {"gracioso": 0, "épico": 2, "tensión": 1, "bajón": 3}.get(etiqueta, 0)
    return VARIANTES[(indice + sesgo) % len(VARIANTES)]
