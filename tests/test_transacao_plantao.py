"""A-14 (parcial): criar plantão + total do mês, e excluir uma série, numa
transação só. Uma falha no meio não pode deixar estado parcial."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.repositories.plantao_repository import FinanceiroRepository, PlantaoRepository
from tests.fixtures_usuario import criar_usuario_no_banco


class TestTransacaoPlantao(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        _, self.headers = criar_usuario_no_banco()
        self.local = self.client.post("/api/locais", headers=self.headers, json={
            "nome_local": "Hospital A-14", "data_inicio": "2026-01-01", "data_final": "2026-12-31"}).json()["data"]

    def _plantoes(self):
        return self.client.get("/api/plantoes", headers=self.headers).json()["data"]

    def test_falha_no_total_do_mes_nao_deixa_o_plantao_gravado(self):
        with patch.object(FinanceiroRepository, "sync_mes", side_effect=RuntimeError("falha simulada")):
            r = self.client.post("/api/plantoes", headers=self.headers, json={
                "local_id": self.local["id"], "data_inicio": "2026-03-10T07:00:00",
                "data_fim": "2026-03-10T19:00:00", "valor": "100.00"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self._plantoes(), [])

    def test_falha_no_meio_da_serie_nao_exclui_pela_metade(self):
        for dia in ("03", "10", "17"):
            r = self.client.post("/api/plantoes", headers=self.headers, json={
                "local_id": self.local["id"], "data_inicio": f"2026-03-{dia}T07:00:00",
                "data_fim": f"2026-03-{dia}T19:00:00", "recorrencia": "Semanal"})
            self.assertEqual(r.status_code, 201, r.text)
        primeiro = min(self._plantoes(), key=lambda p: p["data_inicio"])

        real = PlantaoRepository.delete_by_user
        chamadas = []

        def _falha_na_segunda(db, plantao_id, usuario_id, **kwargs):
            chamadas.append(plantao_id)
            if len(chamadas) == 2:
                raise RuntimeError("falha simulada")
            return real(db, plantao_id, usuario_id, **kwargs)

        with patch.object(PlantaoRepository, "delete_by_user", side_effect=_falha_na_segunda):
            r = self.client.delete(f"/api/plantoes/{primeiro['id']}", headers=self.headers,
                                   params={"aplicar_em": "este_e_futuros"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(len(self._plantoes()), 3)


if __name__ == "__main__":
    unittest.main()
