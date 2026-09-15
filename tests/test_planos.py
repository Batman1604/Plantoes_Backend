"""Plano do usuário.

A v1 é publicada sem restrição por plano — ver
PlanoService.RESTRICOES_POR_PLANO_ATIVAS e a auditoria C-07. Estes testes fixam
os dois lados disso: a coluna `plano` continua existindo e vindo no login, e
nenhum recurso é bloqueado enquanto o interruptor estiver desligado.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.plano_service import PlanoService


class TestPlanos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def criar_usuario_e_token(self):
        # Email E telefone precisam ser únicos por usuário: o cadastro recusa
        # telefone repetido (409). A versão anterior destes testes usava o
        # mesmo número fixo em todos, então só o primeiro a rodar conseguia
        # se cadastrar e os demais falhavam no login por falta de token.
        sufixo = uuid4().hex[:8]
        email = f"plan-user-{sufixo}@example.com"
        telefone = f"119{uuid4().int % 100000000:08d}"
        criacao = self.client.post(
            "/api/usuarios",
            json={
                "nome": "Usuário Plano",
                "email": email,
                "telefone": telefone,
                "senha": "SenhaForte123",
            },
        )
        self.assertEqual(criacao.status_code, 201, criacao.text)

        login = self.client.post(
            "/api/auth/login",
            json={"email": email, "senha": "SenhaForte123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
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
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["id"]

    def test_login_retorna_plano_free_por_padrao(self):
        login, _ = self.criar_usuario_e_token()

        self.assertEqual(login.json()["data"]["usuario"]["plano"], "free")

    def test_relatorio_financeiro_liberado_sem_premium(self):
        _, headers = self.criar_usuario_e_token()

        resposta = self.client.get("/api/financeiro/anual?ano=2026", headers=headers)

        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertTrue(resposta.json()["success"])

    def test_sem_limite_de_plantoes_para_plano_free(self):
        _, headers = self.criar_usuario_e_token()
        local_id = self.criar_local(headers)

        # Onze plantões: um a mais que o antigo limite do plano gratuito.
        for i in range(PlanoService.LIMITE_PLANTOES_FREE + 1):
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
            self.assertEqual(resposta.status_code, 201, f"plantão {i + 1}: {resposta.text}")

    def test_interruptor_de_plano_esta_desligado_na_v1(self):
        # Guarda explícita: se alguém religar as restrições sem implementar a
        # compra no aplicativo, o beco sem saída da auditoria C-07 volta.
        self.assertFalse(PlanoService.RESTRICOES_POR_PLANO_ATIVAS)


if __name__ == "__main__":
    unittest.main()
