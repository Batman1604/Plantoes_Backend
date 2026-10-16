"""Pendências deixadas pelo Backend-A.

(a) Editar "este e futuros" apagava plantões sem tombstone: o outro aparelho
    nunca sabia que eles sumiram.
(b) Plantão já desvinculado (local excluído pelo dono — AUD-006) repassado a
    um terceiro: o clone novo nascia sem local_nome.
"""

import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


class TestSerieComTombstone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _serie(self, headers, recorrencia, passo, total):
        local = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Serie", "data_inicio": "2026-04-01", "data_final": "2026-08-31",
        }).json()["data"]
        ids, dia = [], date(2026, 5, 1)
        for _ in range(total):
            r = self.client.post("/api/plantoes", headers=headers, json={
                "valor": "250.00", "data_inicio": f"{dia.isoformat()}T08:00:00",
                "data_fim": f"{dia.isoformat()}T14:00:00", "recorrencia": recorrencia, "local_id": local["id"],
                "uuid_cliente": str(uuid4()),
            })
            self.assertEqual(r.status_code, 201, r.text)
            ids.append(r.json()["data"]["id"])
            dia += timedelta(days=passo)
        return ids

    def _excluidos_no_pull(self, headers, antes):
        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": antes}).json()["data"]
        return sorted(e["id"] for e in pull["exclusoes"] if e["entidade"] == "plantao")

    def test_virar_unico_gera_tombstone_dos_futuros(self):
        _, headers = criar_usuario_no_banco()
        ids = self._serie(headers, "Semanal", 7, 4)
        antes = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        r = self.client.patch(f"/api/plantoes/{ids[0]}", headers=headers,
                              json={"aplicar_em": "este_e_futuros", "recorrencia": "Unico"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._excluidos_no_pull(headers, antes), sorted(ids[1:]))

    def test_semanal_para_quinzenal_gera_tombstone_dos_intercalados(self):
        _, headers = criar_usuario_no_banco()
        ids = self._serie(headers, "Semanal", 7, 6)
        antes = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        r = self.client.patch(f"/api/plantoes/{ids[0]}", headers=headers,
                              json={"aplicar_em": "este_e_futuros", "recorrencia": "Quinzenal"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._excluidos_no_pull(headers, antes), sorted([ids[1], ids[3], ids[5]]))


class TestRepasseDePlantaoDesvinculado(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _amigos(self, h_x, id_y, h_y):
        amizade = self.client.post("/api/amizades", headers=h_x, json={"destinatario_usuario_id": id_y})
        self.assertEqual(amizade.status_code, 201, amizade.text)
        self.assertEqual(
            self.client.patch(f"/api/amizades/{amizade.json()['data']['id']}/aceitar", headers=h_y).status_code, 200
        )

    def _transferir(self, h_de, id_para, h_para, plantao_id):
        criada = self.client.post("/api/solicitacoes-troca", headers=h_de, json={
            "destinatario_usuario_id": id_para, "tipo": "transferir", "plantao_transferido_id": plantao_id,
        })
        self.assertEqual(criada.status_code, 201, criada.text)
        aceita = self.client.patch(f"/api/solicitacoes-troca/{criada.json()['data']['id']}/aceitar", headers=h_para)
        self.assertEqual(aceita.status_code, 200, aceita.text)
        recebidos = [p for p in self.client.get("/api/plantoes", headers=h_para).json()["data"]
                     if p.get("recebido_via_transferencia")]
        self.assertEqual(len(recebidos), 1, recebidos)
        return recebidos[0]

    def test_clone_de_plantao_desvinculado_leva_o_nome_do_hospital(self):
        id_a, h_a = criar_usuario_no_banco()
        id_b, h_b = criar_usuario_no_banco()
        id_d, h_d = criar_usuario_no_banco()
        self._amigos(h_a, id_b, h_b)
        self._amigos(h_b, id_d, h_d)

        local = self.client.post("/api/locais", headers=h_a, json={
            "nome_local": "Hospital Repasse", "data_inicio": "2026-01-01", "data_final": "2026-12-31",
        }).json()["data"]
        plantao = self.client.post("/api/plantoes", headers=h_a, json={
            "local_id": local["id"], "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00", "valor": "1200.00",
        }).json()["data"]
        clone_b = self._transferir(h_a, id_b, h_b, plantao["id"])

        self.assertEqual(self.client.delete(f"/api/locais/{local['id']}", headers=h_a).status_code, 200)
        desvinculado = next(p for p in self.client.get("/api/plantoes", headers=h_b).json()["data"]
                            if p["id"] == clone_b["id"])
        self.assertIsNone(desvinculado["local_id"])
        self.assertEqual(desvinculado["local_nome"], "Hospital Repasse", "premissa: K1")

        clone_d = self._transferir(h_b, id_d, h_d, clone_b["id"])
        self.assertIsNone(clone_d["local_id"])
        self.assertEqual(clone_d["local_nome"], "Hospital Repasse")


if __name__ == "__main__":
    unittest.main()
