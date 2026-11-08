"""Tests de la variación de formato, el gancho y el registro de cuentas."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from clips import cuentas as mod_cuentas
from clips.estilos import VARIANTES, elegir
from clips.metadatos import claves_del_tema, gancho
from clips.subtitulos import Palabra, _resaltar, a_ass, agrupar


# --- variantes -------------------------------------------------------------

def test_dos_clips_seguidos_nunca_salen_con_el_mismo_formato():
    """Es el punto de todo esto: que el canal no parezca una plantilla."""
    for etiqueta in ("", "gracioso", "épico", "tensión"):
        nombres = [elegir(i, etiqueta).nombre for i in range(len(VARIANTES))]
        assert len(set(nombres)) == len(VARIANTES), (etiqueta, nombres)


def test_la_eleccion_es_reproducible():
    assert elegir(3, "gracioso").nombre == elegir(3, "gracioso").nombre


def test_todas_las_variantes_duran_entre_15_y_30_segundos():
    """El punto dulce de retención en formato corto."""
    for v in VARIANTES:
        assert 15 <= v.duracion <= 30, f"{v.nombre} dura {v.duracion}s"


def test_el_pico_cae_en_la_primera_mitad_del_clip():
    """Si el momento bueno llega al final, el que mira ya se fue."""
    for v in VARIANTES:
        assert v.antes < v.despues, f"{v.nombre}: {v.antes}s de intro es demasiado"


# --- gancho ----------------------------------------------------------------

def test_el_gancho_no_pasa_de_ocho_palabras():
    for tema in ([], ["uno×3"], ["uno×3", "dos×4"], ["a×3", "b×4", "c×5", "d×6"]):
        assert len(gancho(tema, "gracioso").split()) <= 8, tema


def test_el_gancho_usa_las_palabras_del_chat():
    g = gancho(["romanse×8", "matala×20"], "gracioso", {"romance"})
    assert "romance" in g.lower()


def test_siempre_hay_gancho_aunque_no_haya_tema():
    assert gancho([], "").strip()


def test_el_gancho_arranca_en_mayuscula_aunque_empiece_con_signo():
    assert gancho(["romance×8", "matar×9"]).startswith("¿M") or \
           gancho(["romance×8", "matar×9"]).startswith("¿R")


def test_el_ruido_del_chat_no_entra_en_el_gancho():
    assert "xdd" not in gancho(["xdd×40", "faker×9"], "gracioso").lower()


# --- resaltado -------------------------------------------------------------

def test_resalta_solo_las_palabras_del_tema():
    out = _resaltar("que se muera romance", {"romance"}, "&H0000D7FF")
    assert "{\\c&H0000D7FF&}romance" in out
    assert "{\\c" not in out.split("romance")[0].replace("{\\c&H0000D7FF&}", "")


def test_sin_claves_no_toca_el_texto():
    assert _resaltar("hola que tal", set(), "&H0000D7FF") == "hola que tal"


def test_el_gancho_sale_como_su_propio_estilo_y_solo_al_principio():
    lineas = agrupar([Palabra(5.0, 5.4, "hola"), Palabra(5.5, 5.9, "mundo")])
    out = a_ass(lineas, gancho="¿Romance o matarla?", dur_gancho=2.5)
    ganchos = [l for l in out.splitlines() if ",Gancho,," in l]
    assert len(ganchos) == 1 and ganchos[0].startswith("Dialogue: 1,0:00:00.00,0:00:02.50")


def test_las_claves_se_normalizan_para_comparar():
    assert "matala" in claves_del_tema(["mátala×20"])


# --- cuentas ---------------------------------------------------------------

def test_no_deja_guardar_algo_que_parece_un_token():
    for v in ("ghp_aBcD1234567890aBcD1234567890aBcD12", "sk-abc123",
              "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9xxxxx"):
        assert mod_cuentas.parece_secreto(v), v


def test_el_nombre_de_una_variable_de_entorno_si_se_acepta():
    for v in ("CLIP_YT_TOKEN", "", "POSTIZ_API_KEY"):
        assert not mod_cuentas.parece_secreto(v), v


def test_agregar_rechaza_la_credencial_que_parece_secreto(tmp_path, monkeypatch):
    monkeypatch.setattr(mod_cuentas, "REGISTRO", tmp_path / "c.json")
    with pytest.raises(ValueError, match="NOMBRE"):
        mod_cuentas.agregar(mod_cuentas.Cuenta(
            plataforma="youtube_shorts", handle="@x",
            credencial="ghp_aBcD1234567890aBcD1234567890aBcD12"))


def test_el_registro_se_guarda_privado(tmp_path, monkeypatch):
    """Son datos personales: no tienen por qué ser legibles por todo el sistema."""
    monkeypatch.setattr(mod_cuentas, "REGISTRO", tmp_path / "c.json")
    mod_cuentas.agregar(mod_cuentas.Cuenta(plataforma="tiktok", handle="@x"))
    assert oct((tmp_path / "c.json").stat().st_mode)[-3:] == "600"


def test_la_tabla_dice_que_tiktok_no_paga_en_argentina(tmp_path, monkeypatch):
    monkeypatch.setattr(mod_cuentas, "REGISTRO", tmp_path / "c.json")
    mod_cuentas.agregar(mod_cuentas.Cuenta(plataforma="tiktok", handle="@x",
                                           estado="activa"))
    tabla = mod_cuentas.tabla_estado(mod_cuentas.cargar())
    assert "no paga en Argentina" in tabla


def test_una_plataforma_sin_cuenta_aparece_igual_en_la_tabla(tmp_path, monkeypatch):
    monkeypatch.setattr(mod_cuentas, "REGISTRO", tmp_path / "c.json")
    tabla = mod_cuentas.tabla_estado([])
    for info in mod_cuentas.PLATAFORMAS.values():
        assert info.nombre in tabla


def test_el_gancho_no_mayusculiza_un_nick_que_empieza_con_numero():
    """'8cho' quedaba '8Cho'."""
    assert "8Cho" not in gancho(["8cho×15", "manco×12"])
