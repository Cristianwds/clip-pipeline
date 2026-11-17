"""Corta los segmentos del VOD alrededor de cada pico, sin bajar el VOD entero."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .picos import Pico

SALIDA = Path(__file__).resolve().parents[2] / "salida"

# Tope para cortar un clip. El 2026-11-16 un corte quedó 17 minutos con el archivo en
# 0 bytes: la conexión se había muerto y nadie lo cortaba. En el panel hay un solo
# trabajador, así que un corte colgado frena toda la cola. Un corte de 70 s tarda menos
# de un minuto; esto deja margen para una conexión lenta.
TIEMPO_MAXIMO = float(os.environ.get("CLIP_PIPELINE_TIMEOUT_CORTE", 600))

# Los cuelgues vistos fueron de la conexión y no del VOD (los otros cortes de la misma
# tanda salieron bien), así que vale la pena probar de nuevo. Una sola vez: dos topes
# seguidos ya son 20 minutos.
INTENTOS = 2

_PARCIALES = (".part", ".ytdl")


def _correr(cmd: list[str], tope: float) -> subprocess.CompletedProcess:
    """Corre un comando y, si pasa el tope, lo mata junto con todo lo que lanzó.

    `subprocess.run(timeout=...)` mata solo al proceso directo. yt-dlp baja el tramo
    con un ffmpeg hijo, y ese ffmpeg es justo el que queda colgado: sobrevive a su
    padre y sigue con el archivo abierto. Por eso el comando va en un grupo propio y
    se mata el grupo.
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    try:
        salida, error = p.communicate(timeout=tope)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()            # junta al proceso para no dejar un zombi
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, salida, error)


def _borrar_restos(carpeta: Path, nombre: str) -> None:
    """Saca todo lo que dejó un intento que no terminó.

    No solo los `.part`: yt-dlp baja video y audio por separado, y si se cuelga en el
    audio queda un `nombre.f398.mp4` entero pero mudo. Como el éxito se decide mirando
    el disco, ese archivo pasaría por el clip.
    """
    for q in carpeta.glob(nombre + ".*"):
        q.unlink(missing_ok=True)


def _hms(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _formato(altura_max: int) -> list[str]:
    """Qué pista de video se le pide a yt-dlp.

    Sin esto yt-dlp elige vp9/opus y escribe un .webm, ademas de pegarle la extension
    real al nombre: queda "pico_01.mp4.webm" y el archivo que uno cree que escribio
    no existe.

    Es una funcion aparte porque `cuadro` tiene que pedir exactamente lo mismo: la
    facecam se mide en pixeles sobre ese cuadro y se aplica sobre el clip. Si uno
    saliera en 1080 y el otro en 720, el recorte caeria en cualquier lado.
    """
    return ["-f", f"bv*[height<={altura_max}]+ba/b[height<={altura_max}]",
            "-S", "vcodec:h264,acodec:aac,res"]


def cuadro(url: str, t: float, destino: Path, altura_max: int = 720) -> Path:
    """Saca un cuadro del VOD en el segundo `t`, a la misma resolucion que los clips.

    Es para medir la facecam sin bajar nada: yt-dlp solo resuelve la URL del stream
    y ffmpeg salta al segundo pedido y lee un cuadro.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["yt-dlp", "--quiet", "--no-warnings", "-g", *_formato(altura_max), url],
                       capture_output=True, text=True, timeout=120)
    fuentes = r.stdout.strip().splitlines()
    if not fuentes:
        detalle = (r.stderr or "").strip().splitlines()[-1:] or [f"rc={r.returncode}"]
        raise RuntimeError(f"no pude resolver el video: {detalle[0]}")
    f = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(max(0.0, t)), "-i", fuentes[0],
         "-frames:v", "1", "-q:v", "3", str(destino)],
        capture_output=True, text=True, timeout=180)
    # igual que en cortar: el exito se decide mirando el disco
    if not destino.exists() or destino.stat().st_size == 0:
        detalle = (f.stderr or "").strip().splitlines()[-1:] or [f"rc={f.returncode}"]
        raise RuntimeError(f"no pude sacar el cuadro: {detalle[0]}")
    return destino


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
            *_formato(altura_max),
            "--merge-output-format", "mp4",
            "-o", str(base) + ".%(ext)s", url,
        ]
        print(f"  cortando {p.hhmmss()} ...", flush=True)
        r = None
        for intento in range(1, INTENTOS + 1):
            try:
                r = _correr(cmd, TIEMPO_MAXIMO)
                break
            except subprocess.TimeoutExpired:
                _borrar_restos(carpeta, base.name)
                if intento < INTENTOS:
                    print(f"  ! {p.hhmmss()} no terminó en {TIEMPO_MAXIMO / 60:.0f} min, "
                          "pruebo de nuevo", flush=True)

        # yt-dlp puede terminar en 0 y aun asi dejar el archivo con otra extension,
        # asi que el exito se decide mirando el disco, no el returncode.
        producidos = sorted(q for q in carpeta.glob(base.name + ".*")
                            if q.suffix.lower() not in _PARCIALES)
        if not producidos:
            if r is None:
                detalle = [f"no terminó en {TIEMPO_MAXIMO / 60:.0f} min ({INTENTOS} intentos)"]
            else:
                detalle = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [f"rc={r.returncode}"]
            print(f"  ! falló {p.hhmmss()}: {detalle[0]}", flush=True)
            continue
        real = producidos[0]
        if real != destino:
            real = real.replace(destino)
        hechos.append(destino)
    return hechos
