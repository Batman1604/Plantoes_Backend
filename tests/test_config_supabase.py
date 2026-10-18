"""Validação das variáveis do Supabase (canal do código por e-mail).

No deploy do Render, uma das duas ficou vazia e a mensagem "precisam ser
definidas juntas" não dizia qual. Colar no painel também traz espaço ou
quebra de linha junto.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = dict(database_url="sqlite://", environment="test", jwt_secret_key="x" * 40)


def _settings(**extra):
    return Settings(_env_file=None, **BASE, **extra)


def test_so_a_url_diz_que_falta_a_chave():
    with pytest.raises(ValidationError, match="SUPABASE_ANON_KEY está vazia"):
        _settings(supabase_url="https://abc.supabase.co")


def test_so_a_chave_diz_que_falta_a_url():
    with pytest.raises(ValidationError, match="SUPABASE_URL está vazia"):
        _settings(supabase_anon_key="chave-anon")


def test_espacos_colados_no_painel_contam_como_vazio():
    with pytest.raises(ValidationError, match="SUPABASE_ANON_KEY está vazia"):
        _settings(supabase_url="https://abc.supabase.co", supabase_anon_key="  \n")


def test_valores_com_espaco_em_volta_sao_limpos():
    s = _settings(supabase_url=" https://abc.supabase.co\n", supabase_anon_key=" chave-anon ")
    assert (s.supabase_url, s.supabase_anon_key) == ("https://abc.supabase.co", "chave-anon")


def test_url_sem_https_e_recusada():
    with pytest.raises(ValidationError, match="https://"):
        _settings(supabase_url="abc.supabase.co", supabase_anon_key="chave-anon")


def test_nenhuma_das_duas_e_valido():
    s = _settings()
    assert (s.supabase_url, s.supabase_anon_key) == ("", "")
