"""Test del detector con chat sintetico: si no encuentra picos que YO puse, no sirve."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from clips.chat import Mensaje
from clips.picos import detectar


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
