"""Tests de lo que distingue un momento real de un artefacto."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from clips.chat import Mensaje
from clips.picos import detectar
from clips.vocabulario import analizar, perfil_global


def chat(duracion=7200, base=2.0, picos=(), seed=3):
    """Chat de fondo que habla siempre igual ('xdd que de'), mas picos con texto propio.

    Cada pico es (segundo, multiplicador, texto): asi se puede verificar que el analisis
    recupera el texto del pico y NO el vocabulario de fondo.
    """
    rng = np.random.default_rng(seed)
    msgs = []
    for s in range(duracion):
        for _ in range(rng.poisson(base)):
            msgs.append(Mensaje(s + rng.random(), "u", "xdd que de"))
    for centro, mult, texto in picos:
        for s in range(centro - 4, centro + 4):
            for _ in range(rng.poisson(base * mult)):
                msgs.append(Mensaje(s + rng.random(), "u", texto))
    msgs.sort(key=lambda m: m.t)
    return msgs


def test_el_tema_es_lo_especifico_no_lo_frecuente():
    """'faker' aparece solo en el pico; 'xdd' aparece en todo el stream."""
    msgs = chat(picos=((900, 15, "faker se la come xdd"),))
    g, n = perfil_global(msgs)
    a = analizar(msgs, 900, 5.0, g, n)
    tema = " ".join(a.tema)
    assert "faker" in tema, f"no detecto el tema: {a.tema}"
    assert "xdd" not in tema, f"'xdd' no es tema, es como habla el chat: {a.tema}"
    assert any("xdd" in r for r in a.reaccion), f"'xdd' deberia ser reaccion: {a.reaccion}"


def test_un_pico_sin_tema_propio_tiene_foco_bajo():
    """Si el chat solo acelera diciendo lo mismo de siempre, no hay de que hablar."""
    con_tema = chat(picos=((900, 15, "faker se la come xdd"),))
    sin_tema = chat(picos=((900, 15, "xdd que de"),))
    g1, n1 = perfil_global(con_tema)
    g2, n2 = perfil_global(sin_tema)
    foco_con = analizar(con_tema, 900, 5.0, g1, n1).foco
    foco_sin = analizar(sin_tema, 900, 5.0, g2, n2).foco
    assert foco_con > 0.3, f"foco demasiado bajo con tema claro: {foco_con}"
    assert foco_sin < 0.1, f"foco alto sin tema propio: {foco_sin}"
    assert foco_con > foco_sin * 3


def test_etiqueta_desde_emote_conocido():
    msgs = chat(picos=((900, 15, "KEKW KEKW no puedo mas"),))
    g, n = perfil_global(msgs)
    assert analizar(msgs, 900, 5.0, g, n).etiqueta == "gracioso"


def test_descarta_el_arranque_pero_no_el_final():
    """El "se escucha" del arranque no es clip. El final SI puede serlo: en el VOD de
    ibai el climax estaba 2 min antes de la despedida."""
    msgs = chat(duracion=3600, picos=((120, 20, "se escucha"), (3400, 20, "faker")))
    con = detectar(msgs, borde_inicio=600.0, top=10)
    sin = detectar(msgs, borde_inicio=0.0, top=10)
    assert any(abs(p.t - 120) < 15 for p in sin), "sin filtro deberia ver el arranque"
    assert not any(abs(p.t - 120) < 15 for p in con), "no deberia reportar el arranque"
    assert any(abs(p.t - 3400) < 15 for p in con), "no tiene que perder el pico del final"


def test_la_despedida_se_etiqueta_como_cierre():
    """Asi se puede descartar por lo que dice el chat y no por donde cae."""
    msgs = chat(duracion=3600, picos=((3400, 20, "bye bye chau"),))
    picos = detectar(msgs, top=5)
    assert picos, "no detecto la despedida"
    assert picos[0].etiqueta == "cierre", f"etiqueta: {picos[0].etiqueta}"


def test_normaliza_el_alargue_de_letras():
    from clips.vocabulario import tokenizar
    assert tokenizar("siiii seee romanceeee") == ["si", "se", "romance"]


def test_lo_que_se_eleva_en_todos_los_picos_es_reaccion_no_tema():
    """El caso real de Twitch: 'opa' sube en CADA pico, 'faker' en uno solo."""
    from clips.vocabulario import analizar_picos
    msgs = chat(duracion=7200, picos=(
        (900, 15, "opa opa faker la rompe"),
        (2700, 15, "opa opa que pasa aca"),
        (4500, 15, "opa opa no lo puedo creer"),
    ))
    a = analizar_picos(msgs, [900, 2700, 4500], 5.0)
    primero = " ".join(a[0].tema)
    assert "faker" in primero, f"perdio el tema propio: {a[0].tema}"
    assert "opa" not in primero, f"'opa' se eleva en los tres picos, es reaccion: {a[0].tema}"
    assert any("opa" in r for r in a[0].reaccion), f"deberia estar en reaccion: {a[0].reaccion}"


def test_el_ranking_premia_el_acuerdo_sobre_el_volumen():
    """Un pico mas chico pero con tema claro tiene que ganarle a uno grande sin tema."""
    msgs = chat(duracion=7200, picos=(
        (900, 30, "xdd que de"),              # mucho volumen, nada que decir
        (2700, 18, "faker penta increible"),  # menos volumen, tema claro
    ))
    por_score = detectar(msgs, ordenar="score", top=5)
    por_prior = detectar(msgs, ordenar="prioridad", top=5)
    assert abs(por_score[0].t - 900) < 15, "por score deberia ganar el de mas volumen"
    assert abs(por_prior[0].t - 2700) < 15, (
        f"por prioridad deberia ganar el del tema claro; quedo {por_prior[0].t}")


def test_los_artefactos_van_al_final():
    msgs = chat(duracion=7200, picos=(
        (6500, 30, "bye bye chau gg"),        # despedida: el score mas alto
        (2700, 12, "faker penta increible"),
    ))
    picos = detectar(msgs, top=5)
    assert picos[-1].etiqueta == "cierre", f"el cierre tiene que ir ultimo: {[p.etiqueta for p in picos]}"
    assert picos[-1].score > picos[0].score, "justamente: tenia mas score y aun asi va ultimo"
