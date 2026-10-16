"""AUD-029, AUD-030 e AUD-062: privacidade entre amigos na troca e na agenda."""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Troca
from tests.fixtures_usuario import criar_usuario_no_banco


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _amigos(self):
        id_a, h_a = criar_usuario_no_banco(f"a-{uuid4().hex[:8]}@example.com")
        id_b, h_b = criar_usuario_no_banco(f"b-{uuid4().hex[:8]}@example.com")
        amizade = self.client.post("/api/amizades", headers=h_a, json={"destinatario_usuario_id": id_b})
        self.assertEqual(amizade.status_code, 201, amizade.text)
        aceite = self.client.patch(f"/api/amizades/{amizade.json()['data']['id']}/aceitar", headers=h_b)
        self.assertEqual(aceite.status_code, 200, aceite.text)
        return id_a, h_a, id_b, h_b

    def _local(self, headers, nome):
        r = self.client.post("/api/locais", headers=headers, json={
            "nome_local": nome, "data_inicio": "2026-01-01", "data_final": "2026-01-31",
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    def _plantao(self, headers, local_id, valor="1234.00", dia="2026-07-17"):
        r = self.client.post("/api/plantoes", headers=headers, json={
            "local_id": local_id, "data_inicio": f"{dia}T07:00:00", "data_fim": f"{dia}T19:00:00",
            "valor": valor, "pagamento_plantao": "2026-08-05",
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]

    def _configurar(self, h_dono, **campos):
        config_id = self.client.get("/api/amizades", headers=h_dono).json()["data"][0]["configuracao_id"]
        r = self.client.patch(f"/api/amigo-configuracoes/{config_id}", headers=h_dono, json=campos)
        self.assertEqual(r.status_code, 200, r.text)


class TestPlantaoAlvoVisivel(_Base):
    def test_trocar_com_plantao_de_local_oculto_responde_404__AUD029(self):
        id_a, h_a, id_b, h_b = self._amigos()
        visivel = self._local(h_b, "Visivel")
        oculto = self._local(h_b, "Oculto")
        alvo = self._plantao(h_b, oculto["id"])
        self._configurar(h_b, modo_visibilidade="personalizado", locais_permitidos_ids=[visivel["id"]])

        meu_local = self._local(h_a, "Meu")
        meu = self._plantao(h_a, meu_local["id"], dia="2026-07-18")
        r = self.client.post("/api/solicitacoes-troca", headers=h_a, json={
            "destinatario_usuario_id": id_b, "tipo": "trocar",
            "plantao_transferido_id": meu["id"], "plantao_alvo_id": alvo["id"],
        })
        self.assertEqual(r.status_code, 404, r.text)

    def test_alvo_visivel_com_valores_ocultos_chega_sem_valor__AUD029(self):
        id_a, h_a, id_b, h_b = self._amigos()
        local_b = self._local(h_b, "Permitido")
        alvo = self._plantao(h_b, local_b["id"], valor="999.00")
        self._configurar(h_b, mostrar_valores=False)

        r = self.client.post("/api/solicitacoes-troca", headers=h_a, json={
            "destinatario_usuario_id": id_b, "tipo": "receber", "plantao_alvo_id": alvo["id"],
        })
        self.assertEqual(r.status_code, 201, r.text)

        do_remetente = self.client.get("/api/solicitacoes-troca", headers=h_a).json()["data"][0]
        self.assertEqual(float(do_remetente["plantao_recebido"]["valor"]), 0.0)
        do_pull = self.client.get("/api/sync/pull", headers=h_a).json()["data"]["solicitacoes_troca"][0]
        self.assertEqual(float(do_pull["plantao_recebido"]["valor"]), 0.0)
        # O dono continua vendo o próprio valor.
        do_dono = self.client.get("/api/solicitacoes-troca", headers=h_b).json()["data"][0]
        self.assertEqual(float(do_dono["plantao_recebido"]["valor"]), 999.0)


class TestTrocasDoLocal(_Base):
    def test_troca_de_outro_usuario_no_meu_local_nao_aparece__AUD030(self):
        id_a, h_a, id_b, _ = self._amigos()
        local_a = self._local(h_a, "Local de A")
        propria = self.client.get(f"/api/trocas?local_id={local_a['id']}", headers=h_a)
        self.assertEqual(propria.status_code, 200, propria.text)
        with SessionLocal() as db:
            # Troca de B apontando para o local de A, como fica a troca manual
            # registrada com um plantão recebido de A.
            db.add(Troca(usuario_id=id_b, local_id=local_a["id"], nome_transferir="Terceiro",
                         telefone_transferir="11900000000"))
            db.add(Troca(usuario_id=id_a, local_id=local_a["id"], nome_transferir="Minha"))
            db.commit()
        r = self.client.get(f"/api/trocas?local_id={local_a['id']}", headers=h_a)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([t["nome_transferir"] for t in r.json()["data"]], ["Minha"])


class TestAgendaDoAmigo(_Base):
    def test_valores_ocultos_escondem_pagamento_e_bonificacao__AUD062(self):
        _, h_a, _, h_b = self._amigos()
        local_b = self._local(h_b, "Agenda")
        plantao = self._plantao(h_b, local_b["id"])
        bon = self.client.post("/api/bonificacoes", headers=h_b, json={
            "plantao_id": plantao["id"], "valor": "500.00", "descricao": "Plantão extra de feriado",
        })
        self.assertEqual(bon.status_code, 201, bon.text)

        visto = self.client.get("/api/amigos/plantoes", headers=h_a).json()["data"][0]["plantao"]
        self.assertEqual(visto["pagamento_plantao"], "2026-08-05", "premissa: com valores visíveis, aparece")

        self._configurar(h_b, mostrar_valores=False)
        oculto = self.client.get("/api/amigos/plantoes", headers=h_a).json()["data"][0]["plantao"]
        self.assertEqual(float(oculto["valor"]), 0.0)
        self.assertIsNone(oculto["pagamento_plantao"])
        self.assertEqual([(float(b["valor"]), b["descricao"]) for b in oculto["bonificacoes"]], [(0.0, None)])


if __name__ == "__main__":
    unittest.main()
