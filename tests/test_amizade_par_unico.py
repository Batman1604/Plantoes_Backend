"""Amizade única por par não ordenado (pendência do A-08).

A->B e B->A ao mesmo tempo passavam os dois pela checagem da rota
(get_relacao_existente) e gravavam duas linhas para o mesmo par. Agora o
índice único uq_amizades_par recusa a segunda, e a rota responde 409.
"""

import importlib.util
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.main import app
from app.models import Amizade
from app.repositories.plantao_repository import AmizadeRepository
from tests.fixtures_usuario import criar_usuario_no_banco

_MIGRACAO = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "4e1b7c9a2d36_amizades_par_unico.py"


def _linhas_do_par(a: int, b: int) -> int:
    with SessionLocal() as db:
        return db.scalar(
            select(func.count()).select_from(Amizade).where(
                or_(
                    (Amizade.solicitante_usuario_id == a) & (Amizade.destinatario_usuario_id == b),
                    (Amizade.solicitante_usuario_id == b) & (Amizade.destinatario_usuario_id == a),
                )
            )
        )


class TestAmizadeParUnico(unittest.TestCase):
    def test_pedidos_simultaneos_nas_duas_direcoes_dao_201_e_409_e_uma_linha(self):
        id_a, h_a = criar_usuario_no_banco()
        id_b, h_b = criar_usuario_no_banco()

        # Os dois leem "não há relação" antes de qualquer um gravar.
        barreira = threading.Barrier(2, timeout=10)
        checagem_real = AmizadeRepository.get_relacao_existente

        def _checar_e_esperar(db, x, y):
            resultado = checagem_real(db, x, y)
            barreira.wait()
            return resultado

        status = []

        def _pedir(headers, destinatario):
            c = TestClient(app, raise_server_exceptions=False)
            status.append(c.post("/api/amizades", headers=headers, json={"destinatario_usuario_id": destinatario}).status_code)

        with patch("app.routes.plantao_routes.AmizadeRepository.get_relacao_existente", side_effect=_checar_e_esperar):
            threads = [
                threading.Thread(target=_pedir, args=(h_a, id_b)),
                threading.Thread(target=_pedir, args=(h_b, id_a)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

        self.assertEqual(sorted(status), [201, 409])
        self.assertEqual(_linhas_do_par(id_a, id_b), 1)

    def test_o_banco_recusa_o_par_invertido(self):
        id_a, _ = criar_usuario_no_banco()
        id_b, _ = criar_usuario_no_banco()
        with SessionLocal() as db:
            db.add(Amizade(solicitante_usuario_id=id_a, destinatario_usuario_id=id_b, status="pendente"))
            db.commit()
            db.add(Amizade(solicitante_usuario_id=id_b, destinatario_usuario_id=id_a, status="pendente"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()


class TestMigracaoDoParUnico(unittest.TestCase):
    """A migração não apaga dado: com duplicata, falha alto e lista os ids."""

    def _migracao(self):
        spec = importlib.util.spec_from_file_location("migracao_par_unico", _MIGRACAO)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def _rodar_upgrade(self, linhas):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE amizades (id INTEGER PRIMARY KEY, solicitante_usuario_id INTEGER, destinatario_usuario_id INTEGER)"
            ))
            for linha in linhas:
                conn.execute(text("INSERT INTO amizades VALUES (:id, :s, :d)"), dict(zip(("id", "s", "d"), linha)))
            with Operations.context(MigrationContext.configure(conn)):
                self._migracao().upgrade()
            return conn.execute(text("SELECT COUNT(*) FROM amizades")).scalar()

    def test_com_duplicata_falha_e_lista_os_ids(self):
        with self.assertRaises(RuntimeError) as erro:
            self._rodar_upgrade([(1, 10, 20), (2, 20, 10), (3, 10, 30)])
        self.assertIn("id=1", str(erro.exception))
        self.assertIn("id=2", str(erro.exception))
        self.assertNotIn("id=3", str(erro.exception))

    def test_sem_duplicata_cria_o_indice_e_mantem_as_linhas(self):
        self.assertEqual(self._rodar_upgrade([(1, 10, 20), (2, 10, 30)]), 2)


if __name__ == "__main__":
    unittest.main()
