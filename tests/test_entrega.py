"""Tests de la entrega y del control del clip. Archivos de mentira en tmp_path."""
import zipfile
from datetime import datetime

import pytest

from clips import entrega, lote, panel, perfiles
from clips.picos import Pico

DIA = datetime(2026, 11, 12, 10, 0)


def _vod(tmp_path, *controles, titulos=None):
    """Un VOD con un clip producido por cada control, y su archivo en disco."""
    estado = lote.nuevo_estado()
    vod, _ = lote.agregar_vod(estado, "https://www.twitch.tv/videos/1", "Alguien")
    for n, control in enumerate(controles, 1):
        p = lote.pico_a_dict(Pico(t=100.0 * n, score=5.0, tasa=3.0, tema=["algo×3"], foco=0.3), n)
        titulo = (titulos or {}).get(n, f"Título número {n}")
        p.update(veredicto="bueno", clip=f"{vod['id']}/pico_{n:02d}.mp4", control=control,
                 publicacion={"titulo": titulo, "medidas": {"segundos": 20.0},
                              "plataformas": {
                                  "tiktok": {"nombre": "TikTok", "titulo": titulo,
                                             "descripcion": "texto #clips", "problemas": []},
                                  "x": {"nombre": "X", "titulo": titulo,
                                        "descripcion": "texto corto", "problemas": ["dura 200 s"]}}})
        vod["picos"].append(p)
        archivo = tmp_path / p["clip"]
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(b"video %d" % n)
    return estado, vod


# ---------------------------------------------------------------- control

def test_no_se_controla_un_clip_que_no_existe():
    estado = lote.nuevo_estado()
    vod, _ = lote.agregar_vod(estado, "https://www.twitch.tv/videos/1")
    vod["picos"] = [lote.pico_a_dict(Pico(t=100.0, score=5.0, tasa=3.0), 1)]
    with pytest.raises(ValueError, match="no tiene un clip"):
        lote.poner_control(vod, 100.0, "ok")


def test_el_control_solo_acepta_sus_valores_y_se_puede_deshacer(tmp_path):
    _, vod = _vod(tmp_path, "")
    with pytest.raises(ValueError):
        lote.poner_control(vod, 100.0, "bueno")       # eso es un veredicto, no un control
    lote.poner_control(vod, 100.0, "ok")
    assert lote.poner_control(vod, 100.0, "")["control"] == ""


def test_rehacer_un_clip_le_saca_el_visto_bueno_y_conserva_el_motivo(tmp_path):
    # el «ok» era de otro archivo: el clip nuevo no lo miró nadie
    _, vod = _vod(tmp_path, "ok")
    pico = vod["picos"][0]
    lote.poner_control(vod, 100.0, "rehacer", "arranca tarde")
    lote.aplicar_clip(pico, {"clip": "v/nuevo.mp4", "publicacion": {},
                             "ajuste": {"variante": "contexto", "corrimiento": -3.0}})
    assert (pico["control"], pico["nota_control"]) == ("", "arranca tarde")
    assert pico["ajuste"] == {"variante": "contexto", "corrimiento": -3.0}
    assert pico["t"] == 100.0                 # el corrimiento no cambia la identidad del pico


@pytest.mark.parametrize("variante, corrimiento", [("direkto", 0), ("", 40), ("crudo", -15.5)])
def test_un_ajuste_que_no_vale_se_rechaza_antes_de_producir(variante, corrimiento):
    with pytest.raises(ValueError):
        lote.validar_ajuste(variante, corrimiento)


def test_un_ajuste_vacio_es_el_clip_de_siempre():
    assert lote.validar_ajuste(None, 0) == ("", 0.0)


def test_reanalizar_no_pierde_el_control_ni_la_entrega(tmp_path):
    _, vod = _vod(tmp_path, "ok")
    vod["picos"][0]["entregado"] = {"fecha": "2026-11-12T10:00", "paquete": "x.zip"}
    nuevos = [lote.pico_a_dict(Pico(t=102.0, score=5.0, tasa=3.0), 1)]
    (fusionado,) = lote.fusionar_picos(vod["picos"], nuevos)
    assert fusionado["control"] == "ok" and fusionado["entregado"]["paquete"] == "x.zip"


def test_a_un_clip_entregado_no_se_le_saca_el_visto_bueno(tmp_path):
    _, vod = _vod(tmp_path, "ok")
    lote.marcar_entregado(vod, [100.0], "x.zip", ahora=DIA)
    with pytest.raises(ValueError, match="ya se entregó"):
        lote.poner_control(vod, 100.0, "rehacer")


# ---------------------------------------------------------------- qué entra

def test_solo_entran_los_clips_con_visto_bueno_y_sin_entregar(tmp_path):
    _, vod = _vod(tmp_path, "ok", "", "rehacer", "ok")
    vod["picos"][3]["entregado"] = {"fecha": "2026-11-10T10:00", "paquete": "viejo.zip"}
    assert [p["t"] for p in entrega.seleccionar(vod)] == [100.0]


def test_un_veredicto_bueno_sin_control_no_alcanza(tmp_path):
    # el veredicto se pone mirando el VOD, antes de que exista el archivo
    _, vod = _vod(tmp_path, "")
    assert vod["picos"][0]["veredicto"] == "bueno"
    assert entrega.seleccionar(vod) == []


def test_los_clips_van_en_el_orden_del_directo_y_no_del_ranking(tmp_path):
    _, vod = _vod(tmp_path, "ok", "ok")
    vod["picos"].reverse()
    assert [p["t"] for p in entrega.seleccionar(vod)] == [100.0, 200.0]


@pytest.mark.parametrize("titulo, esperado", [
    ("¿Mátala o romance?", "01_matala-o-romance"),
    ("  ", "01_clip"),
    ("a" * 80, "01_" + "a" * 48),
    ("Ñandú / 8cho: «épico»", "01_nandu-8cho-epico"),
])
def test_el_nombre_del_archivo_sobrevive_a_cualquier_app(titulo, esperado):
    assert entrega.nombre_base(1, titulo) == esperado


# ---------------------------------------------------------------- el paquete

def test_el_paquete_trae_cada_video_con_su_texto_y_el_leeme(tmp_path):
    _, vod = _vod(tmp_path, "ok", "ok", titulos={1: "Romance vs malas", 2: "El clap"})
    r = entrega.armar(vod, tmp_path, ahora=DIA)
    assert r["tiempos"] == [100.0, 200.0]
    with zipfile.ZipFile(tmp_path / r["paquete"]) as z:
        nombres = sorted(n for n in z.namelist() if not n.endswith("/"))
        assert nombres == [f"entrega_2026-11-12/{n}" for n in (
            "01_romance-vs-malas.mp4", "01_romance-vs-malas.txt",
            "02_el-clap.mp4", "02_el-clap.txt", "LEEME.txt")]
        assert z.read("entrega_2026-11-12/02_el-clap.mp4") == b"video 2"
        leeme = z.read("entrega_2026-11-12/LEEME.txt").decode()
    assert "02_el-clap.mp4" in leeme and "0h03m20s" in leeme and vod["url"] in leeme


def test_el_texto_de_x_no_repite_el_titulo_y_avisa_si_no_entra(tmp_path):
    _, vod = _vod(tmp_path, "ok", titulos={1: "Romance vs malas"})
    texto = entrega.texto_del_clip(vod["picos"][0])
    tiktok, x = texto.split("=== X ===")
    assert "Romance vs malas" in tiktok and "texto #clips" in tiktok
    assert "Romance vs malas" not in x and "texto corto" in x and "dura 200 s" in x


def test_el_leeme_solo_dice_que_no_se_publico_si_es_verdad(tmp_path):
    _, vod = _vod(tmp_path, "ok", "ok")
    elegidos = [("01_a", vod["picos"][0]), ("02_b", vod["picos"][1])]
    assert "Ninguno de estos clips se publicó" in entrega.leeme(vod, elegidos, DIA)
    vod["picos"][1]["publicado"] = {"tiktok": {"url": "https://tiktok.com/x"}}
    assert "Ninguno de estos clips se publicó" not in entrega.leeme(vod, elegidos, DIA)


def test_sin_permiso_concedido_el_leeme_lo_presenta_como_muestra(tmp_path):
    _, vod = _vod(tmp_path, "ok")
    elegidos = [("01_a", vod["picos"][0])]
    assert "Es una muestra" in entrega.leeme(vod, elegidos, DIA, permiso="sin pedir")
    assert "Es una muestra" not in entrega.leeme(vod, elegidos, DIA, permiso="concedido")


def test_si_falta_un_video_no_se_arma_un_paquete_incompleto(tmp_path):
    _, vod = _vod(tmp_path, "ok", "ok")
    (tmp_path / vod["picos"][1]["clip"]).unlink()
    with pytest.raises(RuntimeError, match="pico_02.mp4"):
        entrega.armar(vod, tmp_path, ahora=DIA)
    assert not (tmp_path / vod["id"] / "entregas").exists()


def test_no_se_le_arma_una_entrega_a_quien_nego_el_permiso(tmp_path):
    _, vod = _vod(tmp_path, "ok")
    with pytest.raises(ValueError, match="negó"):
        entrega.armar(vod, tmp_path, ahora=DIA, permiso="negado")


def test_dos_entregas_del_mismo_dia_no_se_pisan(tmp_path):
    _, vod = _vod(tmp_path, "ok", "ok")
    vod["picos"][1]["control"] = ""
    primera = entrega.armar(vod, tmp_path, ahora=DIA)
    lote.marcar_entregado(vod, primera["tiempos"], primera["paquete"], ahora=DIA)
    vod["picos"][1]["control"] = "ok"
    segunda = entrega.armar(vod, tmp_path, ahora=DIA)
    assert primera["paquete"] != segunda["paquete"]
    assert segunda["tiempos"] == [200.0]                 # lo ya entregado no se manda de nuevo
    assert (tmp_path / primera["paquete"]).is_file() and (tmp_path / segunda["paquete"]).is_file()


def test_no_se_marca_entregado_un_clip_al_que_le_sacaron_el_visto_bueno(tmp_path):
    # pasa si alguien toca «Deshacer» mientras se arma el zip
    _, vod = _vod(tmp_path, "ok", "ok")
    vod["picos"][1]["control"] = ""
    marcados = lote.marcar_entregado(vod, [100.0, 200.0], "x.zip", ahora=DIA)
    assert [p["t"] for p in marcados] == [100.0]
    assert vod["picos"][1]["entregado"] is None


# ---------------------------------------------------------------- por el panel

class _ColaInmediata:
    """Corre el trabajo en el momento, para probar la acción completa sin hilos."""
    def encolar(self, nombre, fn):
        fn()


def test_el_panel_arma_la_entrega_y_la_anota_en_el_estado(tmp_path, monkeypatch):
    estado, vod = _vod(tmp_path, "ok", "")
    ruta = tmp_path / "lote.json"
    lote.guardar(estado, ruta)
    monkeypatch.setattr(lote, "ESTADO", ruta)
    monkeypatch.setattr(panel, "SALIDA", tmp_path)
    monkeypatch.setattr(panel, "TRABAJOS", _ColaInmediata())
    monkeypatch.setattr(panel.mod_perfiles, "cargar",
                        lambda ruta=None: [perfiles.Perfil("Alguien", permiso="pedido")])

    panel.accion("/api/entregar", {"id": vod["id"]})
    picos = lote.cargar(ruta)["vods"][0]["picos"]
    assert picos[0]["entregado"]["paquete"].endswith(".zip") and picos[1]["entregado"] is None
    assert (tmp_path / picos[0]["entregado"]["paquete"]).is_file()

    with pytest.raises(ValueError, match="no hay clips"):
        panel.accion("/api/entregar", {"id": vod["id"]})


def test_rehacer_con_un_valor_malo_vuelve_como_error_y_no_encola(tmp_path, monkeypatch):
    estado, vod = _vod(tmp_path, "")
    ruta = tmp_path / "lote.json"
    lote.guardar(estado, ruta)
    monkeypatch.setattr(lote, "ESTADO", ruta)
    encolados = []
    monkeypatch.setattr(panel, "TRABAJOS", type("C", (), {"encolar": lambda s, n, f: encolados.append(n)})())
    with pytest.raises(ValueError):
        panel.accion("/api/rehacer", {"id": vod["id"], "t": 100.0, "corrimiento": 60})
    assert encolados == [] and lote.cargar(ruta)["vods"][0]["picos"][0]["control"] == ""
    panel.accion("/api/rehacer", {"id": vod["id"], "t": 100.0, "corrimiento": -3, "nota": "tarde"})
    assert len(encolados) == 1
    assert lote.cargar(ruta)["vods"][0]["picos"][0]["nota_control"] == "tarde"
