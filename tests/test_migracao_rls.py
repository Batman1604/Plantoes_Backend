"""Migração b7d41e9c3a58: RLS ligado em todas as tabelas de public.

Nenhum Postgres é tocado: o SQL é gerado em modo offline (dialeto
postgresql) e conferido; no SQLite a migração precisa ser no-op."""

import importlib.util
import io
import unittest
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRACAO = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "b7d41e9c3a58_rls_em_todas_as_tabelas.py"


def _migracao():
    spec = importlib.util.spec_from_file_location("migracao_rls", _MIGRACAO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _sql_postgres(passo: str) -> str:
    saida = io.StringIO()
    contexto = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": saida})
    with Operations.context(contexto):
        getattr(_migracao(), passo)()
    return saida.getvalue()


class TestMigracaoRls(unittest.TestCase):
    def test_upgrade_liga_rls_em_toda_tabela_de_public(self):
        sql = _sql_postgres("upgrade")
        self.assertIn("FROM pg_tables WHERE schemaname = 'public'", sql)
        # quote_ident: o nome da tabela é citado como identificador.
        self.assertIn("'ALTER TABLE public.' || quote_ident(tabela.tablename) || ' ENABLE ROW LEVEL SECURITY'", sql)
        self.assertNotIn("%", sql)
        self.assertNotIn("FORCE", sql)
        self.assertNotIn("CREATE POLICY", sql)

    def test_downgrade_desliga(self):
        sql = _sql_postgres("downgrade")
        self.assertIn("'ALTER TABLE public.' || quote_ident(tabela.tablename) || ' DISABLE ROW LEVEL SECURITY'", sql)

    def test_sqlite_e_no_op(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE exemplo (id INTEGER PRIMARY KEY)")
            with Operations.context(MigrationContext.configure(conn)):
                _migracao().upgrade()
                _migracao().downgrade()
            self.assertEqual(inspect(conn).get_table_names(), ["exemplo"])

    def test_e_a_nova_cabeca(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(_MIGRACAO.parents[1]))
        self.assertEqual(ScriptDirectory.from_config(cfg).get_heads(), ["b7d41e9c3a58"])


if __name__ == "__main__":
    unittest.main()
