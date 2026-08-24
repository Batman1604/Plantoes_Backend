import os
import unittest
from pathlib import Path
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_planos.db"

from fastapi.testclient import TestClient

from app.main import app


class TestPlanos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_planos.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def criar_usuario_e_token(self):
        email = f"plan-user-{uuid4()}@example.com"
        self.client.post(
            "/api/usuarios",
            json={
                "nome": "Usuário Plano",
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
        return login, {"Authorization": f"Bearer {token}"}

    def criar_local(self, headers):
        response = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Premium",
                "data_inicio": "2026-01-01",
                "data_final": "2026-12-31",
                "dia_pagamento": 10,
                "executado": False,
            },
        )
        return response.json()["id"]

    def test_login_retorna_plano_free_por_padrao(self):
        login, _ = self.criar_usuario_e_token()

        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["data"]["usuario"]["plano"], "free")

    def test_financeiro_bloqueado_para_free_e_upgrade_libera(self):
        _, headers = self.criar_usuario_e_token()

        bloqueado = self.client.get("/api/financeiro/anual?ano=2026", headers=headers)
        self.assertEqual(bloqueado.status_code, 403)
        self.assertEqual(
            bloqueado.json(),
            {
                "success": False,
                "message": "Funcionalidade exclusiva para usuários premium",
            },
        )

        upgrade = self.client.post("/api/usuarios/upgrade", headers=headers)
        self.assertEqual(upgrade.status_code, 200)
        self.assertEqual(upgrade.json()["data"]["plano"], "premium")

        upgrade_idempotente = self.client.post("/api/usuarios/upgrade", headers=headers)
        self.assertEqual(upgrade_idempotente.status_code, 200)
        self.assertEqual(upgrade_idempotente.json()["message"], "Usuário já possui plano premium")
        self.assertEqual(upgrade_idempotente.json()["data"]["plano"], "premium")

        liberado = self.client.get("/api/financeiro/anual?ano=2026", headers=headers)
        self.assertEqual(liberado.status_code, 200)
        self.assertTrue(liberado.json()["success"])

    def test_limite_de_10_plantoes_para_plano_free(self):
        _, headers = self.criar_usuario_e_token()
        local_id = self.criar_local(headers)

        for i in range(10):
            resposta = self.client.post(
                "/api/plantoes",
                headers=headers,
                json={
                    "valor": "300.00",
                    "data": f"2026-04-{i + 1:02d}",
                    "horario_inicio": "08:00:00",
                    "horario_fim": "12:00:00",
                    "local_id": local_id,
                    "notificacao_ativa": True,
                    "notificacao_antecedencia_minutos": 60,
                },
            )
            self.assertEqual(resposta.status_code, 201)

        limite = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "valor": "300.00",
                "data": "2026-04-20",
                "horario_inicio": "13:00:00",
                "horario_fim": "18:00:00",
                "local_id": local_id,
                "notificacao_ativa": True,
                "notificacao_antecedencia_minutos": 60,
            },
        )

        self.assertEqual(limite.status_code, 403)
        self.assertEqual(
            limite.json(),
            {
                "success": False,
                "message": "Limite do plano gratuito atingido (10 plantões)",
            },
        )


if __name__ == "__main__":
    unittest.main()
