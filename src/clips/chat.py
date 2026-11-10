"""Descarga y cachea el chat de un VOD.

Backends:
  YouTube -> yt-dlp, que baja el chat replay como si fuera una pista de subtitulos.
             Es la via mantenida: yt-dlp se actualiza cada semana.
  Twitch  -> TwitchDownloaderCLI. El chat de un VOD de Twitch no es una pista de
             subtitulos y la API oficial (Helix) no lo expone, asi que la unica via
             es la API interna de GQL. TwitchDownloaderCLI la encapsula y la mantiene
             actualizada, que es toda la ventaja de usarlo en vez de pegarle a mano.

Antes esto usaba chat-downloader, que soportaba las dos plataformas, pero su
ultima version es de 2022 y hoy falla en ambas porque cambiaron los formatos.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(__file__).resolve().parents[2] / "datos"

# Binario de TwitchDownloaderCLI. Vive afuera del proyecto a proposito: es un
# binario de terceros, no una dependencia del repo.
TWITCH_CLI = Path(
    os.environ.get("TWITCHDOWNLOADER_CLI",
                   Path.home() / ".local/opt/twitchdownloader/TwitchDownloaderCLI")
)


# Tope para bajar un chat. En modo lote nadie mira la terminal: una descarga colgada
# frenaba la cola entera sin dar error. Un chat de 14 h baja en bastante menos que esto.
TIEMPO_MAXIMO = float(os.environ.get("CLIP_PIPELINE_TIMEOUT_CHAT", 2700))


class ChatNoDisponible(RuntimeError):
    pass


def _correr(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIEMPO_MAXIMO)
    except subprocess.TimeoutExpired:
        raise ChatNoDisponible(
            f"la descarga del chat no terminó en {TIEMPO_MAXIMO / 60:.0f} min y se cortó") from None


@dataclass
class Mensaje:
    t: float          # segundos desde el inicio del VOD
    autor: str
    texto: str


def slug(url: str) -> str:
    """Nombre de archivo estable para cachear el chat de una URL."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", url.strip("/")).strip("_")[-80:]


def descargar(url: str, forzar: bool = False) -> Path:
    """Baja el chat del VOD a un JSONL propio. Si ya está cacheado, no lo rebaja."""
    CACHE.mkdir(parents=True, exist_ok=True)
    destino = CACHE / f"{slug(url)}.jsonl"
    if destino.exists() and not forzar:
        print(f"  (cacheado: {destino.name})")
        return destino

    mensajes = _desde_twitch(url) if "twitch.tv" in url else _desde_ytdlp(url)
    if not mensajes:
        raise ChatNoDisponible(
            "El VOD no tiene chat replay. Pasa cuando el stream se subió sin chat, "
            "cuando el chat fue borrado, o cuando el video no fue un directo."
        )

    parcial = destino.with_suffix(".jsonl.parcial")
    with parcial.open("w", encoding="utf-8") as f:
        for m in mensajes:
            f.write(json.dumps({"t": m.t, "autor": m.autor, "texto": m.texto},
                               ensure_ascii=False) + "\n")
    parcial.replace(destino)
    print(f"  {len(mensajes)} mensajes guardados en {destino.name}")
    return destino


def _id_vod_twitch(url: str) -> str:
    m = re.search(r"twitch\.tv/videos/(\d+)", url)
    if not m:
        raise ChatNoDisponible(f"No pude sacar el id del VOD de: {url}")
    return m.group(1)


def _desde_twitch(url: str) -> list[Mensaje]:
    """TwitchDownloaderCLI baja el chat del VOD a su propio JSON."""
    if not TWITCH_CLI.exists():
        raise ChatNoDisponible(
            f"No encontré TwitchDownloaderCLI en {TWITCH_CLI}. Bajalo del release oficial "
            "(lay295/TwitchDownloader), verificá el sha256 y apuntá TWITCHDOWNLOADER_CLI ahí."
        )
    vod = _id_vod_twitch(url)
    with tempfile.TemporaryDirectory() as tmp:
        salida = Path(tmp) / "chat.json"
        print(f"  bajando chat del VOD {vod} con TwitchDownloaderCLI...", flush=True)
        r = _correr([str(TWITCH_CLI), "chatdownload", "--id", vod,
                     "-o", str(salida), "--collision", "overwrite"])
        if not salida.exists():
            detalle = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["sin detalle"]
            raise ChatNoDisponible(f"TwitchDownloaderCLI falló: {detalle[0]}")
        return _parsear_twitch(salida)


def _parsear_twitch(ruta: Path) -> list[Mensaje]:
    with ruta.open(encoding="utf-8") as f:
        d = json.load(f)
    msgs = []
    for c in d.get("comments", []):
        texto = ((c.get("message") or {}).get("body") or "").strip()
        if not texto:
            continue
        autor = (c.get("commenter") or {}).get("display_name") or "?"
        msgs.append(Mensaje(t=float(c["content_offset_seconds"]), autor=autor, texto=texto))
    msgs.sort(key=lambda m: m.t)
    return msgs


def _desde_ytdlp(url: str) -> list[Mensaje]:
    """yt-dlp baja el chat replay de YouTube como pista 'live_chat' en JSONL."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "chat"
        cmd = [
            "yt-dlp", "--skip-download",
            "--write-subs", "--sub-langs", "live_chat",
            "--no-warnings", "-o", str(base), url,
        ]
        print("  bajando chat con yt-dlp...", flush=True)
        r = _correr(cmd)
        archivos = list(Path(tmp).glob("*.live_chat.json"))
        if not archivos:
            detalle = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["sin detalle"]
            raise ChatNoDisponible(f"yt-dlp no devolvió chat: {detalle[0]}")
        return _parsear_live_chat(archivos[0])


def _parsear_live_chat(ruta: Path) -> list[Mensaje]:
    """Extrae (offset, autor, texto) del JSONL de YouTube, ignorando todo lo demás.

    El formato trae un evento por línea con mucha estructura alrededor; solo
    interesan los mensajes de texto, no los superchats ni las membresías.
    """
    msgs: list[Mensaje] = []
    with ruta.open(encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                ev = json.loads(linea)
            except json.JSONDecodeError:
                continue
            accion = ev.get("replayChatItemAction") or {}
            ms = accion.get("videoOffsetTimeMsec")
            if ms is None:
                continue
            for a in accion.get("actions", []):
                item = ((a.get("addChatItemAction") or {}).get("item") or {})
                r = item.get("liveChatTextMessageRenderer")
                if not r:
                    continue
                texto = "".join(
                    run.get("text") or run.get("emoji", {}).get("emojiId", "")
                    for run in (r.get("message") or {}).get("runs", [])
                )
                if not texto.strip():
                    continue
                autor = ((r.get("authorName") or {}).get("simpleText")) or "?"
                msgs.append(Mensaje(t=int(ms) / 1000.0, autor=autor, texto=texto))
    msgs.sort(key=lambda m: m.t)
    return msgs


def leer(ruta: Path) -> list[Mensaje]:
    msgs = []
    with ruta.open(encoding="utf-8") as f:
        for linea in f:
            d = json.loads(linea)
            msgs.append(Mensaje(d["t"], d["autor"], d["texto"]))
    msgs.sort(key=lambda m: m.t)
    return msgs
