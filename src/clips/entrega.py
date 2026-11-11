"""El paquete que se le manda al streamer: los clips aprobados de un directo.

El modelo del proyecto es un servicio: los clips los sube el streamer a sus cuentas.
`publicar.py` deja un `.txt` por plataforma pensado para que los pegue quien opera el
pipeline; lo que hace falta acá es otra cosa, una carpeta que se pueda mandar tal
cual a alguien que no conoce el pipeline ni tiene por qué: los videos con un nombre
que se entienda, el texto de cada uno al lado y un LEEME que diga qué es.

Igual que `publicar.py` y el panel, **no publica ni manda nada**: arma la carpeta y
el zip, y para.

Solo entran los clips con el control en «ok». El veredicto del pico no alcanza: ese
se pone antes de producir, mirando el VOD.

Trabaja sobre los dicts del estado y no importa `lote`: es `lote` (o el panel) el
que anota después qué se entregó.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from .texto import sin_tildes

MAX_NOMBRE = 48


def seleccionar(vod: dict) -> list[dict]:
    """Los clips listos para mandar: producidos, con visto bueno y sin entregar.
    En el orden del directo, que es como los va a reconocer el streamer."""
    listos = [p for p in vod.get("picos", [])
              if p.get("clip") and p.get("control") == "ok" and not p.get("entregado")]
    return sorted(listos, key=lambda p: p["t"])


def nombre_base(n: int, titulo: str) -> str:
    """'01_romance-vs-malas'. Sin tildes ni símbolos: el archivo va a pasar por
    WhatsApp, Drive y un celular, y cada uno rompe los nombres a su manera."""
    limpio = re.sub(r"[^a-z0-9]+", "-", sin_tildes(titulo or "").lower()).strip("-")
    limpio = limpio[:MAX_NOMBRE].rstrip("-")
    return f"{n:02d}_{limpio}" if limpio else f"{n:02d}_clip"


def texto_del_clip(pico: dict) -> str:
    """El texto para copiar y pegar, una sección por plataforma."""
    pub = pico.get("publicacion") or {}
    partes = []
    for clave, datos in (pub.get("plataformas") or {}).items():
        cuerpo = [f"=== {datos['nombre']} ==="]
        # En X el texto es uno solo: no hay un título aparte que pegar.
        if clave != "x" and datos.get("titulo"):
            cuerpo.append(datos["titulo"])
            cuerpo.append("")
        cuerpo.append(datos.get("descripcion", "").strip())
        if datos.get("problemas"):
            cuerpo += ["", "OJO, en esta plataforma no entra así: " + "; ".join(datos["problemas"])]
        partes.append("\n".join(cuerpo))
    return "\n\n\n".join(partes).strip() + "\n"


def leeme(vod: dict, elegidos: list[tuple[str, dict]], fecha: datetime, permiso: str = "") -> str:
    """elegidos: (nombre de archivo sin extensión, pico)."""
    n = len(elegidos)
    lineas = [
        f"Clips del directo de {vod.get('streamer') or 'tu canal'}",
        *([vod["titulo"]] if vod.get("titulo") else []),
        vod["url"],
        f"Armado el {fecha:%d/%m/%Y} · {n} clip{'s' if n != 1 else ''}",
        "",
        "Cada video tiene al lado un .txt con el mismo nombre: es el título y el texto",
        "sugeridos para cada plataforma, listos para copiar y pegar.",
        "",
    ]
    # La frase que el streamer busca primero cuando le llegan clips suyos hechos por
    # otro. Solo se afirma si es verdad para todos los clips del paquete.
    if not any(p.get("publicado") for _, p in elegidos):
        lineas += ["Ninguno de estos clips se publicó en ningún lado.", ""]
    if permiso and permiso != "concedido":
        lineas += ["Es una muestra: no se publica nada sin tu visto bueno.", ""]
    for nombre, p in elegidos:
        pub = p.get("publicacion") or {}
        seg = (pub.get("medidas") or {}).get("segundos")
        lineas.append(f"{nombre}.mp4")
        lineas.append(f"    {pub.get('titulo') or '(sin título)'}")
        lineas.append(f"    minuto {p['momento']} del directo"
                      + (f" · {seg:.0f} s" if seg else ""))
        lineas.append("")
    return "\n".join(lineas).rstrip() + "\n"


def _carpeta_libre(base: Path, fecha: datetime) -> Path:
    """Dos entregas del mismo directo en el mismo día no se pisan."""
    carpeta = base / f"entrega_{fecha:%Y-%m-%d}"
    n = 2
    while carpeta.exists() or carpeta.with_suffix(".zip").exists():
        carpeta = base / f"entrega_{fecha:%Y-%m-%d}_{n}"
        n += 1
    return carpeta


def armar(vod: dict, salida: Path, ahora: datetime | None = None, permiso: str = "") -> dict:
    """Arma la carpeta y el zip. Devuelve {paquete, carpeta, tiempos, archivos}, con
    las rutas relativas a `salida`.

    Si falta el archivo de algún clip no se arma nada: un paquete al que le falta un
    video sin avisar es peor que no mandar el paquete.
    """
    if permiso == "negado":
        raise ValueError(f"{vod.get('streamer') or 'este streamer'} negó el permiso: "
                         "no se le arma una entrega")
    picos = seleccionar(vod)
    if not picos:
        raise ValueError("no hay clips con el visto bueno sin entregar en este directo")
    faltan = [p["clip"] for p in picos if not (salida / p["clip"]).is_file()]
    if faltan:
        raise RuntimeError("faltan en disco: " + ", ".join(faltan))

    fecha = ahora or datetime.now()
    carpeta = _carpeta_libre(salida / vod["id"] / "entregas", fecha)
    carpeta.mkdir(parents=True)
    elegidos = []
    for n, p in enumerate(picos, 1):
        nombre = nombre_base(n, (p.get("publicacion") or {}).get("titulo", ""))
        shutil.copy2(salida / p["clip"], carpeta / f"{nombre}.mp4")
        (carpeta / f"{nombre}.txt").write_text(texto_del_clip(p), encoding="utf-8")
        elegidos.append((nombre, p))
    (carpeta / "LEEME.txt").write_text(leeme(vod, elegidos, fecha, permiso), encoding="utf-8")
    paquete = Path(shutil.make_archive(str(carpeta), "zip", carpeta.parent, carpeta.name))
    return {
        "paquete": str(paquete.relative_to(salida)), "carpeta": str(carpeta.relative_to(salida)),
        "tiempos": [p["t"] for p in picos], "archivos": [f"{n}.mp4" for n, _ in elegidos],
    }
