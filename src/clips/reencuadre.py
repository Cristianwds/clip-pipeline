"""Etapa 3: pasar el clip de 16:9 a 9:16 sin perder lo que importa.

Dos modos, porque no hay uno bueno para todo:

- **desenfoque**: el 16:9 entero, escalado al ancho, sobre un fondo del mismo video
  ampliado y desenfocado. No se pierde NADA de imagen. Es el default porque en
  contenido de stream un recorte vertical se come casi todo el gameplay: de un
  1280x720, el recorte 9:16 a altura completa mide 405 px de ancho.

- **recorte**: recorte vertical a altura completa que se mueve siguiendo la acción.
  Sirve cuando el sujeto es una persona (facecam, charla, IRL) y no el gameplay.

- **dividido**: la facecam arriba y la acción abajo, cada una con su propio recorte.
  Es el formato que usan los clippers, y el único que deja ver las dos cosas a la vez.
  Necesita que le digas dónde está la facecam: es una constante por streamer y no se
  detecta sola (ver la nota sobre detección más abajo).

Sobre el tracking: **no hay detección de caras acá**. OpenCV 5 dejó de incluir las
cascadas de Haar y `CascadeClassifier` ya no existe en el wheel; el detector que sí
trae (FaceDetectorYN) necesita bajar un modelo ONNX aparte. Así que el sujeto se
estima sin modelo, por **energía de movimiento**: se compara cada frame con el
anterior y se busca la franja vertical donde más cambia la imagen. Para un stream es
una aproximación razonable —lo que se mueve es la persona o la acción— y tiene la
ventaja de no depender de ningún modelo descargado.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np

VERTICAL = (1080, 1920)


def _perfil_a_centro(perfil: np.ndarray, ancho_ventana: int) -> int:
    """Dado el movimiento por columna, la ventana que acumula más: su centro.

    Suma acumulada para que sea O(ancho) y no O(ancho x ventana).
    """
    if ancho_ventana >= len(perfil):
        return len(perfil) // 2
    acum = np.concatenate([[0.0], np.cumsum(perfil)])
    sumas = acum[ancho_ventana:] - acum[:-ancho_ventana]
    inicio = int(np.argmax(sumas))
    return inicio + ancho_ventana // 2


def _suavizar(
    centros: list[float], ventana_media: int = 9, salto_max: float = 12.0,
    zona_muerta: float = 25.0,
) -> list[float]:
    """Saca el temblor de la trayectoria. Sin esto el encuadre marea.

    Tres filtros encadenados:
      - media móvil, para el ruido de la detección
      - zona muerta: si el objetivo se movió poco, no se mueve la cámara
      - límite de velocidad: nunca más de salto_max px por muestra
    """
    if not centros:
        return []
    x = np.array(centros, dtype=float)
    k = max(1, ventana_media | 1)
    if len(x) >= k:
        pad = k // 2
        xp = np.pad(x, pad, mode="edge")
        x = np.convolve(xp, np.ones(k) / k, mode="valid")

    salida = [float(x[0])]
    for objetivo in x[1:]:
        actual = salida[-1]
        if abs(objetivo - actual) < zona_muerta:
            salida.append(actual)
            continue
        paso = float(np.clip(objetivo - actual, -salto_max, salto_max))
        salida.append(actual + paso)
    return salida


def trayectoria(
    entrada: Path, aspecto: float = 9 / 16, fps_muestreo: float = 4.0,
    ancho_analisis: int = 320, excluir: tuple[int, int, int, int] | None = None,
) -> tuple[list[tuple[float, float]], tuple[int, int]]:
    """Devuelve [(segundo, centro_x en px del original)] y el tamaño del video.

    excluir: región (x, y, ancho, alto) que se ignora al medir el movimiento. Se usa
    para la facecam: es lo que más se mueve en pantalla y si no se la saca, el encuadre
    se va siempre hacia ella en vez de seguir la acción del juego.
    """
    import cv2

    cap = cv2.VideoCapture(str(entrada))
    if not cap.isOpened():
        raise RuntimeError(f"No pude abrir {entrada}")
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    salto = max(1, int(round(fps / fps_muestreo)))

    escala = ancho_analisis / W
    ancho_recorte_chico = max(8, int(round(H * aspecto * escala)))

    centros: list[float] = []
    tiempos: list[float] = []
    previo = None
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % salto == 0:
            chico = cv2.cvtColor(
                cv2.resize(frame, (ancho_analisis, max(1, int(H * escala)))),
                cv2.COLOR_BGR2GRAY,
            ).astype(np.float32)
            if previo is not None:
                dif = np.abs(chico - previo)
                if excluir is not None:
                    ex, ey, ew, eh = (int(round(v * escala)) for v in excluir)
                    dif[max(0, ey):ey + eh, max(0, ex):ex + ew] = 0.0
                perfil = dif.sum(axis=0)
                cx = _perfil_a_centro(perfil, ancho_recorte_chico)
                centros.append(cx / escala)
                tiempos.append(i / fps)
            previo = chico
        i += 1
    cap.release()

    if not centros:
        return [], (W, H)
    return list(zip(tiempos, _suavizar(centros))), (W, H)


def _tamano(entrada: Path) -> tuple[int, int]:
    import cv2
    cap = cv2.VideoCapture(str(entrada))
    if not cap.isOpened():
        raise RuntimeError(f"No pude abrir {entrada}")
    wh = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return wh


def _bandas(alto_total: int, fraccion_cam: float) -> tuple[int, int]:
    """Reparte el alto entre la banda de la facecam y la de la acción (pares, para h264)."""
    cam = max(2, int(round(alto_total * fraccion_cam)) // 2 * 2)
    return cam, alto_total - cam


def _ancho_crop(alto_fuente: int, ancho_destino: int, alto_destino: int) -> int:
    """Ancho del recorte en el original para que llene una banda sin deformarse."""
    return max(2, int(round(alto_fuente * ancho_destino / alto_destino)) // 2 * 2)


def _limites_sin_facecam(
    facecam: tuple[int, int, int, int] | None, ancho: int, W: int
) -> tuple[int, int]:
    """Rango de x permitido para el recorte, evitando repetir la facecam si hay lugar."""
    lo, hi = 0, W - ancho
    if facecam is None:
        return lo, hi
    fx, _, fw, _ = facecam
    if W - (fx + fw) >= ancho:       # entra entero a la derecha de la facecam
        return max(lo, fx + fw), hi
    if fx >= ancho:                  # o entero a la izquierda
        return lo, min(hi, fx - ancho)
    return lo, hi                    # no hay lugar: se repite y listo


def _ubicar(cx: float, ancho: int, lo: int, hi: int) -> int:
    return int(round(min(max(cx - ancho / 2, lo), hi)))


def _comandos_crop(
    puntos: list[tuple[float, float]], ancho: int, W: int, destino: Path,
    filtro: str = "crop@accion", limites: tuple[int, int] | None = None,
) -> None:
    """Archivo de sendcmd: en cada timestamp, la x del recorte."""
    lo, hi = limites if limites else (0, W - ancho)
    lineas = [f"{t:.3f} {filtro} x {_ubicar(cx, ancho, lo, hi)};" for t, cx in puntos]
    destino.write_text("\n".join(lineas) or f"0.0 {filtro} x {lo};", encoding="utf-8")


def _x_inicial(
    puntos: list[tuple[float, float]], ancho: int, W: int,
    limites: tuple[int, int] | None = None,
) -> int:
    lo, hi = limites if limites else (0, W - ancho)
    return _ubicar(puntos[0][1] if puntos else W / 2, ancho, lo, hi)


def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falló: {(r.stderr or '').strip()[-400:]}")


def desenfocar(entrada: Path, salida: Path) -> Path:
    """16:9 centrado sobre su propia versión ampliada y desenfocada. No pierde imagen."""
    w, h = VERTICAL
    filtro = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma=28[fondo];"
        f"[0:v]scale={w}:-2[frente];"
        f"[fondo][frente]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
    _ffmpeg(["-i", str(entrada), "-filter_complex", filtro, "-map", "[v]",
             "-map", "0:a?", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
             "-c:a", "aac", "-b:a", "128k", str(salida)])
    return salida


def recortar_siguiendo(entrada: Path, salida: Path) -> Path:
    """Recorte vertical a altura completa que se mueve con la acción."""
    puntos, (W, H) = trayectoria(entrada)
    ancho = int(round(H * VERTICAL[0] / VERTICAL[1]))
    if ancho >= W:  # ya es mas angosto que 9:16
        return desenfocar(entrada, salida)

    with tempfile.TemporaryDirectory() as tmp:
        cmds = Path(tmp) / "cmds.txt"
        _comandos_crop(puntos, ancho, W, cmds)
        x0 = _x_inicial(puntos, ancho, W)
        filtro = (
            f"sendcmd=f='{cmds}',crop@accion=w={ancho}:h={H}:x={x0}:y=0,"
            f"scale={VERTICAL[0]}:{VERTICAL[1]},setsar=1"
        )
        _ffmpeg(["-i", str(entrada), "-vf", filtro, "-map", "0:v", "-map", "0:a?",
                 "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                 "-c:a", "aac", "-b:a", "128k", str(salida)])
    return salida


def dividir(
    entrada: Path, salida: Path, facecam: tuple[int, int, int, int],
    fraccion_cam: float = 0.34,
) -> Path:
    """Facecam arriba, acción abajo. facecam = (x, y, ancho, alto) en píxeles del original.

    El fondo es el propio video ampliado y desenfocado, así cualquier hueco que quede
    (la facecam casi nunca tiene exactamente el alto de su banda) no es una franja negra.
    """
    ancho_total, alto_total = VERTICAL
    alto_cam, alto_juego = _bandas(alto_total, fraccion_cam)
    fx, fy, fw, fh = facecam
    cap_w, cap_h = _tamano(entrada)
    if fw <= 0 or fh <= 0 or fx + fw > cap_w or fy + fh > cap_h:
        raise ValueError(f"la facecam {facecam} no entra en un video de {cap_w}x{cap_h}")

    # la facecam se saca del analisis: es lo que mas se mueve y se roba el encuadre
    puntos, (W, H) = trayectoria(entrada, excluir=facecam)
    ancho_juego = min(W, _ancho_crop(H, ancho_total, alto_juego))
    limites = _limites_sin_facecam(facecam, ancho_juego, W)
    with tempfile.TemporaryDirectory() as tmp:
        cmds = Path(tmp) / "cmds.txt"
        _comandos_crop(puntos, ancho_juego, W, cmds, limites=limites)
        x0 = _x_inicial(puntos, ancho_juego, W, limites)
        filtro = (
            f"[0:v]scale={ancho_total}:{alto_total}:force_original_aspect_ratio=increase,"
            f"crop={ancho_total}:{alto_total},gblur=sigma=28[fondo];"
            f"[0:v]crop={fw}:{fh}:{fx}:{fy},scale={ancho_total}:-2[cam];"
            f"[0:v]sendcmd=f='{cmds}',crop@accion=w={ancho_juego}:h={H}:x={x0}:y=0,"
            f"scale={ancho_total}:{alto_juego}[juego];"
            f"[fondo][cam]overlay=0:({alto_cam}-h)/2[arriba];"
            f"[arriba][juego]overlay=0:{alto_cam},setsar=1[v]"
        )
        _ffmpeg(["-i", str(entrada), "-filter_complex", filtro, "-map", "[v]",
                 "-map", "0:a?", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                 "-c:a", "aac", "-b:a", "128k", str(salida)])
    return salida


MODOS = {"desenfoque": desenfocar, "recorte": recortar_siguiendo, "dividido": dividir}


def reencuadrar(
    entrada: Path, modo: str = "desenfoque", sufijo: str = "_9x16",
    facecam: tuple[int, int, int, int] | None = None,
) -> Path:
    if modo not in MODOS:
        raise ValueError(f"modo debe ser uno de {sorted(MODOS)}, no {modo!r}")
    if modo == "dividido" and facecam is None:
        raise ValueError("el modo dividido necesita --facecam x,y,ancho,alto")
    salida = entrada.with_name(entrada.stem + sufijo + ".mp4")
    if modo == "dividido":
        return dividir(entrada, salida, facecam)
    return MODOS[modo](entrada, salida)
