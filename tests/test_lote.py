"""Tests del modo lote, los perfiles, la medición de precisión y el panel.

Como el resto del proyecto: funciones puras con datos sintéticos, nada de red.
"""
from datetime import datetime

import pytest

from clips import lote, panel, perfiles, revision
from clips.picos import Pico


def _pico(t, puesto, **kw):
    d = lote.pico_a_dict(Pico(t=t, score=5.0, tasa=3.0, tema=["algo×3"], foco=0.3), puesto)
    d.update(kw)
    return d


def _estado_con(*veredictos, id_vod="https_www_twitch_tv_videos_1"):
    estado = lote.nuevo_estado()
    vod, _ = lote.agregar_vod(estado, "https://www.twitch.tv/videos/1", "Alguien")
    vod["picos"] = [_pico(100.0 * n, n, veredicto=v) for n, v in enumerate(veredictos, 1)]
    return estado, vod


# ---------------------------------------------------------------- URLs

@pytest.mark.parametrize("url, esperada", [
    ("https://youtu.be/mq_ceLA5aro?t=120", "https://www.youtube.com/watch?v=mq_ceLA5aro"),
    ("https://www.youtube.com/live/mq_ceLA5aro?si=abc", "https://www.youtube.com/watch?v=mq_ceLA5aro"),
    ("https://m.youtube.com/watch?v=mq_ceLA5aro&t=6507s", "https://www.youtube.com/watch?v=mq_ceLA5aro"),
    ("https://twitch.tv/videos/2922521276?t=1h2m3s", "https://www.twitch.tv/videos/2922521276"),
    ("https://www.twitch.tv/videos/2922521276/", "https://www.twitch.tv/videos/2922521276"),
])
def test_la_misma_url_escrita_distinto_se_normaliza_igual(url, esperada):
    assert lote.normalizar(url) == esperada


def test_el_id_coincide_con_el_cache_de_chat_que_ya_existia():
    # si cambiara, los chats ya bajados dejarían de encontrarse y se rebajarían
    vod, _ = lote.agregar_vod(lote.nuevo_estado(), "https://youtu.be/mq_ceLA5aro")
    assert vod["id"] == "https_www_youtube_com_watch_v_mq_ceLA5aro"


def test_no_se_agrega_dos_veces_el_mismo_vod():
    estado = lote.nuevo_estado()
    _, nuevo1 = lote.agregar_vod(estado, "https://www.twitch.tv/videos/123")
    vod, nuevo2 = lote.agregar_vod(estado, "https://twitch.tv/videos/123?t=5m", "Alguien")
    assert (nuevo1, nuevo2) == (True, False)
    assert len(estado["vods"]) == 1
    assert vod["streamer"] == "Alguien"      # se completa el dato que faltaba


@pytest.mark.parametrize("url", ["https://www.twitch.tv/ibai", "https://example.com/video", "hola"])
def test_una_url_que_no_es_un_vod_se_rechaza(url):
    with pytest.raises(ValueError):
        lote.agregar_vod(lote.nuevo_estado(), url)


# ---------------------------------------------------------------- reanálisis

def test_reanalizar_no_pierde_los_veredictos():
    viejos = [_pico(1000.0, 1, veredicto="bueno", nota="la votación"), _pico(2000.0, 2)]
    # el detector cambió: el primer pico se corrió un bin y pasó al segundo puesto
    nuevos = [_pico(2000.0, 1), _pico(1005.0, 2)]
    resultado = lote.fusionar_picos(viejos, nuevos)
    assert [(p["t"], p["veredicto"], p["nota"]) for p in resultado] == [
        (2000.0, "", ""), (1005.0, "bueno", "la votación")]


def test_un_pico_revisado_que_ya_no_se_detecta_se_conserva_sin_puesto():
    viejos = [_pico(1000.0, 1, veredicto="malo"), _pico(5000.0, 2)]
    resultado = lote.fusionar_picos(viejos, [_pico(3000.0, 1)])
    assert [(p["t"], p["puesto"], p["veredicto"]) for p in resultado] == [
        (3000.0, 1, ""), (1000.0, 0, "malo")]
    # y no cuenta para la precisión: ya no es lo que el detector devuelve
    estado = {"vods": [{"id": "v", "plataforma": "twitch", "picos": resultado}]}
    assert len(lote.filas(estado)) == 1


def test_un_veredicto_no_se_hereda_dos_veces():
    viejos = [_pico(1000.0, 1, veredicto="bueno")]
    resultado = lote.fusionar_picos(viejos, [_pico(996.0, 1), _pico(1004.0, 2)])
    assert sum(1 for p in resultado if p["veredicto"]) == 1


def test_aplicar_analisis_limpia_el_error_anterior():
    estado, vod = _estado_con()
    lote.marcar_error(vod, "se cayó la red")
    lote.aplicar_analisis(vod, {"picos": [_pico(50.0, 1)], "mensajes": 10,
                                "duracion": 3600.0, "umbral": 4.2})
    assert (vod["estado"], vod["error"], len(vod["picos"])) == ("analizado", "", 1)


def test_el_lote_no_pisa_un_veredicto_puesto_mientras_corria(tmp_path, monkeypatch):
    ruta = tmp_path / "lote.json"
    with lote.transaccion(ruta) as estado:
        ya, _ = lote.agregar_vod(estado, "https://www.twitch.tv/videos/1")
        lote.aplicar_analisis(ya, {"picos": [_pico(100.0, 1)], "mensajes": 1,
                                   "duracion": 1.0, "umbral": 4.0})
        lote.agregar_vod(estado, "https://www.twitch.tv/videos/2")
        lote.agregar_vod(estado, "https://www.twitch.tv/videos/3")

    def analizar_falso(url):
        # mientras "baja el chat", alguien revisa en el panel (otro proceso)
        with lote.transaccion(ruta) as estado:
            lote.poner_veredicto(estado["vods"][0], 100.0, "bueno")
        if url.endswith("/3"):
            raise RuntimeError("VOD solo para suscriptores")
        return {"picos": [_pico(50.0, 1)], "mensajes": 5, "duracion": 60.0, "umbral": 4.0}

    monkeypatch.setattr(lote, "analizar_url", analizar_falso)
    assert lote.correr(ruta, log=lambda _: None) == (1, 1)
    vods = lote.cargar(ruta)["vods"]
    assert vods[0]["picos"][0]["veredicto"] == "bueno"
    assert [v["estado"] for v in vods] == ["analizado", "analizado", "error"]
    assert vods[2]["error"] == "VOD solo para suscriptores"


def test_una_transaccion_que_falla_no_guarda_nada(tmp_path):
    ruta = tmp_path / "lote.json"
    with lote.transaccion(ruta) as estado:
        lote.agregar_vod(estado, "https://www.twitch.tv/videos/1")
    with pytest.raises(ValueError):
        with lote.transaccion(ruta) as estado:
            lote.agregar_vod(estado, "https://www.twitch.tv/videos/2")
            lote.agregar_vod(estado, "esto no es una url")
    assert len(lote.cargar(ruta)["vods"]) == 1


# ---------------------------------------------------------------- veredictos y publicación

def test_veredicto_invalido_o_pico_inexistente_se_rechazan():
    _, vod = _estado_con("")
    with pytest.raises(ValueError):
        lote.poner_veredicto(vod, 100.0, "genial")
    with pytest.raises(ValueError):
        lote.poner_veredicto(vod, 999.0, "bueno")
    lote.poner_veredicto(vod, 100.0, "bueno", " nota ")
    assert (vod["picos"][0]["veredicto"], vod["picos"][0]["nota"]) == ("bueno", "nota")
    lote.poner_veredicto(vod, 100.0, "")           # deshacer
    assert vod["picos"][0]["veredicto"] == ""


def test_no_se_marca_publicado_un_clip_que_no_existe():
    _, vod = _estado_con("bueno")
    with pytest.raises(ValueError):
        lote.marcar_publicado(vod, 100.0, "tiktok", "https://tiktok.com/x")


def test_las_vistas_se_actualizan_sin_pisar_la_url_ni_la_fecha():
    _, vod = _estado_con("bueno")
    vod["picos"][0]["clip"] = "v/pico.mp4"
    dia = datetime(2026, 11, 10, 19, 0)
    lote.marcar_publicado(vod, 100.0, "tiktok", "https://tiktok.com/x", ahora=dia)
    r = lote.marcar_publicado(vod, 100.0, "tiktok", vistas=1200, ahora=datetime(2026, 11, 17))
    assert r == {"fecha": "2026-11-10T19:00", "url": "https://tiktok.com/x", "vistas": 1200}


def test_el_proximo_clip_se_agenda_el_dia_siguiente_al_ultimo():
    estado, vod = _estado_con("bueno")
    ahora = datetime(2026, 11, 10, 15, 0)
    assert lote.proxima_fecha(estado, ahora) == ahora
    vod["picos"][0]["publicacion"] = {"plataformas": {
        "tiktok": {"cuando": "2026-11-12T19:00"}, "x": {"cuando": "2026-11-12T21:00"}}}
    assert lote.proxima_fecha(estado, ahora) == datetime(2026, 11, 13, 0, 0)


# ---------------------------------------------------------------- listado de canal

def test_del_listado_solo_salen_vods_terminados_y_largos():
    datos = {"entries": [
        {"url": "https://www.twitch.tv/videos/1", "title": "largo", "duration": 7200},
        {"url": "https://www.twitch.tv/videos/2", "title": "clip resubido", "duration": 300},
        {"url": "https://www.youtube.com/watch?v=abc", "title": "en vivo", "duration": None},
        {"url": "https://www.youtube.com/watch?v=def", "title": "programado",
         "duration": 9000, "live_status": "is_upcoming"},
        None,
        {"url": "https://youtu.be/ghi?t=4", "title": "otro", "duration": 5000},
    ]}
    assert [d["url"] for d in lote.parsear_listado(datos)] == [
        "https://www.twitch.tv/videos/1", "https://www.youtube.com/watch?v=ghi"]


# ---------------------------------------------------------------- precisión

def test_lo_no_revisado_no_cuenta_como_malo():
    m = revision.medir([{"veredicto": "bueno"}, {"veredicto": ""}, {"veredicto": "malo"}])
    assert (m["revisados"], m["total"], m["precision"]) == (2, 3, 0.5)


def test_sin_revisados_no_hay_precision_en_vez_de_cero():
    m = revision.medir([{"veredicto": ""}])
    assert m["precision"] is None and m["intervalo"] is None


def test_dudoso_es_error_en_la_estricta_y_acierto_en_la_laxa():
    m = revision.medir([{"veredicto": v} for v in ("bueno", "dudoso", "dudoso", "malo")])
    assert (m["precision"], m["precision_laxa"]) == (0.25, 0.75)


def test_el_intervalo_se_achica_con_mas_datos_y_no_colapsa_en_los_extremos():
    lo_chico, hi_chico = revision.wilson(8, 10)
    lo_grande, hi_grande = revision.wilson(80, 100)
    assert hi_grande - lo_grande < hi_chico - lo_chico
    # 5 de 5 no es «100% seguro»: el intervalo normal diría [1, 1]
    lo, hi = revision.wilson(5, 5)
    assert hi == 1.0 and 0.4 < lo < 0.7
    assert revision.wilson(0, 0) is None


def test_la_precision_en_k_ignora_los_puestos_de_mas_abajo():
    estado, _ = _estado_con("bueno", "bueno", "malo", "malo", "malo", "malo")
    r = revision.resumen(lote.filas(estado), k=2)
    assert r["top_k"]["precision"] == 1.0
    assert r["todos"]["precision"] == pytest.approx(2 / 6)


def test_los_tramos_no_pierden_ni_duplican_filas():
    filas = [{"prioridad": x, "veredicto": "bueno"} for x in (0.5, 3.0, 3.9, 4.0, 6.9, 7.0, 20)]
    tramos = revision.por_tramo(filas, "prioridad", [3, 4, 5, 7])
    assert [t["total"] for t in tramos] == [1, 2, 1, 1, 2]
    assert [t["tramo"] for t in tramos] == ["< 3", "3 – 4", "4 – 5", "5 – 7", "≥ 7"]


def test_un_vod_sin_ningun_bueno_se_cuenta_solo_si_esta_revisado_entero():
    filas = [{"vod": "a", "puesto": 1, "veredicto": "malo"}, {"vod": "a", "puesto": 2, "veredicto": "dudoso"},
             {"vod": "b", "puesto": 1, "veredicto": "malo"}, {"vod": "b", "puesto": 2, "veredicto": ""},
             {"vod": "c", "puesto": 1, "veredicto": "bueno"}, {"vod": "c", "puesto": 2, "veredicto": "malo"}]
    assert revision.vods_sin_nada(filas, k=2) == 1


# ---------------------------------------------------------------- perfiles

def test_la_facecam_se_parsea_y_rechaza_lo_que_no_es_un_rectangulo():
    assert perfiles.parsear_facecam(" 4, 268,320,188 ") == [4, 268, 320, 188]
    assert perfiles.parsear_facecam("") is None
    for mala in ("1,2,3", "a,b,c,d", "0,0,0,100", "-1,0,10,10"):
        with pytest.raises(ValueError):
            perfiles.parsear_facecam(mala)


def test_dividido_sin_facecam_cae_a_desenfoque():
    assert perfiles.Perfil("X", formato="dividido").formato_efectivo == "desenfoque"
    assert perfiles.Perfil("X", formato="dividido", facecam=[0, 0, 10, 10]).formato_efectivo == "dividido"


def test_guardar_un_perfil_con_el_mismo_nombre_lo_reemplaza(tmp_path):
    ruta = tmp_path / "perfiles.json"
    lista = perfiles.poner([], perfiles.Perfil("Ibai", juego="LoL"))
    lista = perfiles.poner(lista, perfiles.Perfil("ibai", juego="Fútbol", permiso="concedido"))
    perfiles.guardar(lista, ruta)
    leidos = perfiles.cargar(ruta)
    assert [(p.nombre, p.juego) for p in leidos] == [("ibai", "Fútbol")]
    assert perfiles.obtener("IBAI", leidos).puede_publicar
    assert oct(ruta.stat().st_mode)[-3:] == "600"


def test_un_perfil_invalido_no_entra():
    for malo in (perfiles.Perfil(" "), perfiles.Perfil("X", formato="cuadrado"),
                 perfiles.Perfil("X", permiso="quizás"), perfiles.Perfil("X", canales=["twitch.tv/x"])):
        with pytest.raises(ValueError):
            perfiles.poner([], malo)


def test_una_clave_desconocida_no_rompe_la_carga():
    assert perfiles.desde_dict({"nombre": "X", "campo_del_futuro": 1}).nombre == "X"


# ---------------------------------------------------------------- panel

@pytest.mark.parametrize("cabecera, esperado", [
    ("bytes=0-99", (0, 99)), ("bytes=100-", (100, 999)), ("bytes=-200", (800, 999)),
    ("bytes=900-5000", (900, 999)), ("bytes=0-0", (0, 0)),
    (None, None), ("bytes=1000-", None), ("bytes=50-10", None), ("bytes=abc", None),
    ("items=0-5", None), ("bytes=-0", None),
])
def test_rango_de_bytes(cabecera, esperado):
    assert panel.rango(cabecera, 1000) == esperado


def test_no_se_sirve_nada_de_afuera_de_la_carpeta(tmp_path):
    base = tmp_path / "salida"
    (base / "vod").mkdir(parents=True)
    (base / "vod" / "clip.mp4").write_bytes(b"x")
    (tmp_path / "secreto.txt").write_text("no")
    assert panel.ruta_segura(base, "vod/clip.mp4") == (base / "vod" / "clip.mp4").resolve()
    assert panel.ruta_segura(base, "../secreto.txt") is None
    assert panel.ruta_segura(base, "vod/../../secreto.txt") is None
    assert panel.ruta_segura(base, "/etc/passwd") is None
    assert panel.ruta_segura(base, "vod") is None            # una carpeta no es un clip
    assert panel.ruta_segura(base, "vod/no_existe.mp4") is None


@pytest.mark.parametrize("origen, local", [
    (None, True), ("http://localhost:8765", True), ("http://127.0.0.1:8765", True),
    ("https://sitio-cualquiera.com", False), ("http://localhost.sitio.com", False),
])
def test_solo_el_propio_panel_puede_mandar_acciones(origen, local):
    assert panel.origen_local(origen) is local


def test_la_vista_del_panel_trae_la_precision(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "SALIDA", tmp_path / "salida")
    estado, _ = _estado_con("bueno", "malo")
    v = panel.vista(estado, [perfiles.Perfil("Alguien", formato="dividido")])
    assert v["precision"]["top_k"]["precision"] == 0.5
    assert v["perfiles"][0]["formato_efectivo"] == "desenfoque"
    assert set(v["plataformas"]) == {"tiktok", "youtube_shorts", "instagram_reels", "x"}


def test_el_medidor_de_facecam_no_borra_el_resto_del_perfil():
    actual = perfiles.Perfil("Ibai", canales=["https://www.twitch.tv/ibai/videos"],
                             permiso="concedido", juego="LoL", modelo="medium")
    p = panel.perfil_desde_formulario(
        {"nombre": "ibai", "facecam": "10,20,300,200", "formato": "dividido"}, actual)
    assert (p.facecam, p.formato_efectivo) == ([10, 20, 300, 200], "dividido")
    assert (p.canales, p.permiso, p.juego, p.modelo) == (
        ["https://www.twitch.tv/ibai/videos"], "concedido", "LoL", "medium")


def test_el_formulario_completo_arma_un_perfil_nuevo_y_puede_vaciar_la_facecam():
    p = panel.perfil_desde_formulario(
        {"nombre": " Nueva ", "canales": "https://a.tv/x\nhttps://b.tv/y", "facecam": "",
         "formato": "recorte", "permiso": "pedido"}, None)
    assert (p.nombre, p.canales, p.facecam, p.permiso) == (
        "Nueva", ["https://a.tv/x", "https://b.tv/y"], None, "pedido")
    con_cam = perfiles.Perfil("X", facecam=[1, 2, 3, 4])
    assert panel.perfil_desde_formulario({"nombre": "X", "facecam": ""}, con_cam).facecam is None


def test_el_cuadro_para_medir_se_pide_igual_que_el_clip():
    # la facecam se mide sobre el cuadro y se aplica sobre el clip: si pidieran
    # resoluciones distintas, el recorte caería en cualquier lado
    import inspect
    from clips import cortar
    assert cortar._formato(720) == ["-f", "bv*[height<=720]+ba/b[height<=720]",
                                    "-S", "vcodec:h264,acodec:aac,res"]
    for fn in (cortar.cortar, cortar.cuadro):
        assert "_formato(altura_max)" in inspect.getsource(fn)
        assert inspect.signature(fn).parameters["altura_max"].default == 720


# ---------------------------------------------------------------- respaldos

def _nombres(carpeta):
    return sorted(q.name for q in carpeta.iterdir()) if carpeta.exists() else []


def test_la_primera_escritura_del_dia_guarda_el_estado_anterior(tmp_path):
    ruta = tmp_path / "lote.json"
    ayer, _ = _estado_con("bueno")
    lote.guardar(ayer, ruta, ahora=datetime(2026, 11, 10, 22, 0))
    assert _nombres(tmp_path / "respaldos") == []        # no había nada que cuidar

    hoy, _ = _estado_con("bueno", "malo")
    lote.guardar(hoy, ruta, ahora=datetime(2026, 11, 11, 9, 0))
    lote.guardar(hoy, ruta, ahora=datetime(2026, 11, 11, 9, 5))
    assert _nombres(tmp_path / "respaldos") == ["lote_2026-11-11.json"]
    # la copia es lo que había ANTES de tocar nada hoy, no lo recién escrito
    copia = lote.cargar(tmp_path / "respaldos" / "lote_2026-11-11.json")
    assert lote.contar_veredictos(copia) == 1


def test_perder_veredictos_deja_una_copia_aunque_ya_haya_una_de_hoy(tmp_path):
    # el caso que la copia diaria no cubre: se revisa a la mañana y un bug pisa todo
    # a la tarde. La diaria es de antes de la revisión.
    ruta, dia = tmp_path / "lote.json", datetime(2026, 11, 11, 9, 0)
    lote.guardar(lote.nuevo_estado(), ruta, ahora=dia)
    revisado, _ = _estado_con("bueno", "malo", "dudoso")
    lote.guardar(revisado, ruta, ahora=dia.replace(hour=10))
    pisado, _ = _estado_con("", "", "")
    lote.guardar(pisado, ruta, ahora=dia.replace(hour=17))

    perdidas = [n for n in _nombres(tmp_path / "respaldos") if n.startswith("perdida_")]
    assert perdidas == ["perdida_2026-11-11_170000_3a0.json"]
    assert lote.contar_veredictos(lote.cargar(tmp_path / "respaldos" / perdidas[0])) == 3


def test_sumar_veredictos_no_genera_copias_de_perdida(tmp_path):
    ruta, dia = tmp_path / "lote.json", datetime(2026, 11, 11, 9, 0)
    for veredictos in [("",), ("bueno",), ("bueno",)]:
        estado, _ = _estado_con(*veredictos)
        lote.guardar(estado, ruta, ahora=dia)
    assert not [n for n in _nombres(tmp_path / "respaldos") if n.startswith("perdida_")]


def test_se_conservan_los_ultimos_dias_y_nunca_se_borra_una_perdida():
    nombres = [f"lote_2026-11-{d:02d}.json" for d in range(1, 21)]
    nombres += ["perdida_2026-10-01_120000_45a0.json", "otra_cosa.txt"]
    sobran = lote.diarios_a_borrar(nombres, conservar=14)
    assert sobran == [f"lote_2026-11-{d:02d}.json" for d in range(1, 7)]


def test_un_estado_roto_en_disco_no_impide_guardar(tmp_path):
    ruta = tmp_path / "lote.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    estado, _ = _estado_con("bueno")
    lote.guardar(estado, ruta, ahora=datetime(2026, 11, 11))
    assert lote.contar_veredictos(lote.cargar(ruta)) == 1
    assert _nombres(tmp_path / "respaldos") == ["lote_2026-11-11.json"]   # el roto, por las dudas


def test_el_estado_real_y_uno_de_prueba_no_comparten_respaldos(tmp_path):
    assert lote.carpeta_de_respaldos(lote.ESTADO) == lote.RESPALDOS
    assert lote.carpeta_de_respaldos(tmp_path / "lote.json") == tmp_path / "respaldos"


# ---------------------------------------------------------------- permiso

@pytest.mark.parametrize("permiso", ["sin pedir", "pedido", "negado"])
def test_sin_permiso_concedido_no_se_registra_una_publicacion(permiso):
    _, vod = _estado_con("bueno")
    with pytest.raises(ValueError, match=permiso):
        lote.exigir_permiso(vod, perfiles.Perfil("Alguien", permiso=permiso))


def test_un_vod_sin_perfil_no_tiene_permiso():
    _, vod = _estado_con("bueno")
    with pytest.raises(ValueError, match="no tiene perfil"):
        lote.exigir_permiso(vod, None)


def test_con_permiso_concedido_pasa():
    _, vod = _estado_con("bueno")
    lote.exigir_permiso(vod, perfiles.Perfil("Alguien", permiso="concedido"))


def test_el_panel_rechaza_marcar_publicado_sin_permiso(tmp_path, monkeypatch):
    # el chequeo tiene que estar en la acción y no solo en el botón deshabilitado
    ruta = tmp_path / "lote.json"
    estado, vod = _estado_con("bueno")
    vod["picos"][0]["clip"] = "v/pico.mp4"
    lote.guardar(estado, ruta)
    monkeypatch.setattr(lote, "ESTADO", ruta)
    pedido = {"id": vod["id"], "t": 100.0, "plataforma": "tiktok", "url": "https://tiktok.com/x"}

    monkeypatch.setattr(panel.mod_perfiles, "cargar",
                        lambda ruta=None: [perfiles.Perfil("Alguien", permiso="pedido")])
    with pytest.raises(ValueError, match="pedido"):
        panel.accion("/api/publicado", pedido)
    assert lote.cargar(ruta)["vods"][0]["picos"][0]["publicado"] == {}

    monkeypatch.setattr(panel.mod_perfiles, "cargar",
                        lambda ruta=None: [perfiles.Perfil("Alguien", permiso="concedido")])
    panel.accion("/api/publicado", pedido)
    assert "tiktok" in lote.cargar(ruta)["vods"][0]["picos"][0]["publicado"]
