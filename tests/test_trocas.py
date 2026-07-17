import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///./test_trocas.db"

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.main import app


class TestTrocas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_trocas.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def criar_usuario(self, email):
        response = self.client.post("/api/usuarios", json={
            "nome": "Usuário Teste", "email": email, "senha": "SenhaForte123",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

    def criar_local_e_plantao(self, headers):
        local = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Teste", "data_inicio": "2026-01-01", "data_final": "2026-01-31",
        }).json()["data"]
        plantao = self.client.post("/api/plantoes", headers=headers, json={
            "local_id": local["id"], "data_inicio": "2026-07-17", "horario_inicio": "07:00",
            "data_fim": "2026-07-17", "horario_fim": "19:00", "valor": "1200.00",
        }).json()["data"]
        return local, plantao

    def transferencia_payload(self, plantao_id, recebido=None):
        payload = {
            "plantao_transferido_id": plantao_id,
            "transferido_para": {"nome": "Maria", "telefone": "+5511999999999", "observacao": "Cobertura"},
        }
        if recebido:
            payload["plantao_recebido"] = recebido
        return payload

    def test_transferencia_isolada_preserva_historico(self):
        headers = self.criar_usuario("isolada@example.com")
        _, plantao = self.criar_local_e_plantao(headers)
        response = self.client.post("/api/trocas", headers=headers, json=self.transferencia_payload(plantao["id"]))
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()["data"]
        self.assertIsNone(data["plantao_recebido"])
        self.assertEqual(data["plantao_transferido"]["tipo_troca"], "transferido")
        self.assertEqual(data["plantao_transferido"]["troca"]["contraparte_nome"], "Maria")
        plantoes = self.client.get("/api/plantoes", headers=headers).json()["data"]
        self.assertEqual(len(plantoes), 1)
        self.assertTrue(plantoes[0]["transferido"])

    def test_troca_completa_cria_plantao_recebido(self):
        headers = self.criar_usuario("completa@example.com")
        local, plantao = self.criar_local_e_plantao(headers)
        recebido = {
            "local_id": local["id"], "data_inicio": "2026-07-20", "horario_inicio": "07:00",
            "data_fim": "2026-07-20", "horario_fim": "19:00", "valor": 800,
            "sobreaviso": False, "recebido_de": {"nome": "João", "telefone": "11999999999"},
        }
        response = self.client.post("/api/trocas", headers=headers, json=self.transferencia_payload(plantao["id"], recebido))
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()["data"]
        self.assertEqual(data["plantao_recebido"]["tipo_troca"], "recebido")
        self.assertTrue(data["plantao_recebido"]["recebido_via_transferencia"])
        self.assertEqual(data["plantao_transferido"]["troca"]["plantao_relacionado_id"], data["plantao_recebido"]["id"])

    def test_acesso_indevido_e_validacao_nao_alteram_plantao(self):
        dono = self.criar_usuario("dono@example.com")
        _, plantao = self.criar_local_e_plantao(dono)
        outro = self.criar_usuario("outro@example.com")
        proibido = self.client.post("/api/trocas", headers=outro, json=self.transferencia_payload(plantao["id"]))
        self.assertEqual(proibido.status_code, 403)
        invalido = self.client.post("/api/trocas", headers=dono, json={"plantao_transferido_id": plantao["id"], "transferido_para": {"nome": "", "telefone": ""}})
        self.assertEqual(invalido.status_code, 422)
        estado = self.client.get("/api/plantoes", headers=dono).json()["data"]
        self.assertFalse(estado[0]["transferido"])

    def test_rollback_transacional_quando_persistencia_falha(self):
        headers = self.criar_usuario("rollback@example.com")
        local, plantao = self.criar_local_e_plantao(headers)
        recebido = {
            "local_id": local["id"], "data_inicio": "2026-07-20", "horario_inicio": "07:00",
            "data_fim": "2026-07-20", "horario_fim": "19:00", "valor": 0,
            "recebido_de": {"nome": "João", "telefone": "11999999999"},
        }
        with patch("app.routes.plantao_routes.Session.commit", side_effect=SQLAlchemyError("falha simulada")):
            response = self.client.post("/api/trocas", headers=headers, json=self.transferencia_payload(plantao["id"], recebido))
        self.assertEqual(response.status_code, 500)
        estado = self.client.get("/api/plantoes", headers=headers).json()["data"]
        self.assertEqual(len(estado), 1)
        self.assertFalse(estado[0]["transferido"])


if __name__ == "__main__":
    unittest.main()
