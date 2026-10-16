"""Bonificação apagada em cascata junto com o plantão (pendência do Backend-A).

A bonificação some do banco pela FK (bonificacoes.plantao_id ON DELETE
CASCADE) sem tombstone próprio de `bonificacao`. Conferido: isso basta, porque
todo caminho que apaga plantão (exclusão "somente este" ou "este e futuros",
edição em série, exclusão do local) grava o tombstone do PLANTÃO para o dono
— o único usuário que tem a bonificação no aparelho — e o cliente apaga as
filhas pela FK local (app_database.dart: Bonificacoes.plantaoId com
KeyAction.cascade e PRAGMA foreign_keys = ON). Este teste fixa esse contrato:
se um caminho deixar de gravar o tombstone do plantão, a bonificação fica
órfã no outro aparelho e ele falha.
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestBonificacaoEmCascata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _plantao_com_bonificacao(self):
        _, headers = criar_usuario_no_banco()
        local = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Cascata", "data_inicio": "2026-01-01", "data_final": "2026-01-31",
        }).json()["data"]
        plantao = self.client.post("/api/plantoes", headers=headers, json={
            "local_id": local["id"], "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00", "valor": "1200.00",
        }).json()["data"]
        bonificacao = self.client.post("/api/bonificacoes", headers=headers, json={
            "plantao_id": plantao["id"], "valor": "300.00", "descricao": "Extra",
        })
        self.assertEqual(bonificacao.status_code, 201, bonificacao.text)
        marco = self.client.get("/api/sync/pull", headers=headers).json()["data"]["servidor_timestamp"]
        return headers, local, plantao, marco

    def _pull_do_outro_aparelho(self, headers, marco):
        dados = self.client.get("/api/sync/pull", headers=headers, params={"since": marco}).json()["data"]
        excluidos = {(e["entidade"], e["id"]) for e in dados["exclusoes"]}
        return excluidos, dados["bonificacoes"]

    def test_excluir_o_plantao_manda_o_tombstone_do_plantao(self):
        headers, _, plantao, marco = self._plantao_com_bonificacao()
        r = self.client.delete(f"/api/plantoes/{plantao['id']}", headers=headers, params={"aplicar_em": "somente_este"})
        self.assertEqual(r.status_code, 200, r.text)

        excluidos, bonificacoes = self._pull_do_outro_aparelho(headers, marco)
        self.assertIn(("plantao", plantao["id"]), excluidos)
        self.assertEqual(bonificacoes, [])

    def test_excluir_o_local_manda_o_tombstone_do_plantao(self):
        headers, local, plantao, marco = self._plantao_com_bonificacao()
        r = self.client.delete(f"/api/locais/{local['id']}", headers=headers)
        self.assertEqual(r.status_code, 200, r.text)

        excluidos, bonificacoes = self._pull_do_outro_aparelho(headers, marco)
        self.assertIn(("plantao", plantao["id"]), excluidos)
        self.assertEqual(bonificacoes, [])


if __name__ == "__main__":
    unittest.main()
