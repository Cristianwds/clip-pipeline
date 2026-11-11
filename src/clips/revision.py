"""Cuánto acierta el detector, medido contra veredictos humanos.

Hasta acá la evidencia era anecdótica: dos VODs y un clip confirmado mirando el
video. Para decidir si esto va a producción hace falta un número: de los primeros k
picos de cada VOD, cuántos son publicables.

Todo acá son funciones puras sobre filas (dicts). Una fila es un pico con su puesto
en el ranking del VOD y, si alguien lo miró, su veredicto.

Dos decisiones:

- **«Dudoso» cuenta como error** en la precisión estricta. La pregunta es cuántos
  clips se pueden publicar sin que un humano los rescate; un dudoso es justo el que
  necesita rescate. La laxa (bueno + dudoso) se informa aparte.
- **Siempre va con intervalo.** Con 10 VODs y 5 picos son 50 veredictos como mucho:
  «80%» sobre 10 revisados y sobre 50 no dicen lo mismo, y el número suelto invita
  a creerle de más. Se usa Wilson porque el intervalo normal se sale de [0, 1] y
  colapsa a ancho cero justo en los extremos, que con muestras chicas es lo común.
"""
from __future__ import annotations

import math

VEREDICTOS = ("bueno", "dudoso", "malo")


def wilson(aciertos: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Intervalo de Wilson al 95% para una proporción. None si no hay datos."""
    if n <= 0:
        return None
    p = aciertos / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    mitad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centro - mitad), min(1.0, centro + mitad))


def medir(filas: list[dict]) -> dict:
    """Cuenta veredictos y saca la precisión. Los picos sin revisar no entran en el
    denominador: no revisado no es malo."""
    cuenta = {v: sum(1 for f in filas if f.get("veredicto") == v) for v in VEREDICTOS}
    n = sum(cuenta.values())
    iv = wilson(cuenta["bueno"], n)
    return {
        "total": len(filas),
        "revisados": n,
        "buenos": cuenta["bueno"],
        "dudosos": cuenta["dudoso"],
        "malos": cuenta["malo"],
        "precision": cuenta["bueno"] / n if n else None,
        "precision_laxa": (cuenta["bueno"] + cuenta["dudoso"]) / n if n else None,
        "intervalo": list(iv) if iv else None,
    }


def en_k(filas: list[dict], k: int) -> list[dict]:
    """Solo los primeros k puestos de cada VOD."""
    return [f for f in filas if f.get("puesto", 0) and f["puesto"] <= k]


def por_grupo(filas: list[dict], clave: str) -> dict[str, dict]:
    grupos: dict[str, list[dict]] = {}
    for f in filas:
        grupos.setdefault(str(f.get(clave) or "—"), []).append(f)
    return {g: medir(fs) for g, fs in sorted(grupos.items())}


def por_tramo(filas: list[dict], campo: str, cortes: list[float]) -> list[dict]:
    """Precisión por tramos de un campo numérico (prioridad, foco, score).

    Sirve para la pregunta que sigue a «¿cuánto acierta?»: ¿hay un valor a partir
    del cual acierta casi siempre? Si lo hay, ese es el umbral para producir sin
    revisar.
    """
    bordes = [-math.inf, *sorted(cortes), math.inf]
    out = []
    for lo, hi in zip(bordes, bordes[1:]):
        dentro = [f for f in filas if lo <= float(f.get(campo) or 0) < hi]
        if lo == -math.inf:
            nombre = f"< {hi:g}"
        elif hi == math.inf:
            nombre = f"≥ {lo:g}"
        else:
            nombre = f"{lo:g} – {hi:g}"
        out.append({"tramo": nombre, **medir(dentro)})
    return out


def vods_sin_nada(filas: list[dict], k: int) -> int:
    """VODs con su top k revisado entero y ningún bueno. Es el peor caso para
    producción: se procesó un VOD completo y no salió nada publicable."""
    por_vod: dict[str, list[dict]] = {}
    for f in en_k(filas, k):
        por_vod.setdefault(f["vod"], []).append(f)
    return sum(
        1 for fs in por_vod.values()
        if all(f.get("veredicto") for f in fs) and not any(f["veredicto"] == "bueno" for f in fs)
    )


def resumen(filas: list[dict], k: int = 5) -> dict:
    top = en_k(filas, k)
    return {
        "k": k,
        "top_k": medir(top),
        "todos": medir(filas),
        "por_puesto": por_grupo(top, "puesto"),
        "por_etiqueta": por_grupo(filas, "etiqueta"),
        "por_streamer": por_grupo(top, "streamer"),
        "por_plataforma": por_grupo(top, "plataforma"),
        "por_prioridad": por_tramo(filas, "prioridad", [3, 4, 5, 7]),
        "por_foco": por_tramo(filas, "foco", [0.1, 0.25, 0.4]),
        "vods_sin_nada": vods_sin_nada(filas, k),
    }
