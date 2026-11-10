"""Corta los segmentos del VOD alrededor de cada pico, sin bajar el VOD entero."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .picos import Pico

SALIDA = Path(__file__).resolve().parents[2] / "salida"


def _hms(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def cortar(
    url: str, picos: list[Pico], antes: float = 8.0, despues: float = 14.0,
    altura_max: int = 720, duraciones: list[tuple[float, float]] | None = None,
    carpeta: Path | None = None, primero: int = 1,
) -> list[Path]:
    """Baja solo el tramo de cada pico usando --download-sections de yt-dlp.

    duraciones: (antes, despues) por clip, para que cada variante de formato use su
    propio largo. Si no se pasa, todos usan el default.
    carpeta: dónde dejar los cortes. En modo lote cada VOD tiene la suya: con todo en
    salida/ los `pico_01` de dos VODs distintos quedaban mezclados.
    primero: número del primer clip, para que cortar de a uno conserve el puesto
    del pico en el nombre.
    """
    carpeta = carpeta or SALIDA
    carpeta.mkdir(parents=True, exist_ok=True)
    hechos = []
    for i, p in enumerate(picos):
        n = primero + i
        if duraciones and i < len(duraciones):
            antes, despues = duraciones[i]
        # el largo va en el nombre: si no, al cambiar de variante se reusaba el
        # corte anterior porque el archivo "ya existia"
        base = carpeta / f"pico_{n:02d}_{p.hhmmss()}_{int(antes + despues)}s"
        destino = base.with_suffix(".mp4")
        if destino.exists():
            hechos.append(destino)
            continue
        seccion = f"*{_hms(p.t - antes)}-{_hms(p.t + despues)}"
        cmd = [
            "yt-dlp", "--quiet", "--no-warnings",
            "--download-sections", seccion,
            "--force-keyframes-at-cuts",
            # Sin esto yt-dlp elige vp9/opus y escribe un .webm, ademas de pegarle la
            # extension real al nombre: queda "pico_01.mp4.webm" y el archivo que uno
            # cree que escribio no existe.
            "-f", f"bv*[height<={altura_max}]+ba/b[height<={altura_max}]",
            "-S", "vcodec:h264,acodec:aac,res",
            "--merge-output-format", "mp4",
            "-o", str(base) + ".%(ext)s", url,
        ]
        print(f"  cortando {p.hhmmss()} ...", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)

        # yt-dlp puede terminar en 0 y aun asi dejar el archivo con otra extension,
        # asi que el exito se decide mirando el disco, no el returncode.
        producidos = sorted(q for q in carpeta.glob(base.name + ".*")
                            if q.suffix.lower() not in (".part", ".ytdl"))
        if not producidos:
            detalle = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [f"rc={r.returncode}"]
            print(f"  ! falló {p.hhmmss()}: {detalle[0]}")
            continue
        real = producidos[0]
        if real != destino:
            real = real.replace(destino)
        hechos.append(destino)
    return hechos
