"""Perfil por streamer: todo lo que es constante de un canal y hoy iba por flags.

Con un solo streamer, `--facecam 4,268,320,188 --streamer X --juego Y` en cada
corrida se aguanta. Con tres ya no, y en modo lote directamente no hay quién tipee
los flags. El perfil guarda eso una vez.

También guarda el **permiso**: si el streamer dijo que sí a los clips. No es un dato
técnico, pero es lo que decide si un clip se puede publicar, así que vive al lado de
lo demás y el panel lo muestra antes de publicar.

Vive en ~/.config/clip-pipeline/, igual que el registro de cuentas: es configuración
de quien corre el pipeline, no del proyecto. No guarda credenciales.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

REGISTRO = Path(os.environ.get(
    "CLIP_PIPELINE_PERFILES", Path.home() / ".config/clip-pipeline/perfiles.json"))

FORMATOS = ("desenfoque", "recorte", "dividido", "nada")
PERMISOS = ("sin pedir", "pedido", "concedido", "negado")


@dataclass
class Perfil:
    nombre: str
    canales: list[str] = field(default_factory=list)   # URLs donde aparecen sus VODs
    facecam: list[int] | None = None                   # x, y, ancho, alto del original
    formato: str = "desenfoque"
    juego: str = ""
    idioma: str = ""              # vacío = que lo detecte whisper
    modelo: str = "small"
    subtitulos: bool = True
    permiso: str = "sin pedir"
    notas: str = ""

    @property
    def formato_efectivo(self) -> str:
        """El dividido sin facecam no se puede armar: cae a desenfoque en vez de
        fallar a mitad de un lote, que es peor que un clip con otro layout."""
        if self.formato == "dividido" and not self.facecam:
            return "desenfoque"
        return self.formato

    @property
    def puede_publicar(self) -> bool:
        return self.permiso == "concedido"


def parsear_facecam(texto: str | None) -> list[int] | None:
    """'4,268,320,188' -> [4, 268, 320, 188]. Vacío -> None."""
    if not texto or not str(texto).strip():
        return None
    try:
        v = [int(x) for x in str(texto).replace(" ", "").split(",")]
    except ValueError:
        raise ValueError("la facecam son cuatro enteros: X,Y,ANCHO,ALTO") from None
    if len(v) != 4 or v[2] <= 0 or v[3] <= 0 or v[0] < 0 or v[1] < 0:
        raise ValueError("la facecam son cuatro enteros: X,Y,ANCHO,ALTO (ancho y alto > 0)")
    return v


def validar(p: Perfil) -> Perfil:
    p.nombre = p.nombre.strip()
    if not p.nombre:
        raise ValueError("el perfil necesita un nombre")
    if p.formato not in FORMATOS:
        raise ValueError(f"formato debe ser uno de {', '.join(FORMATOS)}")
    if p.permiso not in PERMISOS:
        raise ValueError(f"permiso debe ser uno de {', '.join(PERMISOS)}")
    p.canales = [c.strip() for c in p.canales if c and c.strip()]
    for c in p.canales:
        if not c.startswith(("http://", "https://")):
            raise ValueError(f"el canal tiene que ser una URL completa: {c}")
    if p.facecam is not None:
        p.facecam = parsear_facecam(",".join(str(x) for x in p.facecam))
    return p


def desde_dict(d: dict) -> Perfil:
    """Ignora claves que no conoce: un perfil escrito por una versión más nueva no
    tiene que romper la carga de todos los demás."""
    campos = Perfil.__dataclass_fields__
    return Perfil(**{k: v for k, v in d.items() if k in campos})


def cargar(ruta: Path | None = None) -> list[Perfil]:
    ruta = ruta or REGISTRO
    if not ruta.exists():
        return []
    return [desde_dict(d) for d in json.loads(ruta.read_text(encoding="utf-8"))]


def guardar(perfiles: list[Perfil], ruta: Path | None = None) -> Path:
    ruta = ruta or REGISTRO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps([asdict(p) for p in perfiles], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    ruta.chmod(0o600)
    return ruta


def _clave(nombre: str) -> str:
    return nombre.strip().casefold()


def obtener(nombre: str, perfiles: list[Perfil] | None = None) -> Perfil | None:
    if not nombre:
        return None
    perfiles = cargar() if perfiles is None else perfiles
    return next((p for p in perfiles if _clave(p.nombre) == _clave(nombre)), None)


def poner(perfiles: list[Perfil], nuevo: Perfil) -> list[Perfil]:
    """Agrega o reemplaza por nombre, sin distinguir mayúsculas."""
    validar(nuevo)
    resto = [p for p in perfiles if _clave(p.nombre) != _clave(nuevo.nombre)]
    return sorted(resto + [nuevo], key=lambda p: _clave(p.nombre))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Perfiles por streamer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar")
    ag = sub.add_parser("agregar", help="crea o reemplaza un perfil")
    ag.add_argument("nombre")
    ag.add_argument("--canal", action="append", default=[], help="URL del canal (repetible)")
    ag.add_argument("--facecam", default=None, metavar="X,Y,ANCHO,ALTO")
    ag.add_argument("--formato", default="desenfoque", choices=FORMATOS)
    ag.add_argument("--juego", default="")
    ag.add_argument("--idioma", default="")
    ag.add_argument("--permiso", default="sin pedir", choices=PERMISOS)
    ag.add_argument("--notas", default="")
    a = ap.parse_args(argv)

    if a.cmd == "agregar":
        try:
            p = Perfil(nombre=a.nombre, canales=a.canal, facecam=parsear_facecam(a.facecam),
                       formato=a.formato, juego=a.juego, idioma=a.idioma,
                       permiso=a.permiso, notas=a.notas)
            ruta = guardar(poner(cargar(), p))
        except ValueError as e:
            print(e)
            return 2
        print(f"perfil «{p.nombre}» guardado en {ruta}")
        return 0

    perfiles = cargar()
    if not perfiles:
        print("No hay perfiles. Creá uno con: python -m clips.perfiles agregar NOMBRE --canal URL")
        return 0
    for p in perfiles:
        cam = ",".join(map(str, p.facecam)) if p.facecam else "—"
        print(f"{p.nombre:<18} {p.formato_efectivo:<11} facecam {cam:<18} permiso: {p.permiso}")
        for c in p.canales:
            print(f"    {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
