"""K8 (AUD-053): serie_id gravado, devolvido e usado para "este e futuros"."""

import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


def _agora(delta=timedelta(0)) -> str:
    return (datetime.now(UTC) + delta).isoformat()


class TestSerieId(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        _, self.headers = criar_usuario_no_banco()
        self.local = self.client.post("/api/locais", headers=self.headers, json={
            "nome_local": "Hospital Serie", "data_inicio": "2026-01-01", "data_final": "2026-12-31",
        }).json()["data"]

    def _item(self, dia, serie_id=None, inicio="07:00", fim="19:00", **extra):
        item = {
            "uuid_cliente": str(uuid4()), "local_id": self.local["id"], "valor": "100.00",
            "data_inicio": f"{dia}T{inicio}:00", "data_fim": f"{dia}T{fim}:00",
            "recorrencia": "Semanal", "atualizado_em": _agora(),
        }
        if serie_id is not None:
            item["serie_id"] = serie_id
        item.update(extra)
        return item

    def _push(self, *itens):
        r = self.client.post("/api/sync/push", headers=self.headers, json={"plantoes": list(itens)})
        self.assertEqual(r.status_code, 200, r.text)
        dados = r.json()["data"]
        self.assertEqual(dados["erros"], [])
        return dados

    def _plantoes(self):
        return {p["uuid_cliente"]: p for p in self.client.get("/api/plantoes", headers=self.headers).json()["data"]}

    def test_push_grava_e_pull_devolve_serie_id(self):
        item = self._item("2026-07-01", serie_id="serie-123")
        self._push(item)
        pull = self.client.get("/api/sync/pull", headers=self.headers).json()["data"]
        self.assertEqual([p["serie_id"] for p in pull["plantoes"]], ["serie-123"])

    def test_este_e_futuros_segue_o_serie_id_e_nao_a_heuristica(self):
        a1 = self._item("2026-07-01", serie_id="A")
        a2 = self._item("2026-07-08", serie_id="A", inicio="08:00", fim="20:00")  # horário mudado à mão
        b1 = self._item("2026-07-15", serie_id="B")  # mesma "cara" de a1, outra série
        self._push(a1, a2, b1)
        ids = {u: p["id"] for u, p in self._plantoes().items()}

        r = self.client.patch(f"/api/plantoes/{ids[a1['uuid_cliente']]}", headers=self.headers,
                              json={"aplicar_em": "este_e_futuros", "valor": "500.00"})
        self.assertEqual(r.status_code, 200, r.text)
        valores = {u: float(p["valor"]) for u, p in self._plantoes().items()}
        self.assertEqual(valores[a1["uuid_cliente"]], 500.0)
        self.assertEqual(valores[a2["uuid_cliente"]], 500.0)
        self.assertEqual(valores[b1["uuid_cliente"]], 100.0)

    def test_sem_serie_id_continua_a_heuristica(self):
        p1, p2 = self._item("2026-07-01"), self._item("2026-07-08")
        self._push(p1, p2)
        ids = {u: p["id"] for u, p in self._plantoes().items()}
        r = self.client.patch(f"/api/plantoes/{ids[p1['uuid_cliente']]}", headers=self.headers,
                              json={"aplicar_em": "este_e_futuros", "valor": "300.00"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({float(p["valor"]) for p in self._plantoes().values()}, {300.0})

    def test_cliente_antigo_nao_apaga_o_serie_id(self):
        item = self._item("2026-07-01", serie_id="serie-mantida")
        self._push(item)
        antigo = {k: v for k, v in item.items() if k != "serie_id"}
        antigo.update(valor="250.00", atualizado_em=_agora(timedelta(minutes=1)))
        self._push(antigo)
        plantao = self._plantoes()[item["uuid_cliente"]]
        self.assertEqual(float(plantao["valor"]), 250.0)
        self.assertEqual(plantao["serie_id"], "serie-mantida")


if __name__ == "__main__":
    unittest.main()
