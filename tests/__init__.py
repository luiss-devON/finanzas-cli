import sys
from pathlib import Path

# Permite ejecutar los tests sin instalar el paquete: python -m unittest -v
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
