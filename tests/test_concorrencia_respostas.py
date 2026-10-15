"""A-08: respostas concorrentes a amizade e a solicitação de troca.

O aceite concorrente de solicitação de troca está em
tests/auditoria/test_a08_aceite_concorrente.py. Aqui: dois aceites simultâneos
da mesma amizade (o segundo batia no UNIQUE das configurações e virava 500) e
a resposta feita sobre um objeto já desatualizado (outra requisição respondeu
entre a leitura e a escrita).
"""

import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.main import app
from app.models import AmigoConfiguracao, Amizade, SolicitacaoTroca
from app.repositories.plantao_repository import AmizadeRepository, SolicitacaoTrocaRepository
from tests.fixtures_usuario import criar_usuario_no_banco


class TestAmizadeConcorrente(unittest.TestCase):
    def test_dois_aceites_simultaneos_dao_200_e_409_e_um_par_de_configuracoes(self):
        client = TestClient(app)
        id_a, h_a = criar_usuario_no_banco()
        id_b, h_b = criar_usuario_no_banco()
        amizade_id = client.post("/api/amizades", headers=h_a, json={"destinatario_usuario_id": id_b}).json()["data"]["id"]

        barreira = threading.Barrier(2, timeout=10)
        aceitar_real = AmizadeRepository.aceitar
        status = []

        def _aceitar_depois_da_leitura(db, amizade):
            barreira.wait()
            return aceitar_real(db, amizade)

        def _aceitar():
            c = TestClient(app, raise_server_exceptions=False)
            status.append(c.patch(f"/api/amizades/{amizade_id}/aceitar", headers=h_b).status_code)

        with patch("app.routes.plantao_routes.AmizadeRepository.aceitar", side_effect=_aceitar_depois_da_leitura):
            threads = [threading.Thread(target=_aceitar) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

        self.assertEqual(sorted(status), [200, 409])
        with SessionLocal() as db:
            configs = db.scalar(select(func.count()).select_from(AmigoConfiguracao).where(
                AmigoConfiguracao.usuario_id.in_([id_a, id_b])))
        self.assertEqual(configs, 2)

    def test_rejeitar_com_objeto_desatualizado_nao_apaga_amizade_ja_aceita(self):
        client = TestClient(app)
        _, h_a = criar_usuario_no_banco()
        id_b, h_b = criar_usuario_no_banco()
        amizade_id = client.post("/api/amizades", headers=h_a, json={"destinatario_usuario_id": id_b}).json()["data"]["id"]

        with SessionLocal() as db:
            desatualizada = AmizadeRepository.get_by_id(db, amizade_id)  # lida como "pendente"
            self.assertEqual(client.patch(f"/api/amizades/{amizade_id}/aceitar", headers=h_b).status_code, 200)
            self.assertFalse(AmizadeRepository.rejeitar(db, desatualizada))
        with SessionLocal() as db:
            self.assertEqual(db.get(Amizade, amizade_id).status, "aceito")


class TestSolicitacaoDesatualizada(unittest.TestCase):
    def test_rejeitar_depois_de_aceita_nao_sobrescreve_o_status(self):
        client = TestClient(app)
        id_a, h_a = criar_usuario_no_banco()
        id_b, h_b = criar_usuario_no_banco()
        amizade_id = client.post("/api/amizades", headers=h_a, json={"destinatario_usuario_id": id_b}).json()["data"]["id"]
        client.patch(f"/api/amizades/{amizade_id}/aceitar", headers=h_b)
        local = client.post("/api/locais", headers=h_a, json={
            "nome_local": "Hospital", "data_inicio": "2026-01-01", "data_final": "2026-01-31"}).json()["data"]
        plantao = client.post("/api/plantoes", headers=h_a, json={
            "local_id": local["id"], "data_inicio": "2026-01-10T07:00:00", "data_fim": "2026-01-10T19:00:00"}).json()["data"]
        sid = client.post("/api/solicitacoes-troca", headers=h_a, json={
            "destinatario_usuario_id": id_b, "tipo": "transferir", "plantao_transferido_id": plantao["id"],
        }).json()["data"]["id"]

        with SessionLocal() as db:
            desatualizada = SolicitacaoTrocaRepository.get_by_id(db, sid)
            self.assertEqual(client.patch(f"/api/solicitacoes-troca/{sid}/aceitar", headers=h_b).status_code, 200)
            self.assertIsNone(SolicitacaoTrocaRepository.marcar_respondida(db, desatualizada, "rejeitada"))
        with SessionLocal() as db:
            self.assertEqual(db.get(SolicitacaoTroca, sid).status, "aceita")


if __name__ == "__main__":
    unittest.main()
