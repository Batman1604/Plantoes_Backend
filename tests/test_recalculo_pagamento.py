"""AUD-024 / AUD-028: recálculo da data de pagamento.

- Editar o local recalcula com a regra da criação e não sobrescreve a data
  escolhida à mão.
- Mudar o ciclo do local também recalcula (a regra da criação depende dele).
- PATCH do plantão recalcula quando a data muda e o pedido não traz data de
  pagamento (o caso "só o valor mudou" está em tests/auditoria, AUD212).
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestRecalculoDePagamento(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        _, self.headers = criar_usuario_no_banco()
        self.local = self.client.post("/api/locais", headers=self.headers, json={
            "nome_local": "Hospital Pagamento", "data_inicio": "2026-01-01", "data_final": "2026-12-31",
            "dia_pagamento": 5}).json()["data"]

    def _plantao(self, data, **extra):
        r = self.client.post("/api/plantoes", headers=self.headers, json={
            "local_id": self.local["id"], "data_inicio": f"{data}T07:00:00", "data_fim": f"{data}T19:00:00", **extra})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    def _pagamento(self, plantao_id):
        plantoes = self.client.get("/api/plantoes", headers=self.headers).json()["data"]
        return next(p["pagamento_plantao"] for p in plantoes if p["id"] == plantao_id)

    def test_editar_dia_do_local_preserva_data_manual_e_recalcula_a_automatica(self):
        automatico = self._plantao("2026-03-15")
        manual = self._plantao("2026-03-16", pagamento_plantao="2026-09-30")
        self.assertEqual(automatico["pagamento_plantao"], "2026-04-07")

        r = self.client.patch(f"/api/locais/{self.local['id']}", headers=self.headers, json={"dia_pagamento": 10})
        self.assertEqual(r.status_code, 200, r.text)

        self.assertEqual(self._pagamento(automatico["id"]), "2026-04-14")
        self.assertEqual(self._pagamento(manual["id"]), "2026-09-30")

    def test_mudar_o_ciclo_do_local_recalcula(self):
        plantao = self._plantao("2026-03-15")
        self.assertEqual(plantao["pagamento_plantao"], "2026-04-07")
        # Ciclo de 10 a 9: o plantão de 15/03 fecha em 09/04 e é pago no 5º
        # dia útil depois disso (07/05) — mesma conta que um plantão novo faria.
        r = self.client.patch(f"/api/locais/{self.local['id']}", headers=self.headers,
                              json={"data_inicio": "2026-01-10", "data_final": "2026-02-09"})
        self.assertEqual(r.status_code, 200, r.text)
        novo = self._plantao("2026-03-15")
        self.assertEqual(self._pagamento(plantao["id"]), novo["pagamento_plantao"])
        self.assertNotEqual(novo["pagamento_plantao"], "2026-04-07", "premissa: o ciclo mudou a data")

    def test_patch_que_muda_a_data_recalcula(self):
        plantao = self._plantao("2026-03-15")
        r = self.client.patch(f"/api/plantoes/{plantao['id']}", headers=self.headers,
                              json={"data_inicio": "2026-05-15T07:00:00", "data_fim": "2026-05-15T19:00:00"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._pagamento(plantao["id"]), "2026-06-05")


if __name__ == "__main__":
    unittest.main()
