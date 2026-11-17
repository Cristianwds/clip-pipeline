"""Test del detector con chat sintetico: si no encuentra picos que YO puse, no sirve."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from clips.chat import Mensaje
from clips.picos import detectar, rafaga, ventana_sugerida


def chat_sintetico(duracion=7200, base=1.5, picos=((900, 20), (3000, 15), (5400, 30)), seed=7):
    """Chat con una tasa de fondo que varia lento + picos gaussianos en momentos conocidos."""
    rng = np.random.default_rng(seed)
    t = []
    # fondo: la audiencia crece y decrece a lo largo del stream
    for s in range(duracion):
        lam = base * (1 + 0.5 * np.sin(2 * np.pi * s / duracion))
        t += [s + rng.random() for _ in range(rng.poisson(lam))]
    # picos: 8 segundos de chat acelerado
    for centro, mult in picos:
        for s in range(centro - 4, centro + 4):
            t += [s + rng.random() for _ in range(rng.poisson(base * mult))]
    return [Mensaje(x, "u", "KEKW") for x in sorted(t)]


def test_encuentra_los_picos_inyectados():
    esperados = [900, 3000, 5400]
    msgs = chat_sintetico()
    hallados = detectar(msgs, top=5)
    assert hallados, "no detecto ningun pico"
    for e in esperados:
        assert any(abs(p.t - e) <= 10 for p in hallados), \
            f"no encontro el pico de {e}s; hallados: {[round(p.t) for p in hallados]}"


def test_no_inventa_picos_en_chat_plano():
    """El caso que importa: si no paso nada, tiene que decir que no paso nada."""
    msgs = chat_sintetico(picos=())
    hallados = detectar(msgs, top=10)
    assert hallados == [], f"invento {len(hallados)} picos en un chat sin picos: {hallados}"


def test_respeta_la_separacion_minima():
    msgs = chat_sintetico(picos=((900, 20), (930, 20)))
    hallados = detectar(msgs, separacion=90, top=10)
    ts = sorted(p.t for p in hallados)
    assert all(b - a >= 90 for a, b in zip(ts, ts[1:])), f"picos pegados: {ts}"


def test_el_umbral_crece_con_la_cantidad_de_bins():
    """Mirar mas ventanas tiene que exigir mas evidencia por ventana."""
    from clips.picos import umbral_automatico
    assert umbral_automatico(100) < umbral_automatico(10000)
    assert 3.0 < umbral_automatico(1440) < 6.0


# ---------------------------------------------------------------- chats chicos

def chat_chico(duracion=14400, base=0.15, rafagas=((3000, 22), (9000, 24)), ancho=20, seed=3):
    """Chat de canal mediano: menos de un mensaje cada 5 s, y reacciones que no son
    un golpe sino un par de docenas de mensajes repartidos en unos 20 segundos.

    Las ráfagas son de 22+ mensajes porque ese es el piso real: a 0,15 msg/s una de
    14 queda justo en el umbral y se detecta o no según cómo caiga en los bins. Un
    chat chico necesita una reacción proporcionalmente mucho más grande que uno
    grande, y eso no lo arregla ninguna ventana."""
    rng = np.random.default_rng(seed)
    t = list(rng.uniform(0, duracion, rng.poisson(base * duracion)))
    for inicio, n in rafagas:
        t += list(rng.uniform(inicio, inicio + ancho, n))
    return [Mensaje(x, "u", "jaja") for x in sorted(t)]


def test_la_ventana_se_ensancha_solo_para_chats_chicos():
    from clips.picos import ventana_sugerida

    def con_tasa(tasa, duracion=3600):
        n = int(tasa * duracion)
        return [Mensaje(duracion * (i + 1) / n, "u", "x") for i in range(n)]

    # todo lo validado con chats grandes (1,7 a 5,8 msg/s) sigue en 5 s
    for tasa in (0.8, 1.74, 4.57, 20):
        assert ventana_sugerida(con_tasa(tasa)) == 5.0
    assert ventana_sugerida(con_tasa(0.46)) == 10.0
    assert ventana_sugerida(con_tasa(0.21)) == 20.0
    assert ventana_sugerida(con_tasa(0.01)) == 30.0      # tope: más ancho ya no ubica el momento
    assert ventana_sugerida([]) == 5.0


def _aciertos(ancho, sugerida, semillas=20):
    from clips.picos import ventana_sugerida
    n = 0
    for seed in range(semillas):
        msgs = chat_chico(rafagas=((6000, 24),), ancho=ancho, seed=seed)
        ventana = ventana_sugerida(msgs) if sugerida else 5.0
        n += any(abs(p.t - (6000 + ancho / 2)) <= 15 for p in detectar(msgs, ventana=ventana))
    return n


def test_en_un_chat_chico_la_ventana_sugerida_ve_lo_que_la_fija_no():
    """Se mide sobre 20 chats y no sobre uno: en el borde del umbral, que una ráfaga
    puntual se detecte depende de cómo cae en los bins. Medido al escribir esto: la
    ventana fija de 5 s encontraba 6 de 40 ráfagas de 20 segundos; la sugerida, 40."""
    for ancho in (6, 20):
        assert _aciertos(ancho, sugerida=True) >= 19
        assert _aciertos(ancho, sugerida=False) <= 10


def test_la_ventana_ancha_tampoco_inventa_picos():
    """Ensanchar no puede ser una forma disimulada de bajar el umbral."""
    from clips.picos import ventana_sugerida
    inventados = 0
    for base in (0.15, 0.4):
        for seed in range(20):
            msgs = chat_chico(rafagas=(), base=base, seed=seed)
            inventados += len(detectar(msgs, ventana=ventana_sugerida(msgs)))
    assert inventados == 0, f"{inventados} picos en 40 chats donde no pasó nada"


def test_el_pico_de_una_ventana_ancha_se_ubica_dentro_de_la_rafaga():
    # 30 mensajes en 6 segundos, al final de una ventana de 30: sin afinar, el
    # centro del bin (6015) queda lejos y el clip arranca antes de tiempo
    msgs = chat_chico(rafagas=((6021, 30),), ancho=6)
    hallados = detectar(msgs, ventana=30.0)
    assert any(6019 <= p.t <= 6029 for p in hallados), [round(p.t) for p in hallados]


# ---------------------------------------------------------------- la ráfaga

def _tiempos(mensajes):
    return np.array([m.t for m in mensajes])


def test_la_rafaga_abarca_la_reaccion_y_no_mucho_mas():
    # fondo de 1,5 msg/s y una reacción de 20 s, de 2990 a 3010
    rng = np.random.default_rng(1)
    t = list(rng.uniform(0, 7200, rng.poisson(1.5 * 7200))) + list(rng.uniform(2990, 3010, 300))
    inicio, fin = rafaga(np.sort(t), 3000.0)
    assert abs(inicio - 2990) <= 3 and abs(fin - 3010) <= 3, (inicio, fin)


def test_la_rafaga_de_un_chat_chico_no_se_corta_en_el_primer_segundo_vacio():
    """A 0,15 msg/s de fondo, una reacción de 20 s tiene segundos sin ningún mensaje
    en el medio. Cortar en el primero daría una ráfaga de 2 o 3 s."""
    rng = np.random.default_rng(4)
    fondo = list(rng.uniform(0, 14400, rng.poisson(0.15 * 14400)))
    # 2 mensajes por segundo de 9000 a 9020, salvo uno de cada tres, que queda vacío
    rafaga_ = [9000 + s + f for s in range(20) if s % 3 != 2 for f in (0.2, 0.6)]
    inicio, fin = rafaga(np.sort(fondo + rafaga_), 9004.0)
    assert inicio <= 9001 and fin >= 9018, (inicio, fin)


def test_la_rafaga_de_un_chat_chico_cae_sobre_la_reaccion():
    # con mensajes al azar la reacción puede salir más apretada que los 20 s
    # inyectados, pero tiene que estar ahí y no irse a otro lado
    for semilla in range(10):
        ms = chat_chico(seed=semilla)
        p = next(p for p in detectar(ms, ventana=ventana_sugerida(ms)) if abs(p.t - 9010) < 15)
        assert 8995 <= p.inicio <= p.t <= p.fin <= 9028 and p.fin - p.inicio >= 5, (
            semilla, p.inicio, p.fin)


def test_sin_reaccion_no_hay_rafaga_larga():
    # en un chat plano lo que se encuentra alrededor de un punto cualquiera es ruido
    t = _tiempos(chat_sintetico(picos=()))
    largos = [fin - inicio for inicio, fin in (rafaga(t, c) for c in range(1000, 6000, 500))]
    assert max(largos) <= 12, largos


def test_la_rafaga_en_un_tramo_sin_mensajes_es_de_largo_cero():
    t = np.sort(np.random.default_rng(2).uniform(0, 1000, 800))
    assert rafaga(t, 5000.0) == (5000.0, 5000.0)


def test_cada_pico_detectado_trae_su_rafaga():
    for p in detectar(chat_sintetico()):
        assert p.inicio <= p.t <= p.fin and p.fin - p.inicio >= 4, (p.t, p.inicio, p.fin)
