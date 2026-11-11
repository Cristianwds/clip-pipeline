"""Tests de los metadatos y del plan. No tocan la red ni publican nada."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips.metadatos import consolidar, elegir_cita, hashtags, parsear_tema, titular
from clips.publicar import (PLATAFORMAS, Medidas, Publicacion, paquete_postiz,
                            planificar, recortar_texto, validar)


# --- metadatos -------------------------------------------------------------

def test_agrupa_las_variantes_del_chat_y_suma_los_conteos():
    """El chat escribio 'romance' de dos formas: tiene que pesar 13, no 8."""
    out = consolidar(parsear_tema(["romanse×8", "romanze×5", "malas×9"]))
    por_palabra = {t.palabra: t.veces for t in out}
    assert 13 in por_palabra.values(), por_palabra
    assert len(out) == 2, por_palabra


def test_la_ortografia_del_audio_corrige_la_del_chat():
    out = consolidar(parsear_tema(["romanse×8", "romanze×5"]), {"romance"})
    assert out[0].palabra == "romance"


def test_pero_no_corrige_una_palabra_que_el_chat_escribio_bien():
    """El caso que rompio: 'malas' no puede volverse 'mata' por parecerse."""
    out = consolidar(parsear_tema(["malas×9"]), {"mata", "vaya"})
    assert out[0].palabra == "malas"


def test_la_tilde_si_se_corrige_aunque_haya_una_sola_forma():
    out = consolidar(parsear_tema(["medico×5"]), {"médico"})
    assert out[0].palabra == "médico"


def test_el_titulo_usa_las_palabras_del_chat():
    t = titular(["romanse×8", "malas×9"], "gracioso", {"romance"}, streamer="AuronPlay")
    assert "romance" in t.lower() and "AuronPlay" in t


def test_hay_titulo_aunque_el_pico_no_tenga_tema():
    assert titular([], "épico", streamer="ibai")


def test_el_ruido_no_entra_en_el_titulo():
    assert "xdd" not in titular(["xdd×30", "faker×9"], "gracioso").lower()


def test_la_cita_no_es_solo_una_palabra_repetida():
    tokens = consolidar(parsear_tema(["romance×13", "malas×9"]))
    lineas = ["romance romance romance", "a malas le puedo decir"]
    cita = elegir_cita(lineas, tokens)
    assert cita and "malas le puedo decir" in cita, cita


def test_sin_coincidencias_no_inventa_cita():
    tokens = consolidar(parsear_tema(["faker×9"]))
    assert elegir_cita(["hola que tal como va"], tokens) is None


def test_los_hashtags_no_se_repiten_y_respetan_el_tope():
    h = hashtags("gracioso", streamer="AuronPlay", juego="AuronPlay", tope=5)
    assert len(h) == len(set(h)) <= 5


# --- validacion y plan -----------------------------------------------------

def test_detecta_el_clip_demasiado_largo():
    m = Medidas(segundos=400, mb=50, ancho=1080, alto=1920)
    assert validar(m, PLATAFORMAS["youtube_shorts"]), "180s es el maximo de Shorts"
    assert not validar(m, PLATAFORMAS["tiktok"]), "en TikTok 400s entra"


def test_detecta_el_clip_que_no_es_vertical():
    m = Medidas(segundos=30, mb=10, ancho=1920, alto=1080)
    assert any("vertical" in p for p in validar(m, PLATAFORMAS["tiktok"]))


def test_un_clip_bien_no_tiene_problemas_en_ninguna_plataforma():
    m = Medidas(segundos=35, mb=12, ancho=1080, alto=1920)
    assert all(not validar(m, p) for p in PLATAFORMAS.values())


def test_recorta_sin_partir_palabras():
    t = recortar_texto("una frase bastante larga de prueba", 20)
    assert len(t) <= 20 and not t.rstrip("…").endswith(" ") and " larg" not in t


def test_el_plan_separa_los_clips_en_dias_distintos():
    """Publicar todo junto es el patron que las plataformas marcan como masivo."""
    pubs = [Publicacion(clip=f"c{i}.mp4", momento="0h00m00s", prioridad=1.0,
                        titulo="t", descripcion="d",
                        plataformas={k: {"nombre": v.nombre} for k, v in PLATAFORMAS.items()})
            for i in range(3)]
    planificar(pubs, desde=datetime(2026, 11, 8, 9, 0))
    dias = [p.plataformas["tiktok"]["cuando"][:10] for p in pubs]
    assert len(set(dias)) == 3, dias


def test_el_plan_nunca_agenda_en_el_pasado():
    pubs = [Publicacion(clip="c.mp4", momento="0h00m00s", prioridad=1.0, titulo="t",
                        descripcion="d",
                        plataformas={k: {"nombre": v.nombre} for k, v in PLATAFORMAS.items()})]
    planificar(pubs, desde=datetime(2026, 11, 8, 23, 30))
    for datos in pubs[0].plataformas.values():
        assert datos["cuando"] > "2026-11-08T23:30"


def test_el_paquete_de_postiz_no_sale_a_la_red_y_marca_lo_que_falta():
    pub = Publicacion(clip="c.mp4", momento="0h00m00s", prioridad=1.0, titulo="T",
                      descripcion="D",
                      plataformas={"tiktok": {"nombre": "TikTok", "titulo": "T",
                                              "descripcion": "D", "cuando": "2026-11-09T19:00"}})
    p = paquete_postiz(pub, "tiktok")
    assert p["date"] == "2026-11-09T19:00"
    assert "id del canal" in p["posts"][0]["integration"]["id"]


def test_corrige_un_typo_de_una_letra_aunque_el_chat_sea_consistente():
    """'romanse' -> 'romance' es una edicion: typo plausible."""
    assert consolidar(parsear_tema(["romanse×8"]), {"romance"})[0].palabra == "romance"


def test_la_cita_junta_lineas_para_formar_una_frase():
    """Las lineas de subtitulo son fragmentos de 22 chars: una sola no es una frase."""
    tokens = consolidar(parsear_tema(["mierda×5"]))
    lineas = ["le puedo decir", "que se vaya", "a la mierda", "y encima mato"]
    cita = elegir_cita(lineas, tokens)
    assert cita and cita.count(" ") >= 5, cita
    assert "mierda" in cita


def test_la_cita_respeta_el_largo_maximo():
    tokens = consolidar(parsear_tema(["uno×5"]))
    lineas = ["palabra uno larga"] * 10
    cita = elegir_cita(lineas, tokens, maximo=40)
    assert cita is None or len(cita) <= 40



def test_la_cita_no_queda_colgada_en_los_extremos():
    """Una cita que termina en "y encima" queda a medias."""
    tokens = consolidar(parsear_tema(["mierda×5"]))
    lineas = ["y encima", "que se vaya", "a la mierda", "y encima"]
    cita = elegir_cita(lineas, tokens)
    assert cita and not cita.endswith("encima") and not cita.startswith("y "), cita


def test_x_tiene_el_limite_corto_de_texto():
    """En X el texto es uno solo de 280: el recorte tiene que notarse."""
    assert PLATAFORMAS["x"].max_descripcion == 280
    assert PLATAFORMAS["x"].max_hashtags <= 3, "llenar X de hashtags rinde peor"


def test_un_clip_de_treinta_segundos_entra_en_las_cuatro():
    m = Medidas(segundos=28, mb=12, ancho=1080, alto=1920)
    for clave, p in PLATAFORMAS.items():
        assert not validar(m, p), (clave, validar(m, p))


def test_el_plan_cubre_las_cuatro_plataformas():
    assert set(PLATAFORMAS) == {"tiktok", "youtube_shorts", "instagram_reels", "x"}


def test_los_textos_salen_uno_por_clip_y_plataforma(tmp_path):
    from clips.publicar import escribir_textos
    pub = Publicacion(clip="c.mp4", momento="0h00m00s", prioridad=1.0, titulo="T",
                      descripcion="D", plataformas={
                          k: {"nombre": v.nombre, "titulo": "T", "descripcion": "D"}
                          for k, v in PLATAFORMAS.items()})
    escritos = escribir_textos([pub], tmp_path)
    assert len(escritos) == len(PLATAFORMAS)
    assert (tmp_path / "textos" / "c.x.txt").read_text().strip() == "D", \
        "en X el titulo no va aparte"
    assert (tmp_path / "textos" / "c.tiktok.txt").read_text().startswith("T")


def test_descarta_el_coreo_del_chat_como_cita():
    """Caso real: el streamer lee el chat coreando y la transcripcion queda ilegible."""
    tokens = consolidar(parsear_tema(["romance×13", "matala×20", "abuela×9"]))
    lineas = ["romance mátala creo", "que hay más mátala", "si mátala abuela",
              "mátala abuela", "le puedo decir", "que se vaya a la mierda"]
    cita = elegir_cita(lineas, tokens)
    assert cita is None or cita.lower().count("mátala") <= 1, cita


def test_una_frase_normal_pasa_el_filtro_de_diversidad():
    tokens = consolidar(parsear_tema(["mierda×5"]))
    lineas = ["puedo poner a malas", "le puedo decir", "que se vaya a la mierda"]
    assert elegir_cita(lineas, tokens), "una frase variada no se tiene que filtrar"


def test_la_densidad_separa_el_coreo_de_la_frase():
    """Repetir una palabra en siete es coreo; en trece, es como habla la gente."""
    from clips.metadatos import _es_coreo
    assert _es_coreo("romance matala creo que hay mas matala".split())
    assert not _es_coreo(
        "puedo poner a malas le puedo decir que se vaya a la mierda".split())


def test_que_se_repitan_conectores_no_es_coreo():
    from clips.metadatos import _es_coreo
    assert not _es_coreo("que se viene la que se arma".split())


def test_dos_pares_repetidos_tambien_son_coreo():
    """Mirar solo la palabra mas repetida dejaba pasar esto: ninguna llega a tres."""
    from clips.metadatos import _es_coreo
    assert _es_coreo(
        "matala abuela matala abuela le puedo decir que se vaya a la mierda".split())


# --- voz: quién habla en el post -------------------------------------------

import pytest  # noqa: E402

from clips import metadatos, perfiles  # noqa: E402
from clips.publicar import armar_publicacion as _armar_publicacion  # noqa: E402

_TEMA = ["romanse×8", "malas×9"]
_URL = "https://www.twitch.tv/videos/1"


def _meta(voz, tema=_TEMA, etiqueta="gracioso"):
    return metadatos.armar(tema, etiqueta, _URL, 6507, lineas=["romance o a malas le puedo decir"],
                           streamer="AuronPlay", juego="AC Odyssey", voz=voz)


def test_en_voz_propia_el_streamer_no_se_nombra_ni_se_da_credito():
    m = _meta("propia")
    assert "AuronPlay" not in m.titulo and "AuronPlay" not in m.descripcion
    assert "Clip del stream" not in m.descripcion and "Fuente:" not in m.descripcion
    assert "romance" in m.titulo.lower()               # el contenido es el mismo


def test_en_voz_propia_el_link_al_directo_se_conserva_con_el_minuto():
    assert "El directo completo: https://www.twitch.tv/videos/1?t=1h48m27s" in _meta("propia").descripcion


def test_en_voz_propia_no_va_el_hashtag_de_canal_de_clips():
    h = _meta("propia").hashtags
    assert "clips" not in h and "twitch" not in h
    assert h[0] == "auronplay"                          # su nombre sí: es su marca


def test_la_voz_de_tercero_no_cambio():
    # es el comportamiento de antes y el del CLI: no puede moverse sin querer
    m = _meta("tercero")
    assert m.titulo == "Malas vs romance — el chat de AuronPlay no se ponía de acuerdo"
    assert "Clip del stream de AuronPlay." in m.descripcion and "Fuente:" in m.descripcion
    assert "clips" in m.hashtags
    assert metadatos.armar(_TEMA, "gracioso", _URL, 6507, streamer="AuronPlay").titulo == \
        metadatos.armar(_TEMA, "gracioso", _URL, 6507, streamer="AuronPlay", voz="tercero").titulo


def test_con_un_solo_tema_no_se_dice_que_el_chat_no_se_ponia_de_acuerdo():
    assert _meta("propia", tema=["faker×9"]).titulo == "Faker: lo que pedía el chat"


def test_sin_tema_el_titulo_propio_habla_del_directo():
    assert _meta("propia", tema=[], etiqueta="épico").titulo == "El momentazo del directo"


def test_una_voz_desconocida_se_rechaza():
    with pytest.raises(ValueError):
        _meta("mia")
    with pytest.raises(ValueError):
        perfiles.validar(perfiles.Perfil("Alguien", voz="mia"))


def test_perfiles_y_metadatos_conocen_las_mismas_voces():
    # están repetidas para que perfiles no importe nada del proyecto
    assert perfiles.VOCES == metadatos.VOCES


def test_un_perfil_guardado_antes_de_que_existiera_la_voz_es_de_tercero():
    assert perfiles.desde_dict({"nombre": "Viejo", "permiso": "pedido"}).voz == "tercero"


# --- mención en X ----------------------------------------------------------

def _meta_x(**kw):
    return metadatos.armar(_TEMA, "gracioso", _URL, 6507, streamer="AuronPlay",
                           lineas=["romance o a malas le puedo decir"], **kw)


def test_la_mencion_reemplaza_el_nombre_en_el_credito_solo_para_x():
    m = _meta_x(mencion_x="auronplay")
    assert "Clip del stream de @auronplay." in m.descripcion_x
    assert "Clip del stream de AuronPlay." not in m.descripcion_x
    assert "@" not in m.descripcion                    # las otras plataformas no cambian


def test_la_mencion_nunca_abre_el_texto():
    # un post que arranca con @usuario se trata como respuesta y lo ve menos gente
    sin_cita = metadatos.armar(_TEMA, "gracioso", _URL, 6507, streamer="AuronPlay",
                               mencion_x="auronplay")
    for m in (_meta_x(mencion_x="auronplay"), sin_cita):
        assert not m.descripcion_x.lstrip().startswith("@")


def test_sin_usuario_no_hay_texto_aparte_para_x():
    assert _meta_x().descripcion_x is None


def test_en_voz_propia_el_streamer_no_se_menciona_a_si_mismo():
    assert _meta_x(voz="propia", mencion_x="auronplay").descripcion_x is None


def test_la_mencion_llega_al_texto_de_x_y_sobrevive_al_recorte(tmp_path, monkeypatch):
    from clips import publicar
    from clips.picos import Pico
    monkeypatch.setattr(publicar, "medir", lambda clip: publicar.Medidas(20.0, 10.0, 1080, 1920))
    pico = Pico(t=6507.0, score=9.0, tasa=20.0, tema=_TEMA, foco=0.5, etiqueta="gracioso")
    pub = _armar_publicacion(tmp_path / "c.mp4", pico, _URL, streamer="AuronPlay",
                             lineas=["romance o a malas le puedo decir que se vaya a la mierda"],
                             mencion_x="auronplay")
    x = pub.plataformas["x"]["descripcion"]
    assert "@auronplay" in x and len(x) <= 280
    assert "@auronplay" not in pub.plataformas["tiktok"]["descripcion"]


@pytest.mark.parametrize("crudo, limpio", [("@auronplay", "auronplay"), (" Rubius_ ", "Rubius_"), ("", "")])
def test_el_usuario_de_x_se_guarda_sin_arroba(crudo, limpio):
    assert perfiles.validar(perfiles.Perfil("Alguien", usuario_x=crudo)).usuario_x == limpio


@pytest.mark.parametrize("malo", ["https://x.com/auronplay", "con espacio", "x" * 16])
def test_un_usuario_de_x_que_no_es_un_usuario_se_rechaza(malo):
    with pytest.raises(ValueError):
        perfiles.validar(perfiles.Perfil("Alguien", usuario_x=malo))
