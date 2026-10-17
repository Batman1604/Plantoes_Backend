"""Senha com caractere especial na DATABASE_URL não pode derrubar o Alembic.

A senha vai percent-encoded na URL (ex.: "@" vira "%40") — é assim que o
Supabase, e qualquer Postgres gerenciado com senha gerada, entrega a string de
conexão. O migrations/env.py repassa a URL ao Alembic por
config.set_main_option, que passa pela interpolação do ConfigParser: um "%"
solto ali levantava "invalid interpolation syntax" antes de qualquer migração
rodar.
"""

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config

from app.core.config import settings

_RAIZ = Path(__file__).resolve().parent.parent


class TestAlembicUrlComPercent(unittest.TestCase):
    def test_gera_sql_com_senha_percent_encoded(self):
        # Sem o alembic.ini: com ele o env.py roda fileConfig, que desliga os
        # loggers já criados e quebra os assertLogs de outros módulos da suíte.
        cfg = Config()
        cfg.set_main_option("script_location", str(_RAIZ / "migrations"))
        url = "postgresql+psycopg://postgres.ref:s3nh%40%25forte@host.pooler.supabase.com:5432/postgres"
        saida = io.StringIO()
        with patch.object(settings, "database_url", url), contextlib.redirect_stdout(saida):
            # Só a primeira revisão: as seguintes inspecionam o banco e não rodam
            # em modo --sql. O que importa aqui é o env.py aceitar a URL.
            command.upgrade(cfg, "09a7c5244080", sql=True)
        self.assertIn("CREATE TABLE", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
