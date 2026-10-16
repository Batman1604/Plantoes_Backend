"""Guarda da pasta de testes da auditoria 2026-10.

O `tests/conftest.py` raiz já fixa DATABASE_URL num SQLite em arquivo antes de
qualquer import de `app`. Aqui só se CONFERE isso (e que nenhuma credencial de
serviço externo está configurada), para que nenhum teste desta pasta encoste
no Postgres do `.env` nem dispare WhatsApp/SMTP/Anthropic de verdade.
"""

import sys
from pathlib import Path

import pytest

# Permite `import aud_helpers` a partir dos módulos desta pasta.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True, scope="session")
def _ambiente_isolado():
    from app.core.config import settings
    from app.core.database import engine

    assert settings.database_url.startswith("sqlite"), settings.database_url[:10]
    assert engine.url.get_backend_name() == "sqlite"
    assert not settings.smtp_username and not settings.smtp_password
    assert not settings.whatsapp_token and not settings.whatsapp_phone_number_id
    assert not settings.anthropic_api_key
    yield
