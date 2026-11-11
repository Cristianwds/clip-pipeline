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
    """
    from .cortar import cortar

    # se valida antes de bajar nada: un nombre mal escrito no tiene que costar un corte
    variantes = [variante_para(primero + i, p.etiqueta, variante) for i, p in enumerate(picos)]
    hechos: list[Producido] = []
    for i, (pico, v) in enumerate(zip(picos, variantes)):
        log(f"  clip {primero + i + 1}: variante «{v.nombre}» ({v.duracion:.0f}s)")
        cortes = cortar(url, [pico], duraciones=[(v.antes, v.despues)],
                        carpeta=carpeta, primero=primero + i + 1)
        if not cortes:
            continue
        r = Producido(pico=pico, variante=v.nombre, final=cortes[0])

        if formato != "nada":
            from .reencuadre import reencuadrar
            log(f"  reencuadrando {r.final.name} a 9:16 ({formato})...")
            try:
                r.final = reencuadrar(r.final, modo=formato, facecam=facecam)
                log(f"    -> {r.final.name}")
            except Exception as e:
                r.avisos.append(f"reencuadre: {e}")
                log(f"    ! falló: {e}")

        if subtitulos:
            from .subtitulos import preparar, rematar
            log(f"  subtitulando {r.final.name} (modelo {modelo})...")
            try:
                # primero la transcripción: el gancho y el resaltado se arman con
                # el audio, que es el que tiene la ortografía correcta
                objetos, _ = preparar(r.final, modelo, idioma, v.max_chars)
                r.lineas = [l.texto for l in objetos]
                vocab = {w.strip(".,¿?¡!").lower() for l in r.lineas for w in l.split()}
                claves = metadatos.claves_del_tema(pico.tema, vocab)
                if v.gancho and not sin_gancho:
                    r.gancho = metadatos.gancho(pico.tema, pico.etiqueta, vocab)
                    log(f"    gancho: {r.gancho}")
                r.final = rematar(
                    r.final, objetos, cuerpo=v.cuerpo, margen_abajo=v.margen_abajo,
                    claves=claves, color_clave=v.color_clave, gancho=r.gancho)
                log(f"    -> {r.final.name} ({len(r.lineas)} líneas)")
            except Exception as e:
                r.avisos.append(f"subtítulos: {e}")
                log(f"    ! falló: {e}")
        hechos.append(r)
    return hechos
