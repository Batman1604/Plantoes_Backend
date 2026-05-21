import os
import unittest
from datetime import date, timedelta
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
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

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

    def criar_local(self, headers):
        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Recorrencia",
                "data_inicio": "2026-04-01",
                "data_final": "2026-08-31",
                "dia_pagamento": 10,
            },
        )
        return local_resp.json()["id"]

    def criar_plantoes_recorrentes(self, headers, local_id, recorrencia, inicio, total, passo_dias):
        ids = []
        data_cursor = inicio
        for _ in range(total):
            response = self.client.post(
                "/api/plantoes",
                headers=headers,
                json={
                    "valor": "250.00",
                    "data": data_cursor.isoformat(),
                    "horario_inicio": "08:00:00",
                    "horario_fim": "14:00:00",
                    "recorrencia": recorrencia,
                    "local_id": local_id,
                    "notificacao_ativa": True,
                    "notificacao_antecedencia_minutos": 60,
                },
            )
            ids.append(response.json()["data"]["id"])
            data_cursor += timedelta(days=passo_dias)
        return ids

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
                "cor": "#2196F3",
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
        self.assertEqual(body["data"]["cor"], "#2196F3")

    def test_patch_local_atualiza_cor(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Cor",
                "data_inicio": "2026-04-01",
                "data_final": "2026-12-31",
                "dia_pagamento": 10,
                "cor": "#FFEB3B",
            },
        )
        local_id = local_resp.json()["id"]

        response = self.client.patch(
            f"/api/locais/{local_id}",
            headers=headers,
            json={"cor": "#9C27B0"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["cor"], "#9C27B0")

    def test_patch_local_recalcula_pagamento_plantoes_quando_dia_pagamento_muda(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Recalculo",
                "data_inicio": "2026-01-01",
                "data_final": "2026-12-31",
                "dia_pagamento": 5,
            },
        )
        local_id = local_resp.json()["id"]

        plantao_resp = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "valor": "300.00",
                "data": "2026-03-15",
                "horario_inicio": "08:00:00",
                "horario_fim": "14:00:00",
                "local_id": local_id,
                "notificacao_ativa": True,
                "notificacao_antecedencia_minutos": 60,
            },
        )

        pagamento_anterior = plantao_resp.json()["data"]["pagamento_plantao"]
        self.assertEqual(pagamento_anterior, "2026-04-07")

        response = self.client.patch(
            f"/api/locais/{local_id}",
            headers=headers,
            json={"dia_pagamento": 10},
        )

        self.assertEqual(response.status_code, 200)
        plantoes = self.client.get("/api/plantoes", headers=headers).json()
        self.assertEqual(plantoes[0]["pagamento_plantao"], "2026-04-10")

    def test_patch_local_nao_recalcula_pagamento_sem_alterar_dia_pagamento(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Sem Recalculo",
                "data_inicio": "2026-01-01",
                "data_final": "2026-12-31",
                "dia_pagamento": 10,
            },
        )
        local_id = local_resp.json()["id"]

        plantao_resp = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "valor": "320.00",
                "data": "2026-03-20",
                "horario_inicio": "08:00:00",
                "horario_fim": "14:00:00",
                "local_id": local_id,
                "notificacao_ativa": True,
                "notificacao_antecedencia_minutos": 60,
            },
        )
        pagamento_original = plantao_resp.json()["data"]["pagamento_plantao"]

        response = self.client.patch(
            f"/api/locais/{local_id}",
            headers=headers,
            json={"nome_local": "Hospital Sem Recalculo 2"},
        )
        self.assertEqual(response.status_code, 200)

        plantoes = self.client.get("/api/plantoes", headers=headers).json()
        self.assertEqual(plantoes[0]["pagamento_plantao"], pagamento_original)

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

    def test_criacao_plantao_calcula_pagamento_com_dia_util_recorrente(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Recorrente",
                "data_inicio": "2026-04-21",
                "data_final": "2026-05-20",
                "dia_pagamento": 5,
            },
        )
        local_id = local_resp.json()["id"]

        plantao_resp = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "valor": "350.00",
                "data": "2026-04-25",
                "horario_inicio": "08:00:00",
                "horario_fim": "14:00:00",
                "local_id": local_id,
                "notificacao_ativa": True,
                "notificacao_antecedencia_minutos": 60,
            },
        )

        self.assertEqual(plantao_resp.status_code, 201)
        body = plantao_resp.json()
        self.assertEqual(body["data"]["pagamento_plantao"], "2026-06-05")
        self.assertFalse(body["data"]["pagamento_recebido"])

    def test_patch_pagamento_recebido_do_plantao(self):
        headers = self.criar_usuario_e_token()

        local_resp = self.client.post(
            "/api/locais",
            headers=headers,
            json={
                "nome_local": "Hospital Pagamento",
                "data_inicio": "2026-04-21",
                "data_final": "2026-05-20",
                "dia_pagamento": 5,
            },
        )
        local_id = local_resp.json()["id"]

        plantao_resp = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "valor": "500.00",
                "data": "2026-04-25",
                "horario_inicio": "07:00:00",
                "horario_fim": "13:00:00",
                "local_id": local_id,
                "notificacao_ativa": True,
                "notificacao_antecedencia_minutos": 60,
            },
        )
        plantao_id = plantao_resp.json()["data"]["id"]

        response = self.client.patch(
            f"/api/plantoes/{plantao_id}/pagamento-recebido",
            headers=headers,
            json={"pagamento_recebido": True},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["pagamento_recebido"])

    def test_patch_plantao_futuros_de_semanal_para_quinzenal_remove_intercalados(self):
        headers = self.criar_usuario_e_token()
        local_id = self.criar_local(headers)
        ids = self.criar_plantoes_recorrentes(headers, local_id, "Semanal", date(2026, 5, 1), 6, 7)

        response = self.client.patch(
            f"/api/plantoes/{ids[0]}",
            headers=headers,
            json={"aplicar_em": "este_e_futuros", "recorrencia": "Quinzenal"},
        )

        self.assertEqual(response.status_code, 200)
        plantoes = self.client.get("/api/plantoes", headers=headers).json()
        self.assertEqual(len(plantoes), 3)
        datas = sorted([item["data"] for item in plantoes])
        self.assertEqual(datas, ["2026-05-01", "2026-05-15", "2026-05-29"])
        self.assertTrue(all(item["recorrencia"] == "Quinzenal" for item in plantoes))

    def test_patch_plantao_futuros_de_quinzenal_para_semanal_cria_datas_intermediarias(self):
        headers = self.criar_usuario_e_token()
        self.client.post("/api/usuarios/upgrade", headers=headers)
        local_id = self.criar_local(headers)
        ids = self.criar_plantoes_recorrentes(headers, local_id, "Quinzenal", date(2026, 5, 1), 4, 14)

        response = self.client.patch(
            f"/api/plantoes/{ids[0]}",
            headers=headers,
            json={"aplicar_em": "este_e_futuros", "recorrencia": "Semanal"},
        )

        self.assertEqual(response.status_code, 200)
        plantoes = self.client.get("/api/plantoes", headers=headers).json()
        datas = sorted([item["data"] for item in plantoes])
        self.assertIn("2026-05-08", datas)
        self.assertIn("2026-05-22", datas)
        self.assertTrue(all(item["recorrencia"] == "Semanal" for item in plantoes))

    def test_patch_plantao_futuros_para_unico_remove_demais(self):
        headers = self.criar_usuario_e_token()
        local_id = self.criar_local(headers)
        ids = self.criar_plantoes_recorrentes(headers, local_id, "Semanal", date(2026, 5, 1), 4, 7)

        response = self.client.patch(
            f"/api/plantoes/{ids[0]}",
            headers=headers,
            json={"aplicar_em": "este_e_futuros", "recorrencia": "Unico"},
        )

        self.assertEqual(response.status_code, 200)
        plantoes = self.client.get("/api/plantoes", headers=headers).json()
        self.assertEqual(len(plantoes), 1)
        self.assertEqual(plantoes[0]["data"], "2026-05-01")
        self.assertIsNone(plantoes[0]["recorrencia"])


if __name__ == "__main__":
    unittest.main()
