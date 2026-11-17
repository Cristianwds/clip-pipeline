"""Del pico al clip terminado: corte, reencuadre y subtítulos, un pico a la vez.

Esto vivía dentro de `cli.py`. Se sacó cuando apareció el modo lote, porque el panel
y el lote necesitan producir un clip sin pasar por argparse.

Va de a un pico y no por etapas (cortar todos, reencuadrar todos, subtitular todos)
por un motivo concreto: la versión por etapas emparejaba clips y picos con `zip`, y
si el corte del segundo fallaba, el tercer clip salía con el título y el gancho del
segundo pico. Acá cada pico arrastra su propio resultado y no hay nada que alinear.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import estilos, metadatos
from .picos import Pico


# El tramo del clip sale de la ráfaga del chat, no de la variante. Los números vienen
# de transcribir tres picos reales el 2026-11-16: lo que había que ver empezaba entre
# 12 y 28 s antes del centro, y el chat recién arrancaba unos 5 s antes. Son valores
# de partida: se ajustan mirando clips producidos.
MARGEN_ENTRADA = 18.0   # segundos antes de que arranque la ráfaga: el chat reacciona tarde
COLA = 4.0              # segundos después de que se apaga
ANTES_MINIMO = 15.0     # entrada que no se resigna aunque la reacción sea larga
DESPUES_MINIMO = 12.0   # el remate suele venir cuando el chat ya está bajando
TOPE = 40.0             # largo máximo del clip
# Para ajustar el inicio a un comienzo de frase hay que bajar un poco más de lo que
# se va a usar. Solo hacia atrás: adelantar el inicio no necesita material extra.
HOLGURA = 6.0
ADELANTE = 3.0


def tramo(pico: Pico) -> tuple[float, float]:
    """Cuántos segundos antes y después del pico entran en el clip.

    Si el clip se pasa del tope, se acorta la entrada y no la reacción: sin el
    remate no hay clip. Pero la entrada tiene un piso, porque un clip que arranca
    sobre la reacción es el problema que esto vino a arreglar.

    Un pico sin ráfaga medida (guardado antes de que existiera) se trata como una
    ráfaga de largo cero en el centro.
    """
    inicio, fin = (pico.inicio, pico.fin) if pico.fin else (pico.t, pico.t)
    antes = (pico.t - inicio) + MARGEN_ENTRADA
    despues = max((fin - pico.t) + COLA, DESPUES_MINIMO)
    if antes + despues > TOPE:
        antes = max(ANTES_MINIMO, TOPE - despues)
        despues = TOPE - antes
    return antes, despues


@dataclass
class Producido:
    pico: Pico
    variante: str
    final: Path                       # el archivo más terminado que se logró
    lineas: list[str] | None = None   # la transcripción, si hubo subtítulos
    gancho: str = ""
    avisos: list[str] = field(default_factory=list)   # etapas que fallaron


def variante_para(indice: int, etiqueta: str = "", forzada: str | None = None) -> estilos.Variante:
    if not forzada:
        return estilos.elegir(indice, etiqueta)
    v = next((x for x in estilos.VARIANTES if x.nombre == forzada), None)
    if v is None:
        raise ValueError(f"variante desconocida: {forzada}. Son: "
                         f"{', '.join(x.nombre for x in estilos.VARIANTES)}")
    return v


def producir(
    url: str, picos: list[Pico], *, carpeta: Path | None = None,
    formato: str = "desenfoque", facecam: tuple[int, int, int, int] | None = None,
    subtitulos: bool = False, modelo: str = "small", idioma: str | None = None,
    variante: str | None = None, sin_gancho: bool = False, primero: int = 0,
    log: Callable[[str], None] = print,
) -> list[Producido]:
    """Produce un clip por pico. Los que no se pudieron cortar no aparecen.

    primero: puesto del primer pico en el ranking del VOD (base 0). Define el número
    del archivo y por dónde arranca la rotación de variantes, así producir el pico 3
    suelto da el mismo clip que producir los tres primeros juntos.

    Una etapa que falla después del corte no tira el clip: queda el archivo de la
    etapa anterior y el motivo en `avisos`.

    El orden es corte, transcripción, reencuadre, subtítulos. Se transcribe antes de
    reencuadrar porque la transcripción decide dónde empieza el clip, y el reencuadre
    es el que recorta: así el ajuste no cuesta una codificación más.
    """
    from .cortar import cortar

    # se valida antes de bajar nada: un nombre mal escrito no tiene que costar un corte
    variantes = [variante_para(primero + i, p.etiqueta, variante) for i, p in enumerate(picos)]
    hechos: list[Producido] = []
    for i, (pico, v) in enumerate(zip(picos, variantes)):
        antes, despues = tramo(pico)
        # el inicio solo se puede ajustar si hay transcripción para saber dónde cae
        # la frase y reencuadre para recortar. Y nunca más allá del tope.
        holgura = min(HOLGURA, TOPE - (antes + despues)) if subtitulos and formato != "nada" else 0.0
        log(f"  clip {primero + i + 1}: variante «{v.nombre}», "
            f"{antes:.0f} s antes del pico y {despues:.0f} después")
        cortes = cortar(url, [pico], duraciones=[(antes + holgura, despues)],
                        carpeta=carpeta, primero=primero + i + 1)
        if not cortes:
            continue
        r = Producido(pico=pico, variante=v.nombre, final=cortes[0])

        palabras, desde = [], holgura
        if subtitulos:
            from . import subtitulos as mod_sub
            log(f"  transcribiendo {r.final.name} (modelo {modelo})...")
            try:
                palabras, _ = mod_sub.transcribir(r.final, modelo=modelo, idioma=idioma)
                if not palabras:
                    raise RuntimeError("la transcripción no devolvió palabras (¿el clip tiene audio?)")
                if formato != "nada":
                    desde = mod_sub.inicio_de_frase(palabras, holgura, atras=holgura,
                                                    adelante=ADELANTE)
                    if abs(desde - holgura) >= 0.05:
                        log(f"    el inicio se corre {desde - holgura:+.1f} s, a un comienzo de frase")
            except Exception as e:
                r.avisos.append(f"subtítulos: {e}")
                log(f"    ! falló: {e}")

        if formato != "nada":
            from .reencuadre import reencuadrar
            log(f"  reencuadrando {r.final.name} a 9:16 ({formato})...")
            try:
                r.final = reencuadrar(r.final, modo=formato, facecam=facecam, desde=desde)
                log(f"    -> {r.final.name}")
            except Exception as e:
                r.avisos.append(f"reencuadre: {e}")
                log(f"    ! falló: {e}")
                # el archivo que queda es el corte entero: las palabras no se corren
                desde = 0.0

        if palabras:
            log(f"  subtitulando {r.final.name}...")
            try:
                # el gancho y el resaltado se arman con el audio, que es el que
                # tiene la ortografía correcta
                objetos = mod_sub.agrupar(mod_sub.desplazar(palabras, desde),
                                          max_chars=v.max_chars)
                r.lineas = [l.texto for l in objetos]
                vocab = {w.strip(".,¿?¡!").lower() for l in r.lineas for w in l.split()}
                claves = metadatos.claves_del_tema(pico.tema, vocab)
                if v.gancho and not sin_gancho:
                    r.gancho = metadatos.gancho(pico.tema, pico.etiqueta, vocab)
                    log(f"    gancho: {r.gancho}")
                r.final = mod_sub.rematar(
                    r.final, objetos, cuerpo=v.cuerpo, margen_abajo=v.margen_abajo,
                    claves=claves, color_clave=v.color_clave, gancho=r.gancho)
                log(f"    -> {r.final.name} ({len(r.lineas)} líneas)")
            except Exception as e:
                r.avisos.append(f"subtítulos: {e}")
                log(f"    ! falló: {e}")
        hechos.append(r)
    return hechos
