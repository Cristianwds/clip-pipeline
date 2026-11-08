"""Registro de las cuentas de cada plataforma y su estado de monetización.

> **Acá no van contraseñas, tokens ni API keys.** Se guarda el *nombre* de la
> variable de entorno o la entrada del llavero donde vive cada credencial, nunca su
> valor. El archivo es texto plano en el disco.

Además del registro, guarda lo que se sabe de cada plataforma: qué programa de pago
tiene, si está disponible en Argentina y qué pide. Eso cambia seguido, así que cada
entrada lleva la fecha en que se verificó y el link para volver a chequear.

Uso:
    python -m clips.cuentas estado
    python -m clips.cuentas agregar youtube_shorts --handle @micanal
    python -m clips.cuentas listar
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

REGISTRO = Path(os.environ.get(
    "CLIP_PIPELINE_CUENTAS",
    Path.home() / ".config" / "clip-pipeline" / "cuentas.json",
))

VERIFICADO = "2026-11-08"


@dataclass
class InfoPlataforma:
    nombre: str
    programa: str
    paga_en_ar: str            # "sí" | "no" | "por invitación"
    requisitos: str
    donde_verificar: str
    nota: str = ""


# Verificado el 2026-11-08. Las plataformas cambian esto seguido: antes de tomar una
# decision, chequear en la fuente que dice `donde_verificar`.
PLATAFORMAS = {
    "youtube_shorts": InfoPlataforma(
        "YouTube Shorts", "YouTube Partner Program", "sí",
        "1.000 suscriptores + 10M de vistas de Shorts en 90 días "
        "(o 3.000 h de reproducción en videos largos)",
        "https://www.youtube.com/creators/",
        "La única con acceso abierto y reglas claras en Argentina. El RPM de Shorts "
        "es bajísimo acá: del orden de USD 0,02–0,10 cada mil vistas.",
    ),
    "tiktok": InfoPlataforma(
        "TikTok", "Creator Rewards Program", "no",
        "No disponible en Argentina: el programa está en EE.UU., Reino Unido, "
        "Alemania, Japón, Corea, Francia, México y Brasil",
        "TikTok Studio → Herramientas de creador",
        "Ojo: Argentina SÍ figura en el 'Effect Creator Rewards', que es para quien "
        "crea efectos, no videos. Es otro programa y confunde.",
    ),
    "instagram_reels": InfoPlataforma(
        "Instagram Reels", "Bonos de Reels", "por invitación",
        "Por invitación de Meta; la mayoría de los creadores con menos de 100K "
        "seguidores no cobra nada directo",
        "Instagram → Centro de cuentas → Monetización",
        "Sirve para alcance y para que te contraten marcas, no para cobrar por vistas.",
    ),
    "x": InfoPlataforma(
        "X (Twitter)", "Original Content Rewards", "sí, con condiciones",
        "18+, suscripción paga a Premium, 500 seguidores verificados y ~500.000 "
        "impresiones de usuarios verificados en 90 días. El cobro sale por Stripe",
        "https://help.x.com/en/using-x/creator-revenue-sharing",
        "El programa viejo (Creator Revenue Sharing) cerró el 2026-09-07. El que lo "
        "reemplaza se llama Original Content Rewards y está hecho justamente para NO "
        "pagar contenido copiado: es el peor encaje posible para un canal de clips de "
        "otro. Además hay que pagar Premium antes de cobrar nada, y falta confirmar "
        "que Stripe abra cuentas en Argentina.",
    ),
    "facebook": InfoPlataforma(
        "Facebook", "Monetización de contenido / Estrellas", "por invitación",
        "Estrellas: 500 seguidores durante 30 días seguidos y ser mayor de 18",
        "https://www.facebook.com/creators/",
        "El requisito de Estrellas es el más bajo de todos: es la vía más rápida a "
        "cobrar algo, aunque sea poco.",
    ),
}


@dataclass
class Cuenta:
    plataforma: str
    handle: str
    url: str = ""
    estado: str = "en creación"     # en creación | activa | suspendida
    seguidores: int = 0
    credencial: str = ""            # NOMBRE de la variable/entrada, nunca el valor
    notas: str = ""
    alta: str = field(default_factory=lambda: date.today().isoformat())


def cargar() -> list[Cuenta]:
    if not REGISTRO.exists():
        return []
    datos = json.loads(REGISTRO.read_text(encoding="utf-8"))
    return [Cuenta(**c) for c in datos]


def guardar(cuentas: list[Cuenta]) -> Path:
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    REGISTRO.write_text(
        json.dumps([asdict(c) for c in cuentas], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    REGISTRO.chmod(0o600)
    return REGISTRO


def agregar(cuenta: Cuenta) -> list[Cuenta]:
    if cuenta.plataforma not in PLATAFORMAS:
        raise ValueError(f"plataforma desconocida: {cuenta.plataforma}. "
                         f"Son: {', '.join(PLATAFORMAS)}")
    if parece_secreto(cuenta.credencial):
        raise ValueError(
            "en 'credencial' va el NOMBRE de la variable de entorno o la entrada del "
            "llavero (ej. CLIP_YT_TOKEN), nunca el token en sí"
        )
    cuentas = [c for c in cargar()
               if not (c.plataforma == cuenta.plataforma and c.handle == cuenta.handle)]
    cuentas.append(cuenta)
    guardar(cuentas)
    return cuentas


def parece_secreto(valor: str) -> bool:
    """Heurística defensiva: si parece un token de verdad, se rechaza.

    No pretende ser infalible; está para que un descuido no termine con una clave
    escrita en un archivo de texto.
    """
    if not valor:
        return False
    v = valor.strip()
    if " " in v:
        return False
    if len(v) >= 32 and not v.isupper():
        return True
    prefijos = ("sk-", "ghp_", "xox", "eyj", "Bearer", "AIza", "glpat-", "hf_")
    return v.lower().startswith(tuple(p.lower() for p in prefijos))


def que_falta(c: Cuenta) -> str:
    """Qué le falta a esta cuenta para cobrar en su plataforma."""
    info = PLATAFORMAS[c.plataforma]
    if info.paga_en_ar == "no":
        return "no paga en Argentina"
    if c.estado != "activa":
        return f"la cuenta está «{c.estado}»"
    if info.paga_en_ar == "por invitación":
        return "esperar invitación de la plataforma"
    return info.requisitos


def tabla_estado(cuentas: list[Cuenta]) -> str:
    filas = [
        "| Plataforma | Cuenta | Estado | ¿Paga en AR? | Qué falta |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    por_plataforma = {c.plataforma: c for c in cuentas}
    for clave, info in PLATAFORMAS.items():
        c = por_plataforma.get(clave)
        if c:
            filas.append(f"| {info.nombre} | {c.handle} | {c.estado} | "
                         f"{info.paga_en_ar} | {que_falta(c)} |")
        else:
            filas.append(f"| {info.nombre} | — | sin cuenta | {info.paga_en_ar} | "
                         f"crear la cuenta |")
    filas.append("")
    filas.append(f"_Datos de monetización verificados el {VERIFICADO}. "
                 "Las plataformas los cambian: volvé a chequear antes de decidir._")
    return "\n".join(filas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cuentas y estado de monetización.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("estado", help="tabla con el estado de cada plataforma")
    sub.add_parser("listar", help="las cuentas registradas, en crudo")
    p_info = sub.add_parser("info", help="qué pide una plataforma")
    p_info.add_argument("plataforma", choices=sorted(PLATAFORMAS))

    p_add = sub.add_parser("agregar", help="registrar o actualizar una cuenta")
    p_add.add_argument("plataforma", choices=sorted(PLATAFORMAS))
    p_add.add_argument("--handle", required=True)
    p_add.add_argument("--url", default="")
    p_add.add_argument("--estado", default="en creación",
                       choices=("en creación", "activa", "suspendida"))
    p_add.add_argument("--seguidores", type=int, default=0)
    p_add.add_argument("--credencial", default="",
                       help="NOMBRE de la variable de entorno, nunca el valor")
    p_add.add_argument("--notas", default="")

    p_del = sub.add_parser("quitar", help="borrar una cuenta del registro")
    p_del.add_argument("plataforma", choices=sorted(PLATAFORMAS))
    p_del.add_argument("--handle", required=True)

    a = ap.parse_args(argv)

    if a.cmd == "estado":
        print(tabla_estado(cargar()))
    elif a.cmd == "listar":
        cuentas = cargar()
        print(f"Registro: {REGISTRO}")
        for c in cuentas:
            print(f"  {c.plataforma:<18} {c.handle:<22} {c.estado:<12} "
                  f"{c.seguidores:>8} seg.  cred={c.credencial or '—'}")
        if not cuentas:
            print("  (vacío)")
    elif a.cmd == "info":
        i = PLATAFORMAS[a.plataforma]
        print(f"{i.nombre} — {i.programa}\n  ¿Paga en Argentina?  {i.paga_en_ar}"
              f"\n  Requisitos: {i.requisitos}\n  Verificar en: {i.donde_verificar}")
        if i.nota:
            print(f"  Nota: {i.nota}")
    elif a.cmd == "agregar":
        try:
            agregar(Cuenta(plataforma=a.plataforma, handle=a.handle, url=a.url,
                           estado=a.estado, seguidores=a.seguidores,
                           credencial=a.credencial, notas=a.notas))
        except ValueError as e:
            print(f"Error: {e}")
            return 2
        print(f"Guardado en {REGISTRO}")
        print(tabla_estado(cargar()))
    elif a.cmd == "quitar":
        cuentas = [c for c in cargar()
                   if not (c.plataforma == a.plataforma and c.handle == a.handle)]
        guardar(cuentas)
        print(f"Listo. Quedan {len(cuentas)} cuentas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
