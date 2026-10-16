"""Número de consultas nas rotas de listagem (auditoria A-03).

GET /api/plantoes e GET /api/sync/pull montavam a resposta chamando
_plantao_com_troca em laço, o que fazia UMA consulta de troca por plantão.
Medido antes da correção: 205 consultas para 200 plantões na listagem, e o
mesmo padrão no pull — que o cliente chama de forma recorrente.

Estes testes travam a propriedade que importa: o número de consultas não cresce
com a quantidade de plantões. Um laço reintroduzido por engano falha aqui em vez
de virar lentidão silenciosa em produção.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import engine
from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class ContadorDeConsultas:
    def __init__(self):
        self.total = 0

    def __enter__(self):
        self.total = 0
        event.listen(engine, "before_cursor_execute", self._contar)
        return self

    def __exit__(self, *_):
        event.remove(engine, "before_cursor_execute", self._contar)

    def _contar(self, conn, cursor, statement, parameters, context, executemany):
        self.total += 1


class TestConsultasListagem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def autenticar(self):
        sufixo = uuid4().hex[:8]
        # AUD-011: POST /api/usuarios foi removido; o usuário é gravado direto no banco.
        return criar_usuario_no_banco(
            f"consultas-{sufixo}@example.com", "Usuário Consultas",
            telefone=f"119{uuid4().int % 100000000:08d}",
        )[1]

    def semear(self, headers, quantidade):
        local_id = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Consultas",
            "data_inicio": "2026-01-01",
            "data_final": "2026-12-31",
            "dia_pagamento": 5,
        }).json()["data"]["id"]

        for i in range(quantidade):
            dia = (i % 28) + 1
            mes = (i // 28) % 12 + 1
            r = self.client.post("/api/plantoes", headers=headers, json={
                "valor": "300.00",
                "data_inicio": f"2026-{mes:02d}-{dia:02d}T08:00:00",
                "data_fim": f"2026-{mes:02d}-{dia:02d}T14:00:00",
                "local_id": local_id,
            })
            self.assertEqual(r.status_code, 201, r.text)

    def consultas_de(self, caminho, headers):
        with ContadorDeConsultas() as contador:
            resposta = self.client.get(caminho, headers=headers)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return contador.total

    def test_listar_plantoes_nao_cresce_com_a_quantidade(self):
        poucos = self.autenticar()
        self.semear(poucos, 3)
        com_3 = self.consultas_de("/api/plantoes", poucos)

        muitos = self.autenticar()
        self.semear(muitos, 30)
        com_30 = self.consultas_de("/api/plantoes", muitos)

        self.assertEqual(
            com_3,
            com_30,
            f"o número de consultas deve ser constante: 3 plantões={com_3}, "
            f"30 plantões={com_30}. Se cresceu, voltou a haver uma consulta por plantão.",
        )

    def test_sync_pull_nao_cresce_com_a_quantidade(self):
        poucos = self.autenticar()
        self.semear(poucos, 3)
        com_3 = self.consultas_de("/api/sync/pull", poucos)

        muitos = self.autenticar()
        self.semear(muitos, 30)
        com_30 = self.consultas_de("/api/sync/pull", muitos)

        self.assertEqual(com_3, com_30, f"3 plantões={com_3}, 30 plantões={com_30}")

    def test_a_troca_vinculada_continua_aparecendo_na_listagem(self):
        # A correção resolve as trocas em lote; se o cruzamento em memória
        # errar, o vínculo some da resposta sem nenhum erro aparecer.
        headers = self.autenticar()
        self.semear(headers, 2)
        plantoes = self.client.get("/api/plantoes", headers=headers).json()["data"]

        troca = self.client.post("/api/trocas", headers=headers, json={
            "plantao_transferido_id": plantoes[0]["id"],
            "transferido_para": {"nome": "Ana", "telefone": "+5511988887777"},
        })
        self.assertEqual(troca.status_code, 201, troca.text)

        depois = self.client.get("/api/plantoes", headers=headers).json()["data"]
        por_id = {p["id"]: p for p in depois}

        transferido = por_id[plantoes[0]["id"]]
        self.assertEqual(transferido["tipo_troca"], "transferido")
        self.assertEqual(transferido["troca"]["contraparte_nome"], "Ana")

        # O outro plantão não pode ter herdado a troca do vizinho.
        self.assertIsNone(por_id[plantoes[1]["id"]].get("troca"))


if __name__ == "__main__":
    unittest.main()
