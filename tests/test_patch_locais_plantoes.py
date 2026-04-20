import os
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_patch_locais_plantoes.db"

from fastapi.testclient import TestClient

from app.main import app


class TestPatchLocaisEPlantoes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_patch_locais_plantoes.db")
        if db_file.exists():
            db_file.unlink()

    def criar_usuario_e_token(self):
        email = f"patch-user-{uuid4()}@example.com"
        self.client.post(
            "/api/usuarios",
            json={
                "nome": "Usuário Patch",
                "email": email,
                "telefone": "11999999999",
                "senha": "SenhaForte123",
            },
        )
        login = self.client.post(
            "/api/auth/login",
            json={"email": email, "senha": "SenhaForte123"},
        )
        token = login.json()["data"]["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_patch_local_atualiza_campos(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital A",
                "data_inicio": "2026-04-01",
                "data_final": "2026-12-31",
                "dia_pagamento": 10,
                "executado": False,
            },
        )
        local_id = local_resp.json()["id"]

        response = self.client.patch(
            f"/api/locais/{local_id}",
            headers=headers,
            json={"nome_local": "Hospital B"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["nome_local"], "Hospital B")

    def test_patch_plantao_atualiza_campos(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "UPA Centro",
                "data_inicio": "2026-04-01",
                "data_final": "2026-12-31",
                "dia_pagamento": 15,
                "executado": False,
            },
        )
        local_id = local_resp.json()["id"]

        plantao_resp = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "valor": "350.00",
                "data": "2026-04-20",
                "horario_inicio": "08:00:00",
                "horario_fim": "14:00:00",
                "recorrencia": "Semanal",
                "local_id": local_id,
                "notificacao_ativa": True,
                "notificacao_antecedencia_minutos": 60,
            },
        )
        plantao_id = plantao_resp.json()["data"]["id"]

        response = self.client.patch(
            f"/api/plantoes/{plantao_id}",
            headers=headers,
            json={
                "valor": "400.00",
                "horario_fim": "16:00:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["valor"], "400.00")
        self.assertEqual(body["data"]["horario_fim"], "16:00:00")


if __name__ == "__main__":
    unittest.main()
