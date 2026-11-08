"""Tests de la trayectoria del encuadre. Son las dos funciones puras: no hace falta video."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from clips.reencuadre import _perfil_a_centro, _suavizar


def test_centra_donde_esta_el_movimiento():
    perfil = np.zeros(320)
    perfil[200:240] = 10.0
    assert abs(_perfil_a_centro(perfil, 40) - 220) <= 2


def test_elige_la_franja_con_mas_movimiento_no_el_pico_aislado():
    """Una columna altisima no deberia ganarle a una franja ancha con mas energia total."""
    perfil = np.zeros(320)
    perfil[10] = 100.0            # un pico finito
    perfil[200:260] = 5.0         # una franja ancha: 300 en total
    assert _perfil_a_centro(perfil, 60) > 150


def test_si_la_ventana_no_cabe_devuelve_el_centro():
    assert _perfil_a_centro(np.ones(100), 200) == 50


def test_la_zona_muerta_evita_el_temblor():
    """Oscilar unos pocos px no tiene que mover la camara."""
    centros = [500, 505, 498, 503, 497, 502, 500, 504, 499]
    out = _suavizar(centros, zona_muerta=25.0)
    assert len(set(round(v, 3) for v in out)) == 1, f"la camara tembló: {out}"


def test_respeta_el_limite_de_velocidad():
    """Un salto brusco se recorre de a poco: si no, el encuadre marea."""
    centros = [0] * 5 + [600] * 25
    out = _suavizar(centros, salto_max=12.0)
    saltos = [abs(b - a) for a, b in zip(out, out[1:])]
    assert max(saltos) <= 12.0 + 1e-9, f"salto de {max(saltos)} px"
    assert out[-1] > out[0], "no llegó a moverse hacia el objetivo"


def test_sin_muestras_no_explota():
    assert _suavizar([]) == []


def test_las_bandas_suman_el_alto_y_son_pares():
    """h264 necesita dimensiones pares; y si no suman, queda una franja negra."""
    from clips.reencuadre import _bandas
    for frac in (0.2, 0.33, 0.34, 0.5, 0.75):
        cam, juego = _bandas(1920, frac)
        assert cam + juego == 1920, f"{cam}+{juego} != 1920 con {frac}"
        assert cam % 2 == 0 and juego % 2 == 0, f"impar con {frac}: {cam}, {juego}"


def test_el_recorte_de_la_banda_no_deforma():
    """El recorte en el original tiene que tener el mismo aspecto que la banda destino."""
    from clips.reencuadre import _ancho_crop
    ancho = _ancho_crop(720, 1080, 1268)
    assert abs((ancho / 720) - (1080 / 1268)) < 0.01, f"aspecto roto: {ancho}x720"


def test_el_dividido_exige_saber_donde_esta_la_facecam():
    import pytest
    from clips.reencuadre import reencuadrar
    with pytest.raises(ValueError, match="facecam"):
        reencuadrar(Path("no_importa.mp4"), modo="dividido")


def test_los_comandos_de_paneo_quedan_dentro_del_cuadro():
    """Nunca se puede pedir una x que se salga del video: ffmpeg aborta."""
    from clips.reencuadre import _comandos_crop
    import tempfile
    puntos = [(0.0, -500.0), (1.0, 640.0), (2.0, 9999.0)]
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "c.txt"
        _comandos_crop(puntos, ancho=400, W=1280, destino=f)
        xs = [int(l.split()[-1].rstrip(";")) for l in f.read_text().splitlines()]
    assert all(0 <= x <= 1280 - 400 for x in xs), xs


def test_el_recorte_evita_repetir_la_facecam():
    """Si hay lugar, el recorte de la accion no tiene que incluir la facecam."""
    from clips.reencuadre import _limites_sin_facecam
    lo, hi = _limites_sin_facecam((4, 268, 320, 188), ancho=612, W=1280)
    assert lo >= 324, f"el recorte podria empezar en {lo} y pisar la facecam"
    assert hi >= lo, "rango vacio"


def test_si_no_hay_lugar_no_se_traba():
    """Con una facecam enorme no se puede evitar: mejor repetirla que no recortar nada."""
    from clips.reencuadre import _limites_sin_facecam
    lo, hi = _limites_sin_facecam((300, 0, 700, 400), ancho=900, W=1280)
    assert (lo, hi) == (0, 1280 - 900)
