"""Tests de `pipeline.producir`, con las tres etapas reemplazadas.

Lo que se prueba es la orquestación: qué clip sale con qué pico, y qué queda cuando
una etapa falla. Ninguna etapa real corre, así que no hace falta video ni red.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from clips import cortar as mod_cortar, estilos, pipeline, reencuadre, subtitulos
from clips.picos import Pico


def _picos():
    return [
        Pico(t=600.0, score=6.0, tasa=4.0, tema=["romanse×8", "malas×9"], foco=0.5, etiqueta="gracioso"),
        Pico(t=1800.0, score=5.0, tasa=3.0, tema=["clap×40"], foco=0.4, etiqueta="épico"),
        Pico(t=3000.0, score=4.5, tasa=3.0, tema=["faker×9"], foco=0.3),
    ]


@pytest.fixture
def etapas(monkeypatch):
    """Etapas falsas. Devuelve el registro de llamadas y dónde hacer fallar cada una."""
    r = SimpleNamespace(cortes=[], cortes_que_fallan=set(), reencuadre_falla=False,
                        subtitulos_fallan=False, rematar=[], audio="romance o malas")

    def cortar(url, picos, duraciones=None, carpeta=None, primero=1, **kw):
        r.cortes.append((picos[0].t, primero, duraciones[0]))
        if picos[0].t in r.cortes_que_fallan:
            return []
        return [Path(f"pico_{primero:02d}.mp4")]

    def reencuadrar(entrada, modo="desenfoque", facecam=None):
        if r.reencuadre_falla:
            raise RuntimeError("ffmpeg se cayó")
        return entrada.with_name(entrada.stem + "_9x16.mp4")

    def preparar(video, modelo, idioma, max_chars):
        if r.subtitulos_fallan:
            raise RuntimeError("la transcripción no devolvió palabras")
        return [SimpleNamespace(texto=r.audio)], "es"

    def rematar(video, lineas, **kw):
        r.rematar.append(kw)
        return video.with_name(video.stem + "_sub.mp4")

    monkeypatch.setattr(mod_cortar, "cortar", cortar)
    monkeypatch.setattr(reencuadre, "reencuadrar", reencuadrar)
    monkeypatch.setattr(subtitulos, "preparar", preparar)
    monkeypatch.setattr(subtitulos, "rematar", rematar)
    return r


def _producir(picos, **kw):
    return pipeline.producir("https://x/vod", picos, log=lambda _: None, **kw)


def test_un_corte_que_falla_no_le_cambia_el_pico_al_clip_siguiente(etapas):
    # el bug original: con `zip`, el tercer clip salía con el tema del segundo pico
    picos = _picos()
    etapas.cortes_que_fallan = {1800.0}
    hechos = _producir(picos, formato="nada")
    assert [(h.pico.t, h.final.name) for h in hechos] == [
        (600.0, "pico_01.mp4"), (3000.0, "pico_03.mp4")]


def test_si_falla_el_reencuadre_queda_el_corte_y_se_subtitula_igual(etapas):
    etapas.reencuadre_falla = True
    (h,) = _producir(_picos()[:1], formato="dividido", subtitulos=True)
    assert h.final.name == "pico_01_sub.mp4"         # sin el _9x16 en el medio
    assert h.avisos == ["reencuadre: ffmpeg se cayó"]


def test_si_fallan_los_subtitulos_queda_el_vertical_y_el_motivo(etapas):
    etapas.subtitulos_fallan = True
    (h,) = _producir(_picos()[:1], formato="desenfoque", subtitulos=True)
    assert h.final.name == "pico_01_9x16.mp4"
    assert h.lineas is None and h.gancho == ""
    assert len(h.avisos) == 1 and h.avisos[0].startswith("subtítulos:")


def test_sin_fallas_no_hay_avisos_y_el_archivo_pasa_por_las_tres_etapas(etapas):
    (h,) = _producir(_picos()[:1], formato="desenfoque", subtitulos=True)
    assert (h.final.name, h.avisos, h.lineas) == ("pico_01_9x16_sub.mp4", [], ["romance o malas"])


def test_una_variante_mal_escrita_falla_antes_de_cortar_nada(etapas):
    with pytest.raises(ValueError, match="variante desconocida"):
        _producir(_picos(), variante="direkto")
    assert etapas.cortes == []


def test_producir_un_pico_suelto_da_el_mismo_clip_que_en_la_tanda(etapas):
    picos = _picos()
    tanda = _producir(picos, formato="nada")
    cortes_tanda = list(etapas.cortes)
    etapas.cortes.clear()
    (suelto,) = _producir(picos[2:], formato="nada", primero=2)
    assert (suelto.variante, suelto.final.name) == (tanda[2].variante, tanda[2].final.name)
    assert etapas.cortes == cortes_tanda[2:]          # mismo número y mismo largo


def test_cada_clip_se_corta_con_el_largo_de_su_variante(etapas):
    hechos = _producir(_picos(), formato="nada")
    por_nombre = {v.nombre: (v.antes, v.despues) for v in estilos.VARIANTES}
    assert [c[2] for c in etapas.cortes] == [por_nombre[h.variante] for h in hechos]


def test_el_gancho_sale_con_la_ortografia_del_audio_y_no_la_del_chat(etapas):
    # el chat escribió «romanse»; el audio dice «romance»
    (h,) = _producir(_picos()[:1], formato="nada", subtitulos=True, variante="directo")
    assert "romance" in h.gancho.lower() and "romanse" not in h.gancho.lower()
    assert etapas.rematar[0]["gancho"] == h.gancho
    assert "romance" in etapas.rematar[0]["claves"]


@pytest.mark.parametrize("kw", [{"variante": "crudo"}, {"variante": "directo", "sin_gancho": True}])
def test_sin_gancho_no_se_quema_ningun_cartel(etapas, kw):
    (h,) = _producir(_picos()[:1], formato="nada", subtitulos=True, **kw)
    assert h.gancho == "" and etapas.rematar[0]["gancho"] == ""
