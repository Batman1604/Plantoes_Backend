"""DATABASE_URL incompleta falha com mensagem que nomeia o que falta.

Caso real do deploy no Render: o valor colado no painel era
"postgresql+psycopg:/?sslmode=require".
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = dict(environment="test", jwt_secret_key="x" * 40)


def _settings(url):
    return Settings(_env_file=None, database_url=url, **BASE)


def test_url_sem_host_usuario_e_senha_diz_o_que_falta():
    with pytest.raises(ValidationError, match="provavelmente incompleta"):
        _settings("postgresql+psycopg:/?sslmode=require")


def test_url_sem_senha_diz_que_falta_a_senha():
    with pytest.raises(ValidationError, match="falta senha"):
        _settings("postgresql+psycopg://postgres.abc@aws-0-us-west-2.pooler.supabase.com:5432/postgres")


def test_erro_de_producao_nao_vaza_a_senha(monkeypatch):
    # O str() cru do Pydantic cita o valor; o que sobe no boot é _carregar_settings.
    from app.core.config import _carregar_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://usuario:SEGREDO123@/postgres")
    with pytest.raises(RuntimeError) as exc:
        _carregar_settings()
    assert "DATABASE_URL" in str(exc.value)
    assert "SEGREDO123" not in str(exc.value)


def test_url_completa_e_aceita_e_limpa_espacos():
    url = "postgresql+psycopg://postgres.abc:senha@aws-0-us-west-2.pooler.supabase.com:5432/postgres?sslmode=require"
    assert _settings(" " + url + "\n").database_url == url


def test_sqlite_dos_testes_continua_valido():
    assert _settings("sqlite:///x.db").database_url == "sqlite:///x.db"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_producao_recusa_banco_na_propria_maquina(host):
    with pytest.raises(ValidationError, match="própria máquina do servidor"):
        Settings(
            _env_file=None, environment="production", jwt_secret_key="x" * 40,
            allowed_origins="https://x.onrender.com",
            database_url=f"postgresql+psycopg://postgres:senha@{host}:5432/plantoes",
        )


def test_desenvolvimento_continua_aceitando_localhost():
    s = _settings("postgresql+psycopg://postgres:senha@localhost:5432/plantoes")
    assert s.database_url.endswith("@localhost:5432/plantoes")
