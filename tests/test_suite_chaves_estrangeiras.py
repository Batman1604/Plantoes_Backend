"""Plano 5.11: a suíte roda com PRAGMA foreign_keys ligado (ver
tests/conftest.py), para que ondelete CASCADE/SET NULL aconteça como no
Postgres."""

import unittest

from sqlalchemy import text

from app.core.database import engine


class TestChavesEstrangeirasLigadas(unittest.TestCase):
    def test_pragma_foreign_keys_ligado(self):
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("PRAGMA foreign_keys")).scalar(), 1)


if __name__ == "__main__":
    unittest.main()
