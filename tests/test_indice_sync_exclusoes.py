"""A-05: foi_excluido consulta (usuario_id, entidade, uuid_cliente) a cada item
novo do push; o índice composto precisa existir no modelo (e na migração
969110c7e106)."""

import unittest

from sqlalchemy import inspect

from app.core.database import engine


class TestIndiceSyncExclusoes(unittest.TestCase):
    def test_indice_composto_existe(self):
        indices = {ix["name"]: ix["column_names"] for ix in inspect(engine).get_indexes("sync_exclusoes")}
        self.assertEqual(
            indices.get("ix_sync_exclusoes_usuario_entidade_uuid"),
            ["usuario_id", "entidade", "uuid_cliente"],
        )


if __name__ == "__main__":
    unittest.main()
