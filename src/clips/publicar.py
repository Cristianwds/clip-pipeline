"""Etapa 5: arma el paquete de publicación de cada clip. **No publica nada.**

Lo que hace es dejar todo listo y validado: título, descripción, hashtags y horario
sugerido por plataforma, más los problemas que tendría cada clip en cada una. La
salida son dos archivos: un JSON para que lo consuma una herramienta, y un Markdown
para que lo leas vos.

Publicar es un paso aparte y a propósito: son clips de contenido de otra persona, y
la publicación automática y en volumen es justo lo que las plataformas penalizan. El
enchufe a Postiz está hecho (`paquete_postiz`) pero no manda nada por la red.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import metadatos
from .picos import Pico


@dataclass
class Plataforma:
    nombre: str
    max_titulo: int
    max_descripcion: int
    max_seg: float
    max_mb: float
    max_hashtags: int
    hora_sugerida: int          # hora local que suele rendir
    min_seg: float = 3.0


# OJO: estos limites los cambian las plataformas cada tanto. Estan centralizados acá
# justamente para poder corregirlos en un solo lugar cuando dejen de valer.
PLATAFORMAS = {
    "tiktok": Plataforma("TikTok", 2200, 2200, 600, 500, 10, 19),
    "youtube_shorts": Plataforma("YouTube Shorts", 100, 5000, 180, 2000, 15, 18),
    "instagram_reels": Plataforma("Instagram Reels", 2200, 2200, 180, 1000, 12, 20),
    # En X el texto es UNO solo y corto: 280 caracteres sin Premium. Por eso el titulo
    # y la descripcion comparten el mismo limite y los hashtags se recortan a 3, que
    # ademas es la convencion de la plataforma: llenar de hashtags rinde peor.
    "x": Plataforma("X", 280, 280, 140, 512, 3, 21),
}


@dataclass
class Medidas:
    segundos: float
    mb: float
    ancho: int
    alto: int

    @property
    def vertical(self) -> bool:
        return self.alto > self.ancho


def medir(clip: Path) -> Medidas:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries", "format=duration,size",
         "-of", "json", str(clip)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"no pude medir {clip.name}: {r.stderr.strip()[-200:]}")
    d = json.loads(r.stdout)
    v = (d.get("streams") or [{}])[0]
    f = d.get("format") or {}
    return Medidas(
        segundos=float(f.get("duration", 0)),
        mb=float(f.get("size", 0)) / 1_048_576,
        ancho=int(v.get("width", 0)),
        alto=int(v.get("height", 0)),
    )


def validar(m: Medidas, p: Plataforma) -> list[str]:
    """Los problemas que tendría este clip en esta plataforma. Vacío = listo para subir."""
    problemas = []
    if m.segundos > p.max_seg:
        problemas.append(f"dura {m.segundos:.0f}s y el máximo es {p.max_seg:.0f}s")
    if m.segundos < p.min_seg:
        problemas.append(f"dura {m.segundos:.1f}s, demasiado corto")
    if m.mb > p.max_mb:
        problemas.append(f"pesa {m.mb:.0f} MB y el máximo es {p.max_mb:.0f} MB")
    if not m.vertical:
        problemas.append(f"no es vertical ({m.ancho}x{m.alto}): se va a ver con bandas")
    return problemas


def recortar_texto(t: str, maximo: int) -> str:
    """Corta en el último espacio para no partir una palabra al medio."""
    if len(t) <= maximo:
        return t
    cortado = t[: maximo - 1]
    if " " in cortado:
        cortado = cortado[: cortado.rindex(" ")]
    return cortado + "…"


@dataclass
class Publicacion:
    clip: str
    momento: str
    prioridad: float
    titulo: str
    descripcion: str
    hashtags: list[str] = field(default_factory=list)
    cita: str | None = None
    medidas: dict = field(default_factory=dict)
    plataformas: dict = field(default_factory=dict)


def armar_publicacion(
    clip: Path, pico: Pico, url: str, lineas: list[str] | None = None,
    streamer: str | None = None, juego: str | None = None, voz: str = "tercero",
) -> Publicacion:
    meta = metadatos.armar(pico.tema, pico.etiqueta, url, int(pico.t),
                           lineas=lineas, streamer=streamer, juego=juego, voz=voz)
    m = medir(clip)
    por_plataforma = {}
    for clave, p in PLATAFORMAS.items():
        etiquetas = meta.hashtags[: p.max_hashtags]
        cuerpo = meta.descripcion + "\n\n" + " ".join("#" + h for h in etiquetas)
        por_plataforma[clave] = {
            "nombre": p.nombre,
            "titulo": recortar_texto(meta.titulo, p.max_titulo),
            "descripcion": recortar_texto(cuerpo, p.max_descripcion),
            "hashtags": etiquetas,
            "problemas": validar(m, p),
        }
    return Publicacion(
        clip=clip.name, momento=pico.hhmmss(), prioridad=round(pico.prioridad, 1),
        titulo=meta.titulo, descripcion=meta.descripcion, hashtags=meta.hashtags,
        cita=meta.cita, medidas=asdict(m), plataformas=por_plataforma,
    )


def planificar(
    publicaciones: list[Publicacion], desde: datetime | None = None,
    dias_entre_clips: int = 1,
) -> list[Publicacion]:
    """Agenda un clip por día y escalona las plataformas dentro del día.

    Publicar los cinco clips de golpe en las tres plataformas es el patrón que
    YouTube marca como contenido producido en masa. Separarlos cuesta cero y evita
    justo eso.
    """
    base = (desde or datetime.now()).replace(minute=0, second=0, microsecond=0)
    for i, pub in enumerate(publicaciones):
        dia = base + timedelta(days=i * dias_entre_clips)
        for clave, datos in pub.plataformas.items():
            hora = PLATAFORMAS[clave].hora_sugerida
            cuando = dia.replace(hour=hora)
            if cuando <= base:
                cuando += timedelta(days=1)
            datos["cuando"] = cuando.isoformat(timespec="minutes")
    return publicaciones


def paquete_postiz(pub: Publicacion, plataforma: str) -> dict:
    """El payload que habría que mandarle a Postiz. **No hace ninguna llamada.**

    Queda acá para que conectar sea cambiar una función y no reescribir la etapa:
    falta el id del canal (sale de la cuenta de Postiz) y subir el archivo.
    """
    datos = pub.plataformas[plataforma]
    return {
        "type": "schedule",
        "date": datos.get("cuando"),
        "shortLink": False,
        "posts": [{
            "integration": {"id": f"<id del canal de {datos['nombre']} en Postiz>"},
            "value": [{
                "content": f"{datos['titulo']}\n\n{datos['descripcion']}",
                "image": [{"path": pub.clip, "nota": "hay que subirlo primero"}],
            }],
        }],
    }


def escribir_textos(publicaciones: list[Publicacion], carpeta: Path) -> list[Path]:
    """Un .txt por clip y plataforma, con el texto exacto para copiar y pegar.

    Publicar a mano es abrir el archivo, copiar todo y pegarlo. Sin esto hay que ir
    al manifiesto, encontrar el bloque y recortarlo a ojo, que es justo donde se
    cuelan los errores (pegar el texto de TikTok en X y que se corte a la mitad).
    """
    destino = carpeta / "textos"
    destino.mkdir(parents=True, exist_ok=True)
    escritos = []
    for pub in publicaciones:
        base = Path(pub.clip).stem
        for clave, datos in pub.plataformas.items():
            f = destino / f"{base}.{clave}.txt"
            cuerpo = datos["descripcion"]
            # En X el texto es uno solo; en el resto el titulo va aparte.
            texto = cuerpo if clave == "x" else f"{datos['titulo']}\n\n{cuerpo}"
            f.write_text(texto.strip() + "\n", encoding="utf-8")
            escritos.append(f)
    return escritos


def escribir(publicaciones: list[Publicacion], carpeta: Path) -> tuple[Path, Path]:
    carpeta.mkdir(parents=True, exist_ok=True)
    escribir_textos(publicaciones, carpeta)
    js = carpeta / "plan_publicacion.json"
    js.write_text(json.dumps([asdict(p) for p in publicaciones], ensure_ascii=False,
                             indent=2), encoding="utf-8")
    md = carpeta / "plan_publicacion.md"
    md.write_text(_a_markdown(publicaciones), encoding="utf-8")
    return js, md


def _a_markdown(publicaciones: list[Publicacion]) -> str:
    out = ["# Plan de publicación", "",
           "> Generado por el pipeline. **Nada de esto se publicó**: es para revisar y",
           "> decidir. Revisá que el streamer permita clips antes de subir nada.", ""]
    for n, p in enumerate(publicaciones, 1):
        out += [f"## {n}. {p.titulo}", "",
                f"- **Clip:** `{p.clip}` ({p.momento}, prioridad {p.prioridad})",
                f"- **Duración:** {p.medidas['segundos']:.0f}s · "
                f"{p.medidas['ancho']}x{p.medidas['alto']} · {p.medidas['mb']:.1f} MB"]
        if p.cita:
            out.append(f"- **Cita:** «{p.cita}»")
        out += ["", "| Plataforma | Cuándo | Estado |", "| :--- | :--- | :--- |"]
        for datos in p.plataformas.values():
            estado = "✅ listo" if not datos["problemas"] else "⚠️ " + "; ".join(datos["problemas"])
            out.append(f"| {datos['nombre']} | {datos.get('cuando', '—')} | {estado} |")
        out += ["", f"Texto listo para pegar: `salida/textos/{Path(p.clip).stem}.<plataforma>.txt`",
                "", "<details><summary>Ver el texto</summary>", "",
                "```", p.descripcion, "", " ".join("#" + h for h in p.hashtags), "```",
                "", "</details>", ""]
    return "\n".join(out) + "\n"
