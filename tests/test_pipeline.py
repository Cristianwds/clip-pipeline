"""Tests de `pipeline.producir`, con las tres etapas reemplazadas.

Lo que se prueba es la orquestación: qué clip sale con qué pico, y qué queda cuando
una etapa falla. Ninguna etapa real corre, así que no hace falta video ni red.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from clips import cortar as mod_cortar, estilos, pipeline, reencuadre, subtitulos
from clips.picos import Pico
from clips.subtitulos import Palabra


def _picos():
    return [
        Pico(t=600.0, score=6.0, tasa=4.0, tema=["romanse×8", "malas×9"], foco=0.5, etiqueta="gracioso"),
        Pico(t=1800.0, score=5.0, tasa=3.0, tema=["clap×40"], foco=0.4, etiqueta="épico"),
        Pico(t=3000.0, score=4.5, tasa=3.0, tema=["faker×9"], foco=0.3),
    ]


@pytest.fixture
def etapas(monkeypatch):
    """Etapas falsas. Devuelve el registro de llamadas y dónde hacer fallar cada una."""
    # el audio arranca en el segundo 8 del corte: después de cualquier holgura
    r = SimpleNamespace(cortes=[], cortes_que_fallan=set(), reencuadre_falla=False,
                        subtitulos_fallan=False, rematar=[], reencuadres=[], lineas=[],
                        palabras=[Palabra(8.0, 8.4, "romance"), Palabra(8.5, 8.6, "o"),
                                  Palabra(8.7, 9.1, "malas")])

    def cortar(url, picos, duraciones=None, carpeta=None, primero=1, **kw):
        r.cortes.append((picos[0].t, primero, duraciones[0]))
        if picos[0].t in r.cortes_que_fallan:
            return []
        return [Path(f"pico_{primero:02d}.mp4")]

    def reencuadrar(entrada, modo="desenfoque", facecam=None, desde=0.0):
        if r.reencuadre_falla:
            raise RuntimeError("ffmpeg se cayó")
        r.reencuadres.append(desde)
        return entrada.with_name(entrada.stem + "_9x16.mp4")

    def transcribir(video, modelo="small", idioma=None):
        if r.subtitulos_fallan:
            raise RuntimeError("whisper se cayó")
        return list(r.palabras), "es"

    def rematar(video, lineas, **kw):
        r.rematar.append(kw)
        r.lineas.append(lineas)
        return video.with_name(video.stem + "_sub.mp4")

    monkeypatch.setattr(mod_cortar, "cortar", cortar)
    monkeypatch.setattr(reencuadre, "reencuadrar", reencuadrar)
    monkeypatch.setattr(subtitulos, "transcribir", transcribir)
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


# ---------------------------------------------------------------- el tramo del clip

def _con_rafaga(antes, despues, t=600.0):
    return Pico(t=t, score=6.0, tasa=4.0, inicio=t - antes, fin=t + despues)


def test_el_largo_sale_de_la_rafaga_y_no_de_la_variante(etapas):
    corta, larga = _con_rafaga(3, 5), _con_rafaga(3, 20, t=1800.0)
    _producir([corta, larga], formato="nada", variante="directo")
    (_, _, a), (_, _, b) = etapas.cortes
    assert sum(b) > sum(a)
    etapas.cortes.clear()
    for v in estilos.VARIANTES:
        _producir([corta], formato="nada", variante=v.nombre)
    assert len({c[2] for c in etapas.cortes}) == 1


def test_el_clip_entra_bastante_antes_de_que_reaccione_el_chat():
    """Lo que se midió: en los tres picos transcriptos, lo que había que ver empezaba
    entre 12 y 28 s antes del centro, con el chat arrancando 4 a 6 s antes."""
    for rafaga_antes in (4, 5, 6):
        antes, _ = pipeline.tramo(_con_rafaga(rafaga_antes, 8))
        assert antes >= 22


@pytest.mark.parametrize("antes", [0, 4, 13, 39, 45])
@pytest.mark.parametrize("despues", [0, 3, 13, 27, 45])
def test_ningun_clip_pasa_el_tope_ni_arranca_sobre_la_reaccion(antes, despues):
    a, d = pipeline.tramo(_con_rafaga(antes, despues))
    assert a + d <= pipeline.TOPE
    assert a >= pipeline.ANTES_MINIMO, "con menos que eso el clip vuelve a arrancar tarde"
    assert d >= pipeline.DESPUES_MINIMO, "el remate llega cuando el chat ya está bajando"


def test_si_no_entra_todo_se_acorta_la_entrada_y_no_la_reaccion():
    a, d = pipeline.tramo(_con_rafaga(10, 20))        # pediría 28 + 24 = 52
    assert d == 24 and a == pipeline.TOPE - 24


def test_un_pico_sin_rafaga_medida_igual_tiene_tramo():
    # los picos guardados antes de este cambio no traen la ráfaga
    assert pipeline.tramo(Pico(t=600.0, score=6.0, tasa=4.0)) == (
        pipeline.MARGEN_ENTRADA, pipeline.DESPUES_MINIMO)


# ---------------------------------------------------------------- el inicio en una frase

def test_solo_se_baja_de_mas_cuando_el_inicio_se_puede_ajustar(etapas):
    """Ajustar el inicio necesita la transcripción (para saber dónde cae la frase) y
    el reencuadre (que es el que recorta). Sin alguno de los dos, bajar de más es
    dejar segundos sobrantes en el clip."""
    pico = _con_rafaga(4, 8)
    nominal = pipeline.tramo(pico)
    for kw in ({"formato": "nada"}, {"formato": "nada", "subtitulos": True},
               {"formato": "desenfoque"}):
        etapas.cortes.clear()
        _producir([pico], **kw)
        assert etapas.cortes[0][2] == nominal, kw
    etapas.cortes.clear()
    _producir([pico], formato="desenfoque", subtitulos=True)
    assert etapas.cortes[0][2] == (nominal[0] + pipeline.HOLGURA, nominal[1])


def test_el_ajuste_a_la_frase_no_pasa_el_tope(etapas):
    pico = _con_rafaga(10, 20)                         # ya está en el tope
    _producir([pico], formato="desenfoque", subtitulos=True)
    assert sum(etapas.cortes[0][2]) == pipeline.TOPE


def test_el_clip_arranca_donde_empieza_la_frase_y_los_subtitulos_se_corren(etapas):
    # del corte se usarían los segundos desde el 6 (la holgura). La frase más
    # cercana empieza en el 4: el clip arranca ahí y esa palabra pasa a ser el segundo 0
    etapas.palabras = [Palabra(1.0, 1.5, "antes."), Palabra(4.0, 4.5, "Mirá"),
                       Palabra(4.6, 5.0, "esto")]
    (h,) = _producir([_con_rafaga(4, 8)], formato="desenfoque", subtitulos=True)
    desde = etapas.reencuadres[0]
    assert desde == pytest.approx(4.0 - subtitulos.RESPIRO)
    assert h.lineas == ["Mirá esto"], "lo que se dijo antes del inicio no va al clip"
    assert etapas.lineas[0][0].t0 == pytest.approx(subtitulos.RESPIRO)


def test_si_falla_el_reencuadre_los_subtitulos_no_se_corren(etapas):
    # el archivo que queda es el corte entero, con la holgura adentro
    etapas.reencuadre_falla = True
    _producir([_con_rafaga(4, 8)], formato="desenfoque", subtitulos=True)
    assert etapas.lineas[0][0].t0 == pytest.approx(8.0)


def test_sin_voz_el_clip_sale_igual_con_el_inicio_del_chat(etapas):
    etapas.palabras = []
    (h,) = _producir([_con_rafaga(4, 8)], formato="desenfoque", subtitulos=True)
    assert etapas.reencuadres == [pipeline.HOLGURA]
    assert h.final.name == "pico_01_9x16.mp4" and h.avisos[0].startswith("subtítulos:")


def test_lo_quemado_sale_con_la_ortografia_del_audio_y_no_la_del_chat(etapas):
    # el chat escribió «romanse»; el audio dice «romance»
    (h,) = _producir(_picos()[:1], formato="nada", subtitulos=True, variante="directo")
    assert h.gancho and "romanse" not in h.gancho.lower()
    assert etapas.rematar[0]["gancho"] == h.gancho
    assert "romance" in etapas.rematar[0]["claves"]


@pytest.mark.parametrize("kw", [{"variante": "crudo"}, {"variante": "directo", "sin_gancho": True}])
def test_sin_gancho_no_se_quema_ningun_cartel(etapas, kw):
    (h,) = _producir(_picos()[:1], formato="nada", subtitulos=True, **kw)
    assert h.gancho == "" and etapas.rematar[0]["gancho"] == ""
