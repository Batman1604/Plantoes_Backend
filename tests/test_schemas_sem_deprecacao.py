"""AUD-087: os schemas não usam APIs depreciadas do Pydantic.

Importa o módulo num subprocesso com `-W error`: qualquer aviso
(GenericModel, `class Config`, `Field(example=...)`) vira erro de import.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_import_dos_schemas_sem_aviso():
    r = subprocess.run(
        [sys.executable, "-W", "error", "-c", "import app.schemas.plantao_schema"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr[-2000:]
