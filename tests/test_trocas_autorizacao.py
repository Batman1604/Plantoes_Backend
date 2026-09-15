"""Autorização de exclusão de troca (auditoria C-08).

DELETE /api/trocas/{id} autorizava pelo dono do LOCAL referenciado na troca, e
não pelo dono da troca. Os dois divergem sempre que um plantão é recebido por
transferência: o clone criado para quem recebe mantém o local_id de quem
enviou, então uma troca registrada por B pode apontar para um local de A.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import SyncExclusao, Troca


class TestTrocasAutorizacao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def criar_usuario(self):
        sufixo = uuid4().hex[:8]
        resposta = self.client.post("/api/usuarios", json={
            "nome": "Usuário Troca",
            "email": f"troca-{sufixo}@example.com",
            "senha": "SenhaForte123",
        })
        self.assertEqual(resposta.status_code, 201, resposta.text)
        dados = resposta.json()["data"]
        return (
            dados["usuario"]["id"],
            {"Authorization": f"Bearer {dados['access_token']}"},
        )

    def criar_local_e_plantao(self, headers):
        local = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Teste",
            "data_inicio": "2026-01-01",
            "data_final": "2026-01-31",
        }).json()["data"]
        plantao = self.client.post("/api/plantoes", headers=headers, json={
            "local_id": local["id"],
            "data_inicio": "2026-07-17", "horario_inicio": "07:00",
            "data_fim": "2026-07-17", "horario_fim": "19:00",
            "valor": "1200.00",
        }).json()["data"]
        return local, plantao

    def _troca_de_b_apontando_para_local_de_a(self):
        """Monta exatamente a situação que o produto cria numa transferência:
        troca pertencente a B, mas com local_id de um local de A."""
        id_a, headers_a = self.criar_usuario()
        id_b, headers_b = self.criar_usuario()

        local_a, _ = self.criar_local_e_plantao(headers_a)
        _, plantao_b = self.criar_local_e_plantao(headers_b)

        troca_id = self.client.post("/api/trocas", headers=headers_b, json={
            "plantao_transferido_id": plantao_b["id"],
            "transferido_para": {"nome": "Ana", "telefone": "+5511988887777"},
        }).json()["data"]["troca_id"]

        # Reproduz o local_id "herdado" de quem enviou, que é o que o fluxo de
        # transferência realmente grava.
        with SessionLocal() as db:
            troca = db.scalar(select(Troca).where(Troca.id == troca_id))
            troca.local_id = local_a["id"]
            db.commit()

        return headers_a, headers_b, id_b, troca_id

    def test_dono_do_local_alheio_nao_apaga_troca_de_outro_usuario(self):
        headers_a, _, id_b, troca_id = self._troca_de_b_apontando_para_local_de_a()

        resposta = self.client.delete(f"/api/trocas/{troca_id}", headers=headers_a)

        self.assertEqual(resposta.status_code, 403, resposta.text)

        with SessionLocal() as db:
            self.assertIsNotNone(
                db.scalar(select(Troca).where(Troca.id == troca_id)),
                "a troca de B não pode ser apagada por A",
            )
            # O tombstone é gravado contra troca.usuario_id, então uma exclusão
            # indevida também removeria o registro do dispositivo de B.
            self.assertIsNone(
                db.scalar(
                    select(SyncExclusao).where(
                        SyncExclusao.usuario_id == id_b,
                        SyncExclusao.entidade == "troca",
                        SyncExclusao.entidade_id == troca_id,
                    )
                ),
                "nenhum tombstone deve ser gravado para B",
            )

    def test_dono_da_troca_consegue_apagar_a_propria(self):
        _, headers_b, id_b, troca_id = self._troca_de_b_apontando_para_local_de_a()

        resposta = self.client.delete(f"/api/trocas/{troca_id}", headers=headers_b)

        self.assertEqual(resposta.status_code, 200, resposta.text)

        with SessionLocal() as db:
            self.assertIsNone(db.scalar(select(Troca).where(Troca.id == troca_id)))
            self.assertIsNotNone(
                db.scalar(
                    select(SyncExclusao).where(
                        SyncExclusao.usuario_id == id_b,
                        SyncExclusao.entidade == "troca",
                        SyncExclusao.entidade_id == troca_id,
                    )
                ),
                "o tombstone precisa existir para o dispositivo de B convergir",
            )

    def test_troca_inexistente_retorna_404(self):
        _, headers = self.criar_usuario()

        resposta = self.client.delete("/api/trocas/99999999", headers=headers)

        self.assertEqual(resposta.status_code, 404)


if __name__ == "__main__":
    unittest.main()
