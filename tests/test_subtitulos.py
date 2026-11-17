"""Tests del agrupado y del formato ASS. No necesitan el modelo ni audio."""
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips.subtitulos import Palabra, _escapar, _tiempo_ass, a_ass, agrupar
from clips.texto import es_funcional


def palabras(*spec):
    return [Palabra(t0, t1, t) for t0, t1, t in spec]


def test_corta_cuando_hay_silencio():
    p = palabras((0.0, 0.4, "mira"), (0.5, 0.9, "esto"), (3.0, 3.4, "tremendo"))
    out = agrupar(p, corte_silencio=0.6)
    assert [l.texto for l in out] == ["mira esto", "tremendo"]


def test_corta_cuando_la_linea_se_pasa_de_ancho():
    p = palabras(*[(i * 0.3, i * 0.3 + 0.2, "palabra") for i in range(6)])
    out = agrupar(p, max_chars=22, corte_silencio=99, max_dur=99)
    assert all(len(l.texto) <= 22 for l in out), [l.texto for l in out]
    assert len(out) > 1


def test_corta_cuando_dura_demasiado():
    p = palabras(*[(i * 1.0, i * 1.0 + 0.9, "a") for i in range(8)])
    out = agrupar(p, max_dur=2.5, corte_silencio=99, max_chars=999)
    assert all(l.t1 - l.t0 <= 2.5 + 1e-9 for l in out), [(l.t0, l.t1) for l in out]


def test_no_pierde_ni_reordena_palabras():
    p = palabras(*[(i * 0.5, i * 0.5 + 0.3, f"w{i}") for i in range(20)])
    out = agrupar(p)
    juntas = " ".join(l.texto for l in out).split()
    assert juntas == [f"w{i}" for i in range(20)]


def test_los_tiempos_de_cada_linea_son_los_de_sus_palabras():
    p = palabras((1.0, 1.4, "a"), (1.5, 2.0, "b"), (9.0, 9.5, "c"))
    out = agrupar(p, corte_silencio=0.6)
    assert (out[0].t0, out[0].t1) == (1.0, 2.0)
    assert (out[1].t0, out[1].t1) == (9.0, 9.5)


def test_sin_palabras_no_hay_lineas():
    assert agrupar([]) == []


def test_formato_de_tiempo_ass():
    assert _tiempo_ass(0) == "0:00:00.00"
    assert _tiempo_ass(3725.5) == "1:02:05.50"
    assert _tiempo_ass(-3) == "0:00:00.00"


def test_escapa_las_llaves_que_en_ass_son_codigos_de_estilo():
    assert "{" not in _escapar("un {\\b1}truco") and "}" not in _escapar("un {\\b1}truco")
    assert "\n" not in _escapar("dos\nlineas")


def test_el_ass_sale_con_el_tamano_del_video_vertical():
    out = a_ass(agrupar(palabras((0.0, 1.0, "hola"))), ancho=1080, alto=1920)
    assert "PlayResX: 1080" in out and "PlayResY: 1920" in out
    assert out.count("Dialogue:") == 1



def test_no_deja_palabras_funcionales_colgando_al_final():
    """El caso real: "le puedo decir que se | vaya" y no "...que se | vaya"."""
    p = palabras(*[(i * 0.3, i * 0.3 + 0.25, w) for i, w in enumerate(
        ["le", "puedo", "decir", "que", "se", "vaya", "ahora"])])
    out = agrupar(p, max_chars=22, corte_silencio=99, max_dur=99)
    for l in out[:-1]:
        assert not es_funcional(l.palabras[-1].texto), \
            f"la linea {l.texto!r} termina en palabra funcional"


def test_corta_en_el_punto_aunque_entre_mas():
    p = palabras((0.0, 0.3, "listo."), (0.4, 0.7, "va"), (0.8, 1.1, "otra"))
    out = agrupar(p, max_chars=99, corte_silencio=99, max_dur=99)
    assert out[0].texto == "listo.", [l.texto for l in out]


def test_retroceder_el_corte_no_rompe_la_duracion_maxima():
    p = palabras(*[(i * 1.0, i * 1.0 + 0.9, "a") for i in range(8)])
    out = agrupar(p, max_dur=2.5, corte_silencio=99, max_chars=999)
    assert all(l.t1 - l.t0 <= 2.5 + 1e-9 for l in out), [(l.t0, l.t1) for l in out]


def test_no_une_la_huerfana_si_hay_un_silencio_en_el_medio():
    """Pegarlas dejaria el subtitulo colgado en pantalla durante el silencio."""
    p = palabras((1.0, 1.4, "mira"), (1.5, 2.0, "esto"), (9.0, 9.5, "tremendo"))
    out = agrupar(p, corte_silencio=0.6)
    assert len(out) == 2, [l.texto for l in out]
    assert (out[0].t0, out[0].t1) == (1.0, 2.0)



def test_no_arrastra_palabras_al_otro_lado_de_un_silencio():
    """Si se corta por silencio, el corte ya esta donde tiene que estar."""
    p = palabras((0.0, 0.3, "mira"), (0.4, 0.7, "esto"), (0.8, 1.1, "que"),
                 (5.0, 5.3, "tremendo"), (5.4, 5.7, "quedo"))
    out = agrupar(p, corte_silencio=0.6, max_chars=99, max_dur=99)
    assert out[0].texto == "mira esto que", [l.texto for l in out]
    assert out[1].t0 == 5.0


def test_la_linea_no_queda_pelada_por_retroceder():
    """Si casi toda la linea es funcional, mejor el corte feo que una linea de nada."""
    p = palabras(*[(i * 0.2, i * 0.2 + 0.15, w) for i, w in enumerate(
        ["ah", "y", "que", "se", "vaya"])])
    out = agrupar(p, max_chars=12, corte_silencio=99, max_dur=99)
    assert all(l.palabras for l in out)
    assert " ".join(l.texto for l in out).split() == ["ah", "y", "que", "se", "vaya"]


# ---------------------------------------------------------------- dónde arranca el clip

from clips.subtitulos import RESPIRO, desplazar, inicio_de_frase  # noqa: E402


def _frase(*spec):
    return [Palabra(t0, t1, w) for t0, t1, w in spec]


def test_arranca_en_el_comienzo_de_frase_mas_cercano():
    ps = _frase((1.0, 1.4, "bueno."), (4.0, 4.4, "Mirá"), (4.5, 4.9, "esto"), (8.0, 8.4, "Ahí"))
    assert inicio_de_frase(ps, 5.0, atras=6) == pytest.approx(4.0 - RESPIRO)


def test_un_silencio_largo_tambien_es_comienzo_de_frase():
    # whisper no siempre pone el punto; un silencio de más de 0,6 s es un corte igual
    ps = _frase((1.0, 1.4, "y"), (1.5, 1.9, "entonces"), (3.0, 3.4, "agarra"))
    assert inicio_de_frase(ps, 3.5, atras=6) == pytest.approx(3.0 - RESPIRO)


def test_a_igual_distancia_gana_el_de_antes():
    # sobra un segundo de contexto, no falta
    ps = _frase((1.15, 1.5, "Uno."), (5.15, 5.5, "Dos."))
    assert inicio_de_frase(ps, 3.0, atras=6) == pytest.approx(1.0)


def test_no_retrocede_mas_de_lo_pedido():
    # el corte se bajó con `atras` segundos de sobra: antes de eso no hay video
    ps = _frase((0.5, 0.9, "Hola."), (6.5, 6.9, "Dale"))
    assert inicio_de_frase(ps, 6.0, atras=2, adelante=3) == pytest.approx(6.5 - RESPIRO)


def test_sin_frase_cerca_por_lo_menos_no_parte_una_palabra():
    ps = _frase((4.8, 5.6, "largaaa"), (5.7, 6.0, "palabra"))
    assert inicio_de_frase(ps, 5.2, atras=0.3, adelante=0.2) == pytest.approx(5.2)
    assert inicio_de_frase(ps, 5.2, atras=2, adelante=0.2) == pytest.approx(4.8 - RESPIRO)


def test_si_en_el_objetivo_hay_silencio_se_queda_ahi():
    assert inicio_de_frase([], 6.0, atras=6) == 6.0
    ps = _frase((0.0, 0.3, "eh"), (20.0, 20.3, "bueno"))
    assert inicio_de_frase(ps, 6.0, atras=2, adelante=3) == 6.0


def test_desplazar_deja_las_palabras_en_el_tiempo_del_clip():
    ps = _frase((1.0, 1.4, "antes"), (4.0, 4.4, "Mirá"), (4.5, 4.9, "esto"))
    assert [(p.t0, p.texto) for p in desplazar(ps, 3.85)] == [
        (pytest.approx(0.15), "Mirá"), (pytest.approx(0.65), "esto")]
