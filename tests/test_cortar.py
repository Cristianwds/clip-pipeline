"""Tests del corte que no bajan nada.

`cortar` es casi toda E/S, pero la parte que falló de verdad es lógica: qué pasa cuando
yt-dlp se cuelga. Acá yt-dlp se reemplaza por un proceso local o por una función.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from clips import cortar
from clips.picos import Pico


def _pico(t=600.0):
    return Pico(t=t, score=6.0, tasa=4.0)


def _vivo(pid: int) -> bool:
    """Si el proceso sigue corriendo. Un zombi cuenta como muerto: ya no hace nada."""
    try:
        estado = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return estado != "Z"


# ---------------------------------------------------------------- el tope de tiempo

def test_un_comando_que_termina_devuelve_su_salida():
    r = cortar._correr([sys.executable, "-c", "print('hola')"], tope=30)
    assert r.returncode == 0 and r.stdout.strip() == "hola"


def test_el_tope_mata_tambien_a_los_hijos(tmp_path):
    """Lo que pasó el 2026-11-16: yt-dlp murió y el ffmpeg que había lanzado siguió
    vivo, con el archivo en 0 bytes. Matar solo al proceso directo no alcanza."""
    anotado = tmp_path / "pid"
    # un padre que lanza un hijo de 60 s y se queda esperándolo, como yt-dlp con ffmpeg
    padre = ("import subprocess, sys, pathlib; "
             "h = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
             f"pathlib.Path({str(anotado)!r}).write_text(str(h.pid)); h.wait()")
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        cortar._correr([sys.executable, "-c", padre], tope=1.5)
    assert time.monotonic() - t0 < 15, "tenía que volver apenas vence, no esperar al hijo"
    nieto = int(anotado.read_text())
    for _ in range(50):                      # el kernel puede tardar un instante
        if not _vivo(nieto):
            break
        time.sleep(0.1)
    assert not _vivo(nieto)


@pytest.fixture
def sin_red(monkeypatch, tmp_path):
    """Reemplaza `_correr`. Cada llamada toma el siguiente comportamiento de la lista:
    «ok» deja el archivo, «cuelga» deja restos y vence, «falla» no deja nada."""
    guion, llamadas = [], []

    def correr(cmd, tope):
        llamadas.append(cmd)
        que = guion.pop(0) if guion else "ok"
        base = Path(cmd[cmd.index("-o") + 1].replace(".%(ext)s", ""))
        if que == "cuelga":
            # se colgó bajando el audio: queda el parcial y la pista de video, entera
            Path(str(base) + ".f140.m4a.part").write_bytes(b"")
            Path(str(base) + ".f398.mp4").write_bytes(b"video mudo")
            raise subprocess.TimeoutExpired(cmd, tope)
        if que == "ok":
            Path(str(base) + ".mp4").write_bytes(b"video")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: no se pudo")

    monkeypatch.setattr(cortar, "_correr", correr)
    return guion, llamadas


def test_un_corte_colgado_se_reintenta_una_vez(sin_red, tmp_path):
    guion, llamadas = sin_red
    guion += ["cuelga", "ok"]
    hechos = cortar.cortar("https://x/vod", [_pico()], carpeta=tmp_path)
    assert len(llamadas) == 2
    assert [h.name for h in hechos] == ["pico_01_0h10m00s_22s.mp4"]
    assert not list(tmp_path.glob("*.part")), "el parcial del intento colgado tenía que borrarse"


def test_dos_cuelgues_no_tiran_la_tanda_ni_dejan_parciales(sin_red, tmp_path, capsys):
    """Un pico que no se puede cortar no puede llevarse puestos a los siguientes."""
    guion, llamadas = sin_red
    guion += ["cuelga", "cuelga", "ok"]
    hechos = cortar.cortar("https://x/vod", [_pico(600.0), _pico(1800.0)], carpeta=tmp_path)
    assert len(llamadas) == 3, "dos intentos para el primero y uno para el segundo"
    assert [h.name for h in hechos] == ["pico_02_0h30m00s_22s.mp4"]
    assert [q.name for q in tmp_path.iterdir()] == ["pico_02_0h30m00s_22s.mp4"], \
        "la pista de video que quedó del cuelgue no puede pasar por el clip"
    assert "no terminó" in capsys.readouterr().out


def test_un_error_comun_no_se_reintenta(sin_red, tmp_path):
    # reintentar un VOD borrado o privado es perder el doble de tiempo
    guion, llamadas = sin_red
    guion += ["falla"]
    assert cortar.cortar("https://x/vod", [_pico()], carpeta=tmp_path) == []
    assert len(llamadas) == 1


def test_el_tope_alcanza_para_un_corte_normal():
    # un corte de 70 s tarda menos de un minuto; el tope tiene que dejar margen
    # para una conexión lenta sin ser otra forma de «para siempre»
    assert 120 <= cortar.TIEMPO_MAXIMO <= 1800
