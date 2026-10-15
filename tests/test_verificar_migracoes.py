"""AUD-044: em produção, verificar_migracoes falha alto quando a própria
verificação lança (antes: aviso no log e o servidor subia)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app.main as main_mod


class TestVerificarMigracoes(unittest.TestCase):
    def _rodar(self, environment):
        engine_pg = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        with patch.object(main_mod, "engine", engine_pg), \
             patch.object(main_mod.settings, "environment", environment), \
             patch("alembic.script.ScriptDirectory.from_config", side_effect=OSError("alembic.ini ausente")):
            main_mod.verificar_migracoes()

    def test_producao_recusa_subir_se_nao_consegue_verificar(self):
        with self.assertRaises(RuntimeError):
            self._rodar("production")

    def test_fora_de_producao_so_avisa(self):
        with self.assertLogs("app.main", level="WARNING"):
            self._rodar("development")


if __name__ == "__main__":
    unittest.main()
