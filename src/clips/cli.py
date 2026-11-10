"""CLI del pipeline.

Uso tipico:
    python -m clips.cli <url-del-vod>              # solo detecta y lista los picos
    python -m clips.cli <url-del-vod> --cortar 5   # ademas corta los 5 mejores
"""
from __future__ import annotations

import argparse
import sys

from . import chat, picos as mod_picos


def link(url: str, p: mod_picos.Pico) -> str:
    """URL del VOD posicionada en el pico, para abrirla y validar a ojo."""
    if "twitch.tv" in url:
        return f"{url.split('?')[0]}?t={p.hhmmss()}"
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(p.t)}s"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detecta highlights en un VOD usando el chat.")
    ap.add_argument("url", help="URL del VOD de Twitch o YouTube")
    ap.add_argument("--ventana", type=float, default=5.0, help="bin en segundos (default 5)")
    ap.add_argument("--base", type=float, default=600.0, help="ancho de la mediana movil (default 600s)")
    ap.add_argument("--umbral", type=float, default=None, help="score minimo en sigmas (default: automatico segun duracion)")
    ap.add_argument("--separacion", type=float, default=90.0, help="segundos minimos entre picos")
    ap.add_argument("--borde-inicio", type=float, default=600.0, dest="borde_inicio",
                    help="segundos a ignorar al arranque del stream (default 600)")
    ap.add_argument("--borde-fin", type=float, default=0.0, dest="borde_fin",
                    help="segundos a ignorar al final (default 0: la despedida se filtra por etiqueta)")
    ap.add_argument("--top", type=int, default=10, help="cuantos picos listar")
    ap.add_argument("--ordenar", choices=("prioridad", "score", "foco"), default="prioridad",
                    help="criterio de orden (default prioridad: score modulado por foco)")
    ap.add_argument("--cortar", type=int, default=0, metavar="N", help="cortar los N mejores a salida/")
    ap.add_argument("--formato", choices=("desenfoque", "recorte", "dividido", "nada"),
                    default="desenfoque",
                    help="reencuadre vertical de los clips cortados (default desenfoque)")
    ap.add_argument("--facecam", metavar="X,Y,ANCHO,ALTO", default=None,
                    help="posicion de la facecam en pixeles del original; obligatoria "
                         "con --formato dividido. Es una constante por streamer.")
    ap.add_argument("--subtitulos", action="store_true",
                    help="transcribir el audio y quemar los subtitulos en el clip")
    ap.add_argument("--modelo", default="small",
                    help="modelo de faster-whisper: tiny, base, small, medium (default small)")
    ap.add_argument("--idioma", default=None, help="forzar idioma (ej. es); por defecto lo detecta")
    ap.add_argument("--variante", default=None,
                    help="forzar una variante de formato en vez de rotarlas")
    ap.add_argument("--sin-gancho", action="store_true", dest="sin_gancho",
                    help="no poner el cartel de gancho al principio")
    ap.add_argument("--streamer", default=None, help="nombre del streamer, para el credito y los hashtags")
    ap.add_argument("--juego", default=None, help="juego o tema, para los hashtags")
    ap.add_argument("--forzar", action="store_true", help="volver a bajar el chat ignorando el cache")
    a = ap.parse_args(argv)

    facecam = None
    if a.facecam:
        try:
            facecam = tuple(int(v) for v in a.facecam.split(","))
            if len(facecam) != 4:
                raise ValueError
        except ValueError:
            print("--facecam espera cuatro enteros: X,Y,ANCHO,ALTO", file=sys.stderr)
            return 2
    if a.formato == "dividido" and facecam is None:
        print("--formato dividido necesita --facecam X,Y,ANCHO,ALTO", file=sys.stderr)
        return 2

    print(f"Chat de {a.url}")
    try:
        ruta = chat.descargar(a.url, forzar=a.forzar)
    except chat.ChatNoDisponible as e:
        print(f"No se pudo obtener el chat: {e}", file=sys.stderr)
        return 2
    mensajes = chat.leer(ruta)
    if not mensajes:
        print("No hay mensajes: el VOD no tiene chat o no se pudo bajar.", file=sys.stderr)
        return 1

    dur = mensajes[-1].t
    print(f"{len(mensajes)} mensajes sobre {dur / 3600:.1f} h "
          f"({len(mensajes) / max(dur, 1):.2f} msg/s de promedio)\n")

    picos = mod_picos.detectar(
        mensajes, ventana=a.ventana, base_seg=a.base,
        umbral=a.umbral, separacion=a.separacion,
        borde_inicio=a.borde_inicio, borde_fin=a.borde_fin, top=a.top,
        ordenar=a.ordenar,
    )
    umbral = a.umbral if a.umbral is not None else mod_picos.umbral_automatico(int(dur / a.ventana))
    print(f"Umbral: {umbral} sigmas\n")
    if not picos:
        print("Ningun pico supero el umbral: el chat no marca nada claro en este VOD.")
        print("Puede ser que no haya highlights, o que la señal no sirva para este streamer.")
        return 0

    print(f"(orden: {a.ordenar})\n")
    print(f"{'#':>2}  {'momento':>9}  {'prior':>6}  {'score':>6}  {'foco':>5}  {'msg/s':>6}  "
          f"{'tipo':<10}  que dice el chat")
    print("-" * 118)
    for n, p in enumerate(picos, 1):
        tema = ", ".join(p.tema) or "-"
        reac = ", ".join(p.reaccion)
        dice = tema + (f"   [{reac}]" if reac else "")
        marca = " ←artefacto" if p.es_artefacto else ""
        print(f"{n:>2}  {p.hhmmss():>9}  {p.prioridad:>6.1f}  {p.score:>6.1f}  {p.foco:>5.2f}  "
              f"{p.tasa:>6.1f}  {p.etiqueta[:10]:<10}  {dice}{marca}")
    print("\n  prior = score x (0.5 + foco): el volumen, modulado por cuanto se pone de acuerdo el chat")
    print("  foco  = fraccion del chat que converge en un tema propio de ESE momento")
    print("  [entre corchetes] = como reacciona ese chat siempre, no es el asunto del pico")
    if any(p.es_artefacto for p in picos):
        print("  los marcados como artefacto van siempre al final: la despedida del stream no es un clip")
    print("\nPara validar a ojo:")
    for n, p in enumerate(picos, 1):
        print(f"  {n:>2}. {link(a.url, p)}")

    if a.cortar:
        from . import pipeline
        from .cortar import SALIDA
        from .publicar import armar_publicacion, escribir, planificar
        print()
        try:
            hechos = pipeline.producir(
                a.url, picos[: a.cortar], formato=a.formato, facecam=facecam,
                subtitulos=a.subtitulos, modelo=a.modelo, idioma=a.idioma,
                variante=a.variante, sin_gancho=a.sin_gancho)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        pubs = []
        for h in hechos:
            try:
                pubs.append(armar_publicacion(
                    h.final, h.pico, a.url, lineas=h.lineas,
                    streamer=a.streamer, juego=a.juego))
            except Exception as e:
                print(f"  ! no pude armar el plan de {h.final.name}: {e}")
        if pubs:
            js, md = escribir(planificar(pubs), SALIDA)
            print(f"\n  plan de publicación: {md.name} (y {js.name})")
            print("  NO se publicó nada: revisalo antes de subir.")
        print(f"{len(hechos)} clips en salida/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
