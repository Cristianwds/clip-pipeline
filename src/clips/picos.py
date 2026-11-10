"""Detección de picos de actividad del chat y ranking de los candidatos.

La hipótesis a validar: cuando pasa algo bueno, el chat se acelera, y el pico de
mensajes por segundo coincide con el highlight.

Tres problemas que hay que resolver para que esto sirva de verdad:

1. La tasa cruda depende de cuánta gente está mirando, y eso cambia a lo largo del
   stream. Se normaliza contra una mediana móvil: lo que importa no es "cuántos
   mensajes hay" sino "cuántos más que lo normal para este momento".

2. Un chat es un proceso de conteo: aun sin que pase nada, la cantidad de mensajes
   por bin fluctúa. Con un umbral ingenuo de 3 sigmas el detector devuelve diez
   "highlights" en un stream donde no pasó nada. Se arregla con la transformación de
   Anscombe, que deja el ruido con varianza 1 independientemente de la tasa base, y
   con un umbral corregido por la cantidad de bins (si mirás 1500 ventanas, algunas
   se pasan de 3 sigmas por puro azar).

3. El volumen ordena mal. El score mide CUÁNTO chat hay; para elegir clips importa
   más CUÁNTO SE PONEN DE ACUERDO, que es el foco que calcula vocabulario.py. En la
   prueba real el score ponía primera la despedida del stream y el foco ponía primero
   el momento que el chat aplaudió. Por eso el ranking usa los dos.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import vocabulario
from .chat import Mensaje

# Tipos de momento que nunca son un clip, por mas que el chat se acelere.
ARTEFACTOS = {"cierre"}


@dataclass
class Pico:
    t: float                          # segundo del VOD donde está el pico
    score: float                      # sigmas por encima de lo normal (ruido ya descontado)
    tasa: float                       # mensajes por segundo en la ventana
    tema: list[str] = field(default_factory=list)      # el asunto de este momento
    reaccion: list[str] = field(default_factory=list)  # cómo reacciona ese chat siempre
    foco: float = 0.0                 # qué fracción del chat converge en el tema
    etiqueta: str = ""                # tipo de momento (gracioso, épico, cierre...)

    @property
    def es_artefacto(self) -> bool:
        return self.etiqueta in ARTEFACTOS

    @property
    def prioridad(self) -> float:
        """Score modulado por el foco: de media a una vez y media según el acuerdo.

        No se multiplica por el foco directo porque un pico sin tema identificable
        igual puede ser un clip (un minuto entero de risas no tiene "asunto"), así
        que el foco ajusta pero no anula.
        """
        return self.score * (0.5 + self.foco)

    def hhmmss(self) -> str:
        s = int(self.t)
        return f"{s // 3600:d}h{(s % 3600) // 60:02d}m{s % 60:02d}s"


def _mediana_movil(x: np.ndarray, ancho: int) -> tuple[np.ndarray, np.ndarray]:
    """Mediana y MAD móviles, centradas. Ancho en cantidad de bins."""
    ancho = max(3, ancho | 1)  # impar
    pad = ancho // 2
    xp = np.pad(x, pad, mode="edge")
    ventanas = np.lib.stride_tricks.sliding_window_view(xp, ancho)
    med = np.median(ventanas, axis=1)
    mad = np.median(np.abs(ventanas - med[:, None]), axis=1)
    return med, mad


def umbral_automatico(n_bins: int, alpha: float = 0.01) -> float:
    """Umbral en sigmas para que la probabilidad de UN falso positivo en todo el
    stream sea alpha. Corrección de Bonferroni sobre la cantidad de ventanas."""
    p = alpha / max(n_bins, 1)
    lo, hi = 0.0, 10.0
    for _ in range(80):  # binaria sobre la cola de la normal
        mid = (lo + hi) / 2
        if 0.5 * math.erfc(mid / math.sqrt(2)) > p:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


VENTANA_BASE = 5.0      # segundos; la que se usó en todas las pruebas con chats grandes
VENTANA_MAXIMA = 30.0
MENSAJES_POR_VENTANA = 4.0
BINS_BASE_MINIMOS = 60


def ventana_sugerida(mensajes: list[Mensaje]) -> float:
    """Tamaño de bin para que en una ventana normal haya unos 4 mensajes.

    Con 5 segundos fijos, un chat de 0,15 msg/s tiene menos de un mensaje por
    ventana, y ahí pasan dos cosas: Anscombe deja de estabilizar la varianza (la
    aproximación vale para medias de 4 o más) y una reacción de 12 mensajes
    repartidos en 20 segundos nunca junta lo suficiente en un solo bin. En la prueba
    con canales medianos, cinco VODs de 0,13 a 0,46 msg/s dieron entre 0 y 1 pico.

    Se ensancha la ventana en vez de bajar el umbral, porque el umbral es lo que
    impide inventar picos: una ventana más ancha solo junta la misma evidencia en
    menos pedazos. Para chats de más de 0,8 msg/s da 5 s, o sea que todo lo ya
    validado con chats grandes no cambia.

    Va en múltiplos de 5 y con tope de 30: más que eso ya no ubica el momento.
    """
    if len(mensajes) < 2 or mensajes[-1].t <= 0:
        return VENTANA_BASE
    tasa = len(mensajes) / mensajes[-1].t
    cruda = MENSAJES_POR_VENTANA / tasa
    redonda = math.ceil(cruda / VENTANA_BASE - 1e-9) * VENTANA_BASE
    return float(min(max(redonda, VENTANA_BASE), VENTANA_MAXIMA))


def _afinar(t: np.ndarray, inicio: float, fin: float, paso: float = VENTANA_BASE) -> float:
    """Dentro de una ventana ancha, el centro del tramo de `paso` segundos con más
    mensajes. Una ventana de 30 s detecta el pico pero lo ubica con ±15 s de error,
    y el clip se corta con 4 a 9 segundos de entrada: sin afinar, el momento puede
    quedar afuera."""
    mejor, cuenta_mejor = inicio, -1
    for x in np.arange(inicio, fin - paso + 1e-9, 1.0):
        cuenta = int(np.searchsorted(t, x + paso) - np.searchsorted(t, x))
        if cuenta > cuenta_mejor:
            mejor, cuenta_mejor = x, cuenta
    return float(mejor + paso / 2)


def detectar(
    mensajes: list[Mensaje],
    ventana: float = 5.0,
    base_seg: float = 600.0,
    umbral: float | None = None,
    separacion: float = 90.0,
    borde_inicio: float = 600.0,
    borde_fin: float = 0.0,
    top: int = 10,
    ordenar: str = "prioridad",
) -> list[Pico]:
    """Devuelve los picos ordenados por el criterio pedido.

    ventana:      tamaño del bin en segundos
    base_seg:     ancho de la mediana móvil que define "lo normal"
    umbral:       score mínimo en sigmas; None = automático según la cantidad de bins
    separacion:   segundos mínimos entre picos, para no sacar diez clips del mismo momento
    borde_inicio: segundos a ignorar al arranque (ahí está siempre el "¿se escucha?")
    borde_fin:    segundos a ignorar al final; 0 por defecto, ver el comentario abajo
    top:          cuántos devolver
    ordenar:      "prioridad" (score y foco), "score" o "foco"
    """
    if not mensajes:
        return []

    t = np.array([m.t for m in mensajes])
    bordes_bin = np.arange(0, t.max() + ventana, ventana)
    cuenta, _ = np.histogram(t, bins=bordes_bin)
    if len(cuenta) > 1:
        cuenta, bordes_bin = cuenta[:-1], bordes_bin[:-1]  # el último bin está incompleto
    if len(cuenta) < 3:
        return []

    # Anscombe: deja el ruido de conteo con varianza ~1 para cualquier tasa base
    y = 2.0 * np.sqrt(cuenta + 0.375)
    # La línea de base necesita bins suficientes, no solo segundos: con ventanas de
    # 30 s, 10 minutos son 20 bins y el MAD estimado sobre 20 valores salía inflado
    # la mitad de las veces, tapando picos reales. Con 5 y 10 s esto no cambia nada.
    med_y, mad_y = _mediana_movil(y, max(int(base_seg / ventana), BINS_BASE_MINIMOS))
    sigma = np.maximum(1.4826 * mad_y, 1.0)  # el piso teórico del ruido es 1
    score = (y - med_y) / sigma

    if umbral is None:
        umbral = umbral_automatico(len(cuenta))

    tasa = cuenta / ventana
    fin = float(bordes_bin[-1] + ventana)

    # Se juntan mas candidatos que los que se van a devolver, porque el orden final lo
    # decide el foco y todavia no se conoce. El tope evita analizar un stream entero.
    cupo = max(3 * top, 30)
    candidatos: list[Pico] = []
    for i in np.argsort(score)[::-1]:
        if score[i] < umbral or len(candidatos) >= cupo:
            break
        centro = bordes_bin[i] + ventana / 2
        # El arranque del stream siempre tiene el "¿se escucha?" y nunca es un clip:
        # se descarta por tiempo. El final NO, aunque ahi este la despedida: en la
        # prueba con el VOD de ibai, filtrar los ultimos 5 min mataba la despedida
        # pero tambien el climax del partido, que estaba 2 min antes. El final de un
        # stream suele ser justo despues del mejor momento, asi que la despedida se
        # descarta por lo que dice el chat (etiqueta "cierre"), no por donde cae.
        if centro < borde_inicio or (borde_fin and centro > fin - borde_fin):
            continue
        if any(abs(centro - p.t) < separacion for p in candidatos):
            continue
        candidatos.append(Pico(t=float(centro), score=float(score[i]), tasa=float(tasa[i])))

    if not candidatos:
        return []

    if ventana > VENTANA_BASE:
        for p in candidatos:
            p.t = _afinar(t, p.t - ventana / 2, p.t + ventana / 2)

    for p, a in zip(candidatos, vocabulario.analizar_picos(
            mensajes, [p.t for p in candidatos], ventana)):
        p.tema, p.reaccion, p.foco, p.etiqueta = a.tema, a.reaccion, a.foco, a.etiqueta

    claves = {
        "prioridad": lambda p: (p.es_artefacto, -p.prioridad),
        "score": lambda p: (p.es_artefacto, -p.score),
        "foco": lambda p: (p.es_artefacto, -p.foco),
    }
    if ordenar not in claves:
        raise ValueError(f"ordenar debe ser uno de {sorted(claves)}, no {ordenar!r}")
    # los artefactos conocidos (la despedida) van al final, no importa que score tengan
    candidatos.sort(key=claves[ordenar])
    return candidatos[:top]
