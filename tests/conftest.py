"""Carga de los servidores MCP para las pruebas.

Los tres servidores se llaman server.py, así que se cargan por ruta con nombres
distintos (cima_server, pubmed_server, elactancia_server).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

# pubmed/server.py hace `import lactmed` (módulo de su misma carpeta).
sys.path.insert(0, str(RAIZ / "mcp" / "pubmed"))


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


cima = _cargar("cima_server", RAIZ / "mcp" / "cima" / "server.py")
pubmed = _cargar("pubmed_server", RAIZ / "mcp" / "pubmed" / "server.py")
elactancia = _cargar("elactancia_server", RAIZ / "mcp" / "elactancia" / "server.py")
import lactmed  # noqa: E402


@pytest.fixture
def indice_lactmed():
    """Salta la prueba si no se ha generado el índice local de LactMed."""
    if lactmed.cargar() is None:
        pytest.skip("Falta el índice de LactMed: uv run python mcp/pubmed/lactmed.py")
