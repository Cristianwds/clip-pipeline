"""Lo que ningún test puede tocar: el registro real del usuario."""
import sys
from pathlib import Path

import pytest

# conftest se carga antes que cualquier test: con esto alcanza para todos, incluido
# correr un archivo suelto que no agregue `src` por su cuenta
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clips import lote  # noqa: E402


@pytest.fixture(autouse=True)
def _respaldos_fuera_de_la_config_real(tmp_path, monkeypatch):
    # `lote.guardar` respalda el estado en ~/.config/clip-pipeline/respaldos. Un test
    # que guardara en la ruta por defecto dejaría ahí estados sintéticos mezclados
    # con las copias de los veredictos de verdad.
    monkeypatch.setattr(lote, "RESPALDOS", tmp_path / "respaldos_de_prueba")
