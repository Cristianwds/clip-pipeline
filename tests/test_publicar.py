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
