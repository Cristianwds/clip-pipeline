"""Panel web local: la cola de VODs, la revisión de picos y los clips listos.

    python -m clips.panel            # http://localhost:8765

Está hecho con la biblioteca estándar y no con un framework por dos motivos: son
una docena de rutas, y el proyecto ya depende de tres binarios externos que hay que
mantener al día. Lo único que http.server no trae y hace falta es servir video con
`Range`, que son las veinte líneas de `rango()`.

Tres cosas que no son obvias:

- **Escucha solo en 127.0.0.1 y rechaza POST de otro origen.** El panel lanza
  descargas y escribe en disco. Sin el chequeo de origen, cualquier página abierta
  en el mismo navegador podría mandarle un POST a localhost.
- **Un solo trabajo a la vez.** El análisis y la producción usan red, CPU y disco;
  dos en paralelo no terminan antes y sí se pisan. Van a una cola con un único hilo.
- **Los trabajos largos no retienen el estado.** Leen lo que necesitan, trabajan sin
  el candado y lo vuelven a tomar solo para aplicar el resultado. Por eso se puede
  seguir revisando mientras se analiza otro VOD.

Igual que `publicar.py`, **no publica**: deja el texto para copiar, el archivo para
bajar y el link de subida de cada plataforma, y registra lo que vos marcás como
publicado.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import queue
import shutil
import threading
import time
from collections import deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import entrega, estilos, lote, perfiles as mod_perfiles, revision
from .cortar import SALIDA
from .publicar import PLATAFORMAS

HTML = Path(__file__).with_name("panel.html")

# Dónde se sube a mano en cada plataforma. Cambian cada tanto, como los límites.
SUBIDA = {
    "youtube_shorts": "https://www.youtube.com/upload",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
    "instagram_reels": "https://www.instagram.com/",
    "x": "https://x.com/compose/post",
}


# ---------------------------------------------------------------- funciones puras

def rango(cabecera: str | None, tam: int) -> tuple[int, int] | None:
    """Interpreta `Range: bytes=a-b` contra un archivo de `tam` bytes.

    Devuelve (inicio, fin) inclusive, o None si no hay rango o no se entiende (y
    entonces se manda el archivo entero). Sin esto el navegador no puede saltar
    dentro del clip: la barra de progreso del <video> no hace nada.
    """
    if not cabecera or not cabecera.startswith("bytes=") or tam <= 0:
        return None
    espec = cabecera[6:].split(",")[0].strip()
    ini, _, fin = espec.partition("-")
    try:
        if ini == "":                       # "-500": los últimos 500 bytes
            n = int(fin)
            if n <= 0:
                return None
            return (max(tam - n, 0), tam - 1)
        a = int(ini)
        b = int(fin) if fin else tam - 1
    except ValueError:
        return None
    if a < 0 or a >= tam or b < a:
        return None
    return (a, min(b, tam - 1))


def ruta_segura(base: Path, relativa: str) -> Path | None:
    """La ruta pedida, solo si cae adentro de `base`. Un `../` no puede sacar del
    disco nada que no sea un clip."""
    base = base.resolve()
    destino = (base / relativa).resolve()
    if destino != base and base not in destino.parents:
        return None
    return destino if destino.is_file() else None


def origen_local(origen: str | None) -> bool:
    """True si el pedido viene del propio panel. Sin cabecera Origin (curl, o un
    GET normal) se acepta: lo que se quiere frenar es otra página del navegador."""
    if not origen:
        return True
    return urlparse(origen).hostname in ("localhost", "127.0.0.1", "::1")


def perfil_desde_formulario(d: dict, actual: mod_perfiles.Perfil | None) -> mod_perfiles.Perfil:
    """El perfil que resulta de aplicar los campos enviados sobre el que ya existía.

    Solo se pisan los campos que vinieron. El medidor de facecam manda nada más que
    nombre, facecam y formato: armando el perfil de cero, ese guardado borraba los
    canales y el permiso.
    """
    base = asdict(actual) if actual else {"nombre": d.get("nombre", "")}
    for campo in ("nombre", "formato", "juego", "idioma", "permiso", "notas", "modelo", "voz",
                  "usuario_x"):
        if campo in d:
            base[campo] = str(d[campo]).strip()
    if "canales" in d:
        crudo = d["canales"]
        base["canales"] = crudo.split() if isinstance(crudo, str) else list(crudo)
    if "facecam" in d:
        crudo = d["facecam"]
        base["facecam"] = mod_perfiles.parsear_facecam(
            ",".join(map(str, crudo)) if isinstance(crudo, list) else crudo)
    if "subtitulos" in d:
        base["subtitulos"] = bool(d["subtitulos"])
    return mod_perfiles.desde_dict(base)


def vista(estado: dict, perfiles: list[mod_perfiles.Perfil]) -> dict:
    """Todo lo que el panel muestra, en un solo JSON."""
    libre = shutil.disk_usage(SALIDA.parent).free
    return {
        "vods": estado["vods"],
        "perfiles": [dict(asdict(p), formato_efectivo=p.formato_efectivo) for p in perfiles],
        "precision": revision.resumen(lote.filas(estado)),
        "plataformas": {k: {"nombre": p.nombre, "subida": SUBIDA.get(k, "")}
                        for k, p in PLATAFORMAS.items()},
        "opciones": {"formatos": mod_perfiles.FORMATOS, "permisos": mod_perfiles.PERMISOS,
                     "voces": mod_perfiles.VOCES,
                     "variantes": [v.nombre for v in estilos.VARIANTES],
                     "corrimiento_maximo": lote.CORRIMIENTO_MAXIMO},
        "disco_libre_gb": round(libre / 1e9, 1),
    }


# ---------------------------------------------------------------- trabajos

class Trabajos:
    """Cola de un solo hilo, con el registro de lo que se va imprimiendo."""

    def __init__(self) -> None:
        self.cola: queue.Queue = queue.Queue()
        self.registro: deque[str] = deque(maxlen=300)
        self.actual = ""
        self.en_cola: list[str] = []
        self._resto = ""
        threading.Thread(target=self._bucle, daemon=True).start()

    def encolar(self, nombre: str, fn) -> None:
        self.en_cola.append(nombre)
        self.cola.put((nombre, fn))

    # el pipeline informa con print(): el hilo redirige stdout acá
    def write(self, texto: str) -> int:
        *lineas, self._resto = (self._resto + texto).split("\n")
        for l in lineas:
            if l.strip():
                self.registro.append(f"{time.strftime('%H:%M:%S')}  {l.rstrip()}")
        return len(texto)

    def flush(self) -> None:
        pass

    def _bucle(self) -> None:
        while True:
            nombre, fn = self.cola.get()
            self.en_cola.remove(nombre)
            self.actual = nombre
            self.write(f"▶ {nombre}\n")
            try:
                with contextlib.redirect_stdout(self):
                    fn()
                self.write(f"✔ {nombre}\n")
            except Exception as e:           # un trabajo roto no puede matar la cola
                self.write(f"✖ {nombre}: {e}\n")
            self.actual = ""

    def vista(self) -> dict:
        return {"actual": self.actual, "en_cola": list(self.en_cola),
                "registro": list(self.registro)}


TRABAJOS: Trabajos | None = None
CANDADO = threading.RLock()


def mutar(fn):
    """Lee el estado, lo modifica y lo guarda. El candado del proceso ordena los hilos
    del panel; el del archivo (`lote.transaccion`), al panel con `lote correr`."""
    with CANDADO, lote.transaccion() as estado:
        return fn(estado)


def _vod_o_error(estado: dict, id_vod: str) -> dict:
    vod = lote.buscar(estado, id_vod)
    if vod is None:
        raise ValueError("ese VOD no está en la cola")
    return vod


def trabajo_analizar(id_vod: str) -> None:
    with CANDADO:
        url = _vod_o_error(lote.cargar(), id_vod)["url"]
    try:
        resultado = lote.analizar_url(url)
    except Exception as e:
        mutar(lambda est: lote.marcar_error(_vod_o_error(est, id_vod), str(e)))
        raise
    mutar(lambda est: lote.aplicar_analisis(_vod_o_error(est, id_vod), resultado))
    print(f"  {len(resultado['picos'])} picos")


def trabajo_descubrir(nombre: str, limite: int) -> None:
    perfil = mod_perfiles.obtener(nombre)
    if perfil is None or not perfil.canales:
        raise ValueError(f"el perfil «{nombre}» no tiene canales cargados")
    for canal in perfil.canales:
        for d in lote.descubrir(canal, limite):
            vod, nuevo = mutar(lambda est: lote.agregar_vod(est, d["url"], perfil.nombre, d["titulo"]))
            if nuevo:
                print(f"  + {d['titulo'][:70]}")
                TRABAJOS.encolar(f"Analizar {perfil.nombre}: {d['titulo'][:40]}",
                                 lambda i=vod["id"]: trabajo_analizar(i))


def trabajo_producir(id_vod: str, t: float, variante: str = "", corrimiento: float = 0.0) -> None:
    with CANDADO:
        estado = lote.cargar()
        vod = _vod_o_error(estado, id_vod)
        pico = lote.buscar_pico(vod, t)
        if pico is None:
            raise ValueError("ese pico no existe")
        desde = lote.proxima_fecha(estado)
    resultado = lote.producir_pico(vod, pico, mod_perfiles.obtener(vod.get("streamer", "")),
                                   desde=desde, variante=variante, corrimiento=corrimiento)
    for aviso in resultado["avisos"]:
        print(f"  aviso: {aviso}")
    mutar(lambda est: lote.aplicar_clip(lote.buscar_pico(_vod_o_error(est, id_vod), t), resultado))


def trabajo_entregar(id_vod: str) -> None:
    with CANDADO:
        vod = _vod_o_error(lote.cargar(), id_vod)
    perfil = mod_perfiles.obtener(vod.get("streamer", ""))
    r = entrega.armar(vod, SALIDA, permiso=perfil.permiso if perfil else "")
    marcados = mutar(lambda est: lote.marcar_entregado(
        _vod_o_error(est, id_vod), r["tiempos"], r["paquete"]))
    print(f"  {len(marcados)} clips en {r['paquete']}")


# ---------------------------------------------------------------- acciones (POST)

def accion(ruta: str, d: dict) -> dict:
    """Despacha un POST. Levanta ValueError con un mensaje para mostrarle al usuario."""
    if ruta == "/api/vods":
        vod, nuevo = mutar(lambda est: lote.agregar_vod(est, d.get("url", ""), d.get("streamer", "")))
        if nuevo or vod["estado"] != "analizado":
            TRABAJOS.encolar(f"Analizar {vod['url']}", lambda: trabajo_analizar(vod["id"]))
        return {"nuevo": nuevo}
    if ruta == "/api/analizar":
        mutar(lambda est: _vod_o_error(est, d["id"]))
        TRABAJOS.encolar(f"Analizar {d['id'][-24:]}", lambda: trabajo_analizar(d["id"]))
        return {}
    if ruta == "/api/descubrir":
        limite = max(1, min(int(d.get("limite") or 3), 10))
        TRABAJOS.encolar(f"Buscar VODs de {d['streamer']}",
                         lambda: trabajo_descubrir(d["streamer"], limite))
        return {}
    if ruta == "/api/veredicto":
        mutar(lambda est: lote.poner_veredicto(
            _vod_o_error(est, d["id"]), float(d["t"]), d.get("veredicto", ""), d.get("nota")))
        if d.get("producir"):
            TRABAJOS.encolar(f"Producir clip {d['id'][-12:]} @ {int(float(d['t']))}s",
                             lambda: trabajo_producir(d["id"], float(d["t"])))
        return {}
    if ruta == "/api/producir":
        mutar(lambda est: _vod_o_error(est, d["id"]))
        TRABAJOS.encolar(f"Producir clip {d['id'][-12:]} @ {int(float(d['t']))}s",
                         lambda: trabajo_producir(d["id"], float(d["t"])))
        return {}
    if ruta == "/api/facecam":
        mutar(lambda est: lote.poner_facecam(_vod_o_error(est, d["id"]), d.get("facecam")))
        return {}
    if ruta == "/api/control":
        mutar(lambda est: lote.poner_control(
            _vod_o_error(est, d["id"]), float(d["t"]), d.get("control", ""), d.get("nota")))
        return {}
    if ruta == "/api/rehacer":
        # se valida acá y no en el trabajo: un valor mal puesto tiene que volver como
        # error del formulario, no aparecer tres minutos después en el registro
        variante, corrimiento = lote.validar_ajuste(
            d.get("variante"), float(d.get("corrimiento") or 0))
        mutar(lambda est: lote.poner_control(
            _vod_o_error(est, d["id"]), float(d["t"]), "rehacer", d.get("nota")))
        TRABAJOS.encolar(f"Producir clip {d['id'][-12:]} @ {int(float(d['t']))}s",
                         lambda: trabajo_producir(d["id"], float(d["t"]), variante, corrimiento))
        return {}
    if ruta == "/api/entregar":
        with CANDADO:
            vod = _vod_o_error(lote.cargar(), d["id"])
        if not entrega.seleccionar(vod):
            raise ValueError("no hay clips con el visto bueno sin entregar en este directo")
        TRABAJOS.encolar(f"Armar entrega {d['id'][-12:]}", lambda: trabajo_entregar(d["id"]))
        return {}
    if ruta == "/api/publicado":
        if d.get("plataforma") not in PLATAFORMAS:
            raise ValueError("plataforma desconocida")
        vistas = d.get("vistas")
        vistas = int(vistas) if vistas not in (None, "") else None

        def publicar(est: dict) -> None:
            vod = _vod_o_error(est, d["id"])
            # el aviso de la pestaña Clips era solo un cartel: se podía anotar una
            # publicación de un streamer al que nunca se le pidió permiso
            lote.exigir_permiso(vod, mod_perfiles.obtener(vod.get("streamer", "")))
            lote.marcar_publicado(vod, float(d["t"]), d["plataforma"], d.get("url", ""), vistas)

        mutar(publicar)
        return {}
    if ruta == "/api/perfiles":
        with CANDADO:
            todos = mod_perfiles.cargar()
            p = perfil_desde_formulario(d, mod_perfiles.obtener(d.get("nombre", ""), todos))
            mod_perfiles.guardar(mod_perfiles.poner(todos, p))
        return {}
    raise LookupError(ruta)


# ---------------------------------------------------------------- HTTP

class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:      # el registro que importa es el de trabajos
        pass

    def _json(self, datos: dict, codigo: int = 200) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:
        ruta = urlparse(self.path).path
        if ruta == "/":
            cuerpo = HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)
        elif ruta == "/api/estado":
            with CANDADO:
                datos = vista(lote.cargar(), mod_perfiles.cargar())
            datos["trabajos"] = TRABAJOS.vista()
            self._json(datos)
        elif ruta.startswith("/clips/"):
            self._archivo(unquote(ruta[len("/clips/"):]))
        elif ruta == "/api/cuadro":
            self._cuadro(parse_qs(urlparse(self.path).query))
        else:
            self._json({"error": "no existe"}, 404)

    def _cuadro(self, q: dict) -> None:
        """Un cuadro del VOD para medir la facecam. Se guarda: pedir dos veces el
        mismo segundo no vuelve a ir a la plataforma."""
        from .cortar import cuadro
        try:
            id_vod, t = q["id"][0], int(float(q.get("t", ["1800"])[0]))
            with CANDADO:
                vod = _vod_o_error(lote.cargar(), id_vod)
            destino = SALIDA / vod["id"] / f"cuadro_{t}.jpg"
            if not destino.exists():
                cuadro(vod["url"], t, destino)
        except (KeyError, ValueError) as e:
            self._json({"error": str(e)}, 400)
            return
        except Exception as e:
            self._json({"error": str(e)}, 502)
            return
        self._archivo(f"{vod['id']}/{destino.name}")

    def _archivo(self, relativa: str) -> None:
        archivo = ruta_segura(SALIDA, relativa)
        if archivo is None:
            self._json({"error": "no existe"}, 404)
            return
        tam = archivo.stat().st_size
        r = rango(self.headers.get("Range"), tam)
        ini, fin = r if r else (0, tam - 1)
        self.send_response(206 if r else 200)
        tipo = {"mp4": "video/mp4", "jpg": "image/jpeg", "txt": "text/plain; charset=utf-8",
                "zip": "application/zip"}.get(
            archivo.suffix.lstrip("."), "application/octet-stream")
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fin - ini + 1))
        if r:
            self.send_header("Content-Range", f"bytes {ini}-{fin}/{tam}")
        self.end_headers()
        with archivo.open("rb") as f:
            f.seek(ini)
            falta = fin - ini + 1
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                while falta > 0:
                    trozo = f.read(min(1 << 16, falta))
                    if not trozo:
                        break
                    self.wfile.write(trozo)
                    falta -= len(trozo)

    def do_POST(self) -> None:
        if not origen_local(self.headers.get("Origin")):
            self._json({"error": "origen no permitido"}, 403)
            return
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            datos = json.loads(self.rfile.read(largo) or b"{}")
            self._json({"ok": True, **accion(urlparse(self.path).path, datos)})
        except LookupError:
            self._json({"error": "no existe"}, 404)
        except (ValueError, KeyError, TypeError) as e:
            mensaje = f"falta el campo {e}" if isinstance(e, KeyError) else str(e)
            self._json({"error": mensaje}, 400)


def main(argv: list[str] | None = None) -> int:
    global TRABAJOS
    ap = argparse.ArgumentParser(description="Panel web local del pipeline.")
    ap.add_argument("--puerto", type=int, default=8765)
    a = ap.parse_args(argv)
    TRABAJOS = Trabajos()
    servidor = ThreadingHTTPServer(("127.0.0.1", a.puerto), Manejador)
    print(f"Panel en http://localhost:{a.puerto}  (Ctrl+C para cerrar)", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
