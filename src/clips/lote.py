"""Modo lote: la cola de VODs, en qué estado está cada uno y qué se decidió de cada pico.

El CLI procesa un VOD y se olvida. Para producción hace falta memoria: qué VODs ya
se analizaron, qué picos se revisaron y con qué veredicto, qué clips se produjeron y
dónde se publicaron. Todo eso vive en un solo archivo, `datos/lote.json`.

La lógica está partida en dos a propósito:

- funciones puras sobre el estado (un dict), que son las que tienen tests;
- las que hacen E/S (`analizar_url`, `descubrir`, `producir_pico`), que no tocan el
  estado: devuelven un resultado y otra función pura lo aplica.

Esa separación también es lo que deja al panel correr un análisis de varios minutos
sin tener el estado tomado: mientras tanto se puede seguir revisando.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import chat, perfiles as mod_perfiles, picos as mod_picos, revision
from .cortar import SALIDA
from .picos import Pico

ESTADO = chat.CACHE / "lote.json"
# Los respaldos van afuera del repo a propósito: `datos/` está ignorado por git, así
# que un `git clean -fdx` o borrar la carpeta para liberar disco se lleva el estado.
RESPALDOS = Path(os.environ.get(
    "CLIP_PIPELINE_RESPALDOS", Path.home() / ".config/clip-pipeline/respaldos"))
RESPALDOS_DIARIOS = 14   # cuántos días de copias se conservan
TOP = 10                 # picos que se guardan por VOD
DURACION_MINIMA = 1200   # un "VOD" de menos de 20 min es un clip o un resubido
CONTROLES = ("ok", "rehacer")
# Cuánto se puede correr el inicio de un clip al rehacerlo. Más que esto ya no es
# ajustar el corte: es otro momento, y le corresponde otro pico.
CORRIMIENTO_MAXIMO = 15.0


# ---------------------------------------------------------------- URLs

def plataforma_e_id(url: str) -> tuple[str, str]:
    """('twitch', '2922521276') o ('youtube', 'mq_ceLA5aro'). ('', '') si no la conoce."""
    u = urlparse(url.strip())
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host.endswith("twitch.tv"):
        m = re.search(r"/videos/(\d+)", u.path)
        return ("twitch", m.group(1)) if m else ("", "")
    if host == "youtu.be":
        return ("youtube", u.path.strip("/").split("/")[0])
    if host.endswith("youtube.com"):
        v = parse_qs(u.query).get("v", [""])[0]
        if not v:
            m = re.match(r"/(?:live|shorts|embed)/([\w-]+)", u.path)
            v = m.group(1) if m else ""
        return ("youtube", v) if v else ("", "")
    return ("", "")


def normalizar(url: str) -> str:
    """Forma canónica de la URL de un VOD.

    El mismo VOD llega escrito de varias formas (`youtu.be/ID`, `/live/ID`, con
    `?t=` pegado de cuando se compartió). Sin esto se analiza dos veces y, peor, se
    revisa dos veces: los veredictos duplicados inflan la muestra.
    """
    plataforma, vid = plataforma_e_id(url)
    if plataforma == "twitch":
        return f"https://www.twitch.tv/videos/{vid}"
    if plataforma == "youtube":
        return f"https://www.youtube.com/watch?v={vid}"
    return url.strip()


# ---------------------------------------------------------------- estado

def nuevo_estado() -> dict:
    return {"vods": []}


def cargar(ruta: Path | None = None) -> dict:
    ruta = ruta or ESTADO
    if not ruta.exists():
        return nuevo_estado()
    return json.loads(ruta.read_text(encoding="utf-8"))


def contar_veredictos(estado: dict) -> int:
    return sum(1 for v in estado.get("vods", []) for p in v.get("picos", [])
               if p.get("veredicto"))


def diarios_a_borrar(nombres: list[str], conservar: int = RESPALDOS_DIARIOS) -> list[str]:
    """De las copias diarias, las que sobran. Las `perdida_*` no se tocan nunca: cada
    una es el estado de justo antes de que desaparecieran veredictos."""
    diarios = sorted(n for n in nombres if re.fullmatch(r"lote_\d{4}-\d{2}-\d{2}\.json", n))
    return diarios[:-conservar] if conservar > 0 else diarios


def carpeta_de_respaldos(ruta: Path) -> Path:
    """El estado real se respalda en `RESPALDOS`; cualquier otro (una copia de
    prueba, el de un test), al lado suyo, para no mezclarlo con el de verdad."""
    return RESPALDOS if ruta == ESTADO else ruta.parent / "respaldos"


def respaldar(ruta: Path, estado_nuevo: dict, ahora: datetime | None = None,
              carpeta: Path | None = None) -> list[Path]:
    """Copia el archivo que está por pisarse, si hace falta. Devuelve lo que copió.

    Los veredictos son lo único del proyecto que no se regenera y vivían en una sola
    copia. Se guardan dos cosas distintas:

    - una copia por día, tomada en la primera escritura del día: es el estado como
      quedó la última vez que se trabajó. Sirve contra el disco o un borrado.
    - una copia cada vez que el estado nuevo tiene **menos** veredictos que el que
      está en disco. La diaria no alcanza para esto: si un bug pisa los veredictos a
      la tarde, la copia de ese día es de antes de la sesión de revisión de la
      mañana. Deshacer un veredicto a mano también dispara una copia; es barato y
      preferible a adivinar cuándo una pérdida es intencional.
    """
    if not ruta.exists():
        return []
    ahora = ahora or datetime.now()
    carpeta = carpeta or carpeta_de_respaldos(ruta)
    hechos: list[Path] = []
    diaria = carpeta / f"lote_{ahora:%Y-%m-%d}.json"
    if not diaria.exists():
        carpeta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ruta, diaria)
        hechos.append(diaria)
        for nombre in diarios_a_borrar([q.name for q in carpeta.iterdir()]):
            (carpeta / nombre).unlink()
    try:
        antes = contar_veredictos(json.loads(ruta.read_text(encoding="utf-8")))
    except (ValueError, AttributeError):
        antes = 0      # un archivo roto ya quedó en la copia diaria
    despues = contar_veredictos(estado_nuevo)
    if despues < antes:
        carpeta.mkdir(parents=True, exist_ok=True)
        perdida = carpeta / f"perdida_{ahora:%Y-%m-%d_%H%M%S}_{antes}a{despues}.json"
        shutil.copy2(ruta, perdida)
        hechos.append(perdida)
    return hechos


def guardar(estado: dict, ruta: Path | None = None, ahora: datetime | None = None) -> Path:
    ruta = ruta or ESTADO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    try:
        respaldar(ruta, estado, ahora)
    except OSError as e:
        # no poder respaldar no puede impedir guardar: se perdería el veredicto
        # recién puesto, que es justo lo que el respaldo quiere cuidar
        print(f"  aviso: no se pudo respaldar el estado ({e})")
    # se escribe aparte y se renombra: si el proceso muere a mitad de la escritura,
    # el archivo que queda es el anterior entero y no uno truncado. Acá adentro
    # están los veredictos, que son lo único del proyecto que no se puede regenerar.
    parcial = ruta.with_suffix(".json.parcial")
    parcial.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    parcial.replace(ruta)
    return ruta


@contextlib.contextmanager
def transaccion(ruta: Path | None = None):
    """Lee el estado, lo presta para modificarlo y lo guarda, con el archivo tomado.

    El panel y `lote correr` son procesos distintos que escriben el mismo archivo. La
    primera versión de `correr` cargaba el estado una vez al empezar y lo guardaba
    después de cada VOD: todo veredicto puesto en el panel mientras tanto se pisaba
    con la copia vieja. Ahora nadie guarda una copia que no haya leído recién.

    Si el bloque levanta una excepción, no se guarda nada.
    """
    ruta = ruta or ESTADO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta.with_suffix(".candado"), "w") as candado:
        fcntl.flock(candado, fcntl.LOCK_EX)
        estado = cargar(ruta)
        yield estado
        guardar(estado, ruta)


def buscar(estado: dict, id_vod: str) -> dict | None:
    return next((v for v in estado["vods"] if v["id"] == id_vod), None)


def agregar_vod(estado: dict, url: str, streamer: str = "", titulo: str = "",
                ahora: datetime | None = None) -> tuple[dict, bool]:
    """Suma un VOD a la cola. Devuelve (vod, si era nuevo)."""
    canonica = normalizar(url)
    if not plataforma_e_id(canonica)[0]:
        raise ValueError(f"no reconozco esta URL como un VOD de Twitch o YouTube: {url}")
    id_vod = chat.slug(canonica)
    existente = buscar(estado, id_vod)
    if existente:
        # un VOD descubierto sin streamer puede completarse después
        if streamer and not existente.get("streamer"):
            existente["streamer"] = streamer
        return existente, False
    vod = {
        "id": id_vod, "url": canonica, "plataforma": plataforma_e_id(canonica)[0],
        "streamer": streamer, "titulo": titulo, "estado": "pendiente", "error": "",
        "agregado": (ahora or datetime.now()).isoformat(timespec="seconds"),
        "analizado": "", "mensajes": 0, "duracion": 0.0, "umbral": 0.0, "picos": [],
    }
    estado["vods"].append(vod)
    return vod, True


def pico_a_dict(p: Pico, puesto: int) -> dict:
    return {
        "t": p.t, "momento": p.hhmmss(), "puesto": puesto,
        "score": round(p.score, 2), "foco": round(p.foco, 3), "tasa": round(p.tasa, 2),
        "prioridad": round(p.prioridad, 2), "tema": p.tema, "reaccion": p.reaccion,
        "etiqueta": p.etiqueta, "artefacto": p.es_artefacto,
        "veredicto": "", "nota": "", "clip": "", "publicacion": None, "publicado": {},
        "control": "", "nota_control": "", "ajuste": {}, "entregado": None,
    }


def dict_a_pico(d: dict) -> Pico:
    return Pico(t=d["t"], score=d["score"], tasa=d["tasa"], tema=list(d["tema"]),
                reaccion=list(d["reaccion"]), foco=d["foco"], etiqueta=d["etiqueta"])


HEREDADO = ("veredicto", "nota", "clip", "publicacion", "publicado",
            "control", "nota_control", "ajuste", "entregado")


def fusionar_picos(viejos: list[dict], nuevos: list[dict], tolerancia: float = 10.0) -> list[dict]:
    """Los picos nuevos, conservando lo que un humano ya decidió sobre los viejos.

    Reanalizar un VOD (porque cambió el detector) no puede borrar los veredictos:
    son el único dato caro del proyecto. Un pico nuevo hereda del viejo que caiga a
    menos de `tolerancia` segundos. Los viejos revisados que el detector ya no
    devuelve se conservan al final, sin puesto, para no perder el veredicto.
    """
    usados: set[int] = set()
    for n in nuevos:
        cerca = [(abs(v["t"] - n["t"]), i) for i, v in enumerate(viejos)
                 if i not in usados and abs(v["t"] - n["t"]) <= tolerancia]
        if cerca:
            _, i = min(cerca)
            usados.add(i)
            for campo in HEREDADO:
                n[campo] = viejos[i].get(campo, n[campo])
    huerfanos = [dict(v, puesto=0) for i, v in enumerate(viejos)
                 if i not in usados and (v.get("veredicto") or v.get("clip"))]
    return nuevos + huerfanos


def aplicar_analisis(vod: dict, resultado: dict, ahora: datetime | None = None) -> dict:
    vod["picos"] = fusionar_picos(vod.get("picos", []), resultado["picos"])
    vod.update(estado="analizado", error="",
               mensajes=resultado["mensajes"], duracion=resultado["duracion"],
               umbral=resultado["umbral"], ventana=resultado.get("ventana", 5.0),
               analizado=(ahora or datetime.now()).isoformat(timespec="seconds"))
    return vod


def marcar_error(vod: dict, mensaje: str) -> dict:
    vod.update(estado="error", error=mensaje)
    return vod


def buscar_pico(vod: dict, t: float) -> dict | None:
    return next((p for p in vod["picos"] if abs(p["t"] - t) < 0.01), None)


def poner_veredicto(vod: dict, t: float, veredicto: str, nota: str | None = None) -> dict:
    """veredicto vacío lo borra, para poder deshacer un click equivocado."""
    if veredicto and veredicto not in revision.VEREDICTOS:
        raise ValueError(f"veredicto debe ser uno de {', '.join(revision.VEREDICTOS)}")
    pico = buscar_pico(vod, t)
    if pico is None:
        raise ValueError("ese pico no existe en este VOD")
    pico["veredicto"] = veredicto
    if nota is not None:
        pico["nota"] = nota.strip()
    return pico


def poner_control(vod: dict, t: float, control: str, nota: str | None = None) -> dict:
    """El segundo veredicto: sobre el clip ya hecho, no sobre el momento.

    El veredicto del pico se pone mirando el VOD embebido, antes de producir: dice
    que el momento vale. No dice nada de si el clip salió bien (el corte, los
    subtítulos, el gancho, el encuadre). Sin este paso lo primero que lo ve entero
    es el streamer. Vacío lo borra.
    """
    if control and control not in CONTROLES:
        raise ValueError(f"el control debe ser uno de {', '.join(CONTROLES)}")
    pico = buscar_pico(vod, t)
    if pico is None or not pico.get("clip"):
        raise ValueError("ese pico no tiene un clip producido")
    if pico.get("entregado") and control != "ok":
        raise ValueError("ese clip ya se entregó: no se le puede sacar el visto bueno")
    pico["control"] = control
    if nota is not None:
        pico["nota_control"] = nota.strip()
    return pico


def validar_ajuste(variante: str | None, corrimiento: float) -> tuple[str, float]:
    """Normaliza lo que se pide al rehacer un clip. Levanta ValueError si no vale."""
    from . import estilos
    variante = (variante or "").strip()
    if variante and variante not in {v.nombre for v in estilos.VARIANTES}:
        raise ValueError(f"variante desconocida: {variante}")
    if abs(corrimiento) > CORRIMIENTO_MAXIMO:
        raise ValueError(f"el inicio se puede correr hasta {CORRIMIENTO_MAXIMO:.0f} s; "
                         "más que eso es otro momento")
    return variante, float(corrimiento)


def marcar_entregado(vod: dict, tiempos: list[float], paquete: str,
                     ahora: datetime | None = None) -> list[dict]:
    """Anota que estos clips salieron en un paquete. Solo los que tienen el visto
    bueno: si a alguno se lo sacaron mientras se armaba el zip, no se marca."""
    fecha = (ahora or datetime.now()).isoformat(timespec="minutes")
    marcados = []
    for t in tiempos:
        pico = buscar_pico(vod, t)
        if pico and pico.get("clip") and pico.get("control") == "ok":
            pico["entregado"] = {"fecha": fecha, "paquete": paquete}
            marcados.append(pico)
    return marcados


def poner_facecam(vod: dict, facecam: str | list[int] | None) -> dict:
    """La facecam de este VOD en particular. Vacío la quita y vuelve a valer la del
    perfil."""
    crudo = ",".join(map(str, facecam)) if isinstance(facecam, list) else facecam
    vod["facecam"] = mod_perfiles.parsear_facecam(crudo)
    return vod


def encuadre(vod: dict, perfil: mod_perfiles.Perfil) -> tuple[str, list[int] | None]:
    """(formato, facecam) con los que se produce un clip de este VOD.

    La facecam era una sola por streamer, y no es tan constante: cambia de lugar y
    de tamaño según el juego o la escena. Con la del perfil mal ubicada, el modo
    dividido recorta un pedazo de juego arriba y deja la cámara metida en la banda
    de abajo. La del VOD manda; si no tiene, vale la del perfil.

    Como en `Perfil.formato_efectivo`, el dividido sin ninguna facecam cae a
    desenfoque en vez de fallar a mitad de una producción.
    """
    facecam = vod.get("facecam") or perfil.facecam
    formato = perfil.formato
    if formato == "dividido" and not facecam:
        formato = "desenfoque"
    return formato, (list(facecam) if facecam else None)


def exigir_permiso(vod: dict, perfil: mod_perfiles.Perfil | None) -> None:
    """Levanta ValueError si no hay permiso concedido para publicar clips de este VOD.

    Producir no pasa por acá: hacen falta clips ya hechos para pedir el permiso (son
    lo que se le manda al streamer). Lo que no se puede sin permiso es registrar una
    publicación. Un VOD sin perfil cuenta como «sin pedir»: el permiso es de alguien.
    """
    if perfil is not None and perfil.puede_publicar:
        return
    quien = vod.get("streamer") or "este VOD"
    if perfil is None:
        raise ValueError(f"{quien} no tiene perfil, así que no hay permiso anotado: "
                         "cargalo en la pestaña Streamers")
    raise ValueError(f"el permiso de {perfil.nombre} está «{perfil.permiso}»: tiene que "
                     "estar «concedido» para registrar una publicación")


def marcar_publicado(vod: dict, t: float, plataforma: str, url: str = "",
                     vistas: int | None = None, ahora: datetime | None = None) -> dict:
    """Anota que un clip se subió a una plataforma, y después sus vistas.

    Sin este registro no hay forma de contestar si el canal es monetizable: hay que
    poder cruzar las vistas con la etiqueta, la variante y el streamer.
    """
    pico = buscar_pico(vod, t)
    if pico is None or not pico.get("clip"):
        raise ValueError("ese pico no tiene un clip producido")
    registro = pico["publicado"].setdefault(
        plataforma, {"fecha": (ahora or datetime.now()).isoformat(timespec="minutes"),
                     "url": "", "vistas": None})
    if url:
        registro["url"] = url.strip()
    if vistas is not None:
        if vistas < 0:
            raise ValueError("las vistas no pueden ser negativas")
        registro["vistas"] = vistas
    return registro


def filas(estado: dict) -> list[dict]:
    """Una fila por pico con puesto, para `revision`. Los huérfanos (puesto 0) no
    entran: ya no son lo que el detector devuelve."""
    return [
        {"vod": v["id"], "streamer": v.get("streamer") or "—", "plataforma": v["plataforma"],
         "puesto": p["puesto"], "veredicto": p["veredicto"], "etiqueta": p["etiqueta"] or "—",
         "prioridad": p["prioridad"], "foco": p["foco"], "score": p["score"]}
        for v in estado["vods"] for p in v["picos"] if p["puesto"]
    ]


def proxima_fecha(estado: dict, ahora: datetime | None = None) -> datetime:
    """Día para agendar el próximo clip: uno por día, después del último agendado.

    Los clips se producen de a uno desde el panel, así que el «un clip por día» de
    `publicar.planificar` hay que sostenerlo acá, mirando lo que ya está agendado.
    """
    ahora = ahora or datetime.now()
    fechas = []
    for v in estado["vods"]:
        for p in v["picos"]:
            for datos in ((p.get("publicacion") or {}).get("plataformas") or {}).values():
                if datos.get("cuando"):
                    fechas.append(datetime.fromisoformat(datos["cuando"]))
    if not fechas or max(fechas) < ahora:
        return ahora
    return (max(fechas) + timedelta(days=1)).replace(hour=0, minute=0)


# ---------------------------------------------------------------- E/S

def analizar_url(url: str, top: int = TOP) -> dict:
    """Baja el chat y detecta. No toca el estado: devuelve lo que hay que aplicar."""
    ruta = chat.descargar(url)
    mensajes = chat.leer(ruta)
    if not mensajes:
        raise chat.ChatNoDisponible("el chat bajó vacío")
    dur = mensajes[-1].t
    ventana = mod_picos.ventana_sugerida(mensajes)
    detectados = mod_picos.detectar(mensajes, ventana=ventana, top=top)
    return {
        "mensajes": len(mensajes), "duracion": dur, "ventana": ventana,
        "umbral": mod_picos.umbral_automatico(int(dur / ventana)),
        "picos": [pico_a_dict(p, n) for n, p in enumerate(detectados, 1)],
    }


def parsear_listado(datos: dict, minimo: float = DURACION_MINIMA) -> list[dict]:
    """Saca los VODs del JSON de `yt-dlp --flat-playlist -J`.

    Se descartan los que no tienen duración (directos en curso o programados, que
    todavía no tienen chat replay) y los cortos, que son clips subidos al canal.
    """
    out = []
    for e in datos.get("entries") or []:
        if not e:
            continue
        url = e.get("url") or e.get("webpage_url") or ""
        dur = e.get("duration")
        if not url or not dur or dur < minimo or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        if not plataforma_e_id(url)[0]:
            continue
        out.append({"url": normalizar(url), "titulo": e.get("title") or "", "duracion": float(dur)})
    return out


def descubrir(canal: str, limite: int = 5) -> list[dict]:
    """Los últimos VODs de un canal. En Twitch conviene pasar
    `https://www.twitch.tv/CANAL/videos?filter=archives`; en YouTube, `.../streams`."""
    # se piden de más porque después se filtran los cortos y los que están en vivo
    r = subprocess.run(
        ["yt-dlp", "--flat-playlist", "-J", "--no-warnings",
         "--playlist-end", str(limite * 3), canal],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        detalle = (r.stderr or "").strip().splitlines()[-1:] or [f"rc={r.returncode}"]
        raise RuntimeError(f"no pude listar {canal}: {detalle[0]}")
    return parsear_listado(json.loads(r.stdout))[:limite]


def producir_pico(vod: dict, pico: dict, perfil: mod_perfiles.Perfil | None,
                  desde: datetime | None = None, log=print,
                  variante: str | None = None, corrimiento: float = 0.0) -> dict:
    """Corta, reencuadra y subtitula un pico. Devuelve {clip, publicacion, avisos, ajuste}.

    variante y corrimiento son para rehacer un clip que no pasó el control: otra
    variante cambia el largo y el estilo; el corrimiento mueve el corte entero
    (negativo = arranca antes). El `t` del pico guardado no se toca, porque es su
    identidad en el estado: el corrimiento se aplica solo a la copia que se produce.
    """
    from . import pipeline
    from .publicar import armar_publicacion, escribir_textos, planificar

    variante, corrimiento = validar_ajuste(variante, corrimiento)
    perfil = perfil or mod_perfiles.Perfil(nombre=vod.get("streamer") or "")
    carpeta = SALIDA / vod["id"]
    a_producir = dict_a_pico(pico)
    a_producir.t += corrimiento
    formato, facecam = encuadre(vod, perfil)
    hechos = pipeline.producir(
        vod["url"], [a_producir], carpeta=carpeta, variante=variante or None,
        formato=formato, facecam=tuple(facecam) if facecam else None,
        subtitulos=perfil.subtitulos, modelo=perfil.modelo, idioma=perfil.idioma or None,
        primero=max(pico["puesto"] - 1, 0), log=log)
    if not hechos:
        raise RuntimeError("no se pudo cortar el clip (¿el VOD sigue disponible?)")
    h = hechos[0]
    pub = armar_publicacion(h.final, h.pico, vod["url"], lineas=h.lineas,
                            streamer=perfil.nombre or None, juego=perfil.juego or None,
                            voz=perfil.voz)
    planificar([pub], desde=desde)
    escribir_textos([pub], carpeta)
    datos = asdict(pub)
    datos["variante"], datos["gancho"], datos["voz"] = h.variante, h.gancho, perfil.voz
    return {"clip": f"{vod['id']}/{h.final.name}", "publicacion": datos, "avisos": h.avisos,
            "ajuste": {"variante": variante, "corrimiento": corrimiento}}


def aplicar_clip(pico: dict, resultado: dict) -> dict:
    pico["clip"], pico["publicacion"] = resultado["clip"], resultado["publicacion"]
    pico["ajuste"] = resultado.get("ajuste") or {}
    # un clip recién hecho no lo miró nadie: el control anterior era de otro archivo.
    # La nota se conserva, porque dice por qué se rehízo.
    pico["control"] = ""
    return pico


def correr(ruta: Path | None = None, log=print) -> tuple[int, int]:
    """Analiza todos los pendientes. Devuelve (analizados, fallidos).

    Un VOD que falla queda marcado con su error y el lote sigue: lo normal en un
    lote es que alguno falle (VOD solo para suscriptores, chat borrado) y eso no
    puede frenar a los otros nueve.

    El estado se toma solo para aplicar cada resultado, nunca durante la descarga:
    así se puede revisar en el panel mientras el lote corre.
    """
    pendientes = [(v["id"], v["url"], v.get("streamer") or "?")
                  for v in cargar(ruta)["vods"] if v["estado"] == "pendiente"]
    bien = mal = 0
    for id_vod, url, streamer in pendientes:
        log(f"[{streamer}] {url}")
        resultado, error = None, ""
        try:
            resultado = analizar_url(url)
        except Exception as e:
            error = str(e)
        with transaccion(ruta) as estado:
            vod = buscar(estado, id_vod)
            if vod is None:              # lo sacaron de la cola mientras se analizaba
                continue
            if resultado is None:
                marcar_error(vod, error)
                mal += 1
                log(f"  ! {error}")
            else:
                aplicar_analisis(vod, resultado)
                bien += 1
                log(f"  {len(resultado['picos'])} picos")
    return bien, mal


# ---------------------------------------------------------------- CLI

def _texto_estado(estado: dict) -> str:
    out = [f"{'streamer':<16} {'estado':<10} {'picos':>5} {'rev':>4} {'buenos':>6}  url"]
    for v in estado["vods"]:
        con_puesto = [p for p in v["picos"] if p["puesto"]]
        rev = sum(1 for p in con_puesto if p["veredicto"])
        buenos = sum(1 for p in con_puesto if p["veredicto"] == "bueno")
        out.append(f"{(v.get('streamer') or '—')[:16]:<16} {v['estado']:<10} "
                   f"{len(con_puesto):>5} {rev:>4} {buenos:>6}  {v['url']}"
                   + (f"\n    ! {v['error']}" if v["error"] else ""))
    m = revision.resumen(filas(estado))["top_k"]
    if m["revisados"]:
        lo, hi = m["intervalo"]
        out.append(f"\nPrecisión en el top 5: {m['precision']:.0%} "
                   f"({m['buenos']}/{m['revisados']} revisados, IC 95% {lo:.0%}–{hi:.0%})")
    else:
        out.append("\nTodavía no hay picos revisados: abrí el panel con python -m clips.panel")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cola de VODs del pipeline.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ag = sub.add_parser("agregar", help="suma VODs a la cola")
    ag.add_argument("urls", nargs="+")
    ag.add_argument("--streamer", default="")
    de = sub.add_parser("descubrir", help="suma los últimos VODs de los canales de un perfil")
    de.add_argument("streamer", nargs="?", default="", help="vacío = todos los perfiles")
    de.add_argument("--limite", type=int, default=3)
    sub.add_parser("correr", help="analiza todos los pendientes")
    sub.add_parser("estado")
    a = ap.parse_args(argv)

    if a.cmd == "agregar":
        with transaccion() as estado:
            for url in a.urls:
                try:
                    vod, nuevo = agregar_vod(estado, url, a.streamer)
                    print(("+ " if nuevo else "= ya estaba: ") + vod["url"])
                except ValueError as e:
                    print(f"! {e}")
    elif a.cmd == "descubrir":
        todos = mod_perfiles.cargar()
        elegidos = [p for p in todos if not a.streamer
                    or p.nombre.casefold() == a.streamer.casefold()]
        if not elegidos:
            print("No hay perfiles que coincidan. Mirá: python -m clips.perfiles listar")
            return 1
        for perfil in elegidos:
            for canal in perfil.canales:
                try:
                    encontrados = descubrir(canal, a.limite)
                except RuntimeError as e:
                    print(f"! {e}")
                    continue
                with transaccion() as estado:
                    for d in encontrados:
                        _, nuevo = agregar_vod(estado, d["url"], perfil.nombre, d["titulo"])
                        if nuevo:
                            print(f"+ [{perfil.nombre}] {d['titulo'][:60]}  {d['url']}")
    elif a.cmd == "correr":
        bien, mal = correr()
        print(f"\n{bien} analizados, {mal} con error")
    estado = cargar()
    print("\n" + _texto_estado(estado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
