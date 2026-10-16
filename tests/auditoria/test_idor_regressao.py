"""Autorização entre usuários (IDOR) — REGRESSÃO, deve PASSAR hoje.

Dois usuários fictícios, A e B. B tenta agir sobre recursos de A por id, pela
API REST e pelo /api/sync/push; um terceiro C tenta mexer em amizade e
solicitação de troca alheias; e um token revogado no logout não pode voltar a
valer. Cenários: agente 5 S11, S12, S14, S15, S16.

Prova de dente (registrada no relatório do agente 4): reintroduzindo o defeito
com `unittest.mock.patch` no repositório/serviço que faz a checagem de dono,
os testes correspondentes falham.
"""

import unittest
from datetime import timedelta

import aud_helpers as h


class TestIdorRest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def setUp(self):
        self.id_a, self.headers_a, *_ = h.criar_usuario(self.client)
        self.id_b, self.headers_b, *_ = h.criar_usuario(self.client)
        self.local_a = h.criar_local(self.client, self.headers_a, nome="Local de A")
        self.plantao_a = h.criar_plantao(self.client, self.headers_a, self.local_a["id"])
        bon = self.client.post("/api/bonificacoes", headers=self.headers_a, json={
            "plantao_id": self.plantao_a["id"], "valor": "50.00", "descricao": "de A"})
        self.assertEqual(bon.status_code, 201, bon.text)
        self.bon_a = bon.json()["data"]

    def _estado_de_a(self):
        plantoes = h.listar_plantoes(self.client, self.headers_a)
        locais = self.client.get("/api/locais", headers=self.headers_a).json()["data"]
        return (
            [(p["id"], p["valor"], p["data_inicio"], p["executado"], p["local_id"],
              [(b["id"], b["valor"]) for b in p["bonificacoes"]]) for p in plantoes],
            [(l["id"], l["nome_local"], l["dia_pagamento"]) for l in locais],
        )

    def test_b_nao_age_sobre_recursos_de_a(self):
        antes = self._estado_de_a()
        pa, la, ba = self.plantao_a["id"], self.local_a["id"], self.bon_a["id"]
        local_b = h.criar_local(self.client, self.headers_b)
        plantao_b = h.criar_plantao(self.client, self.headers_b, local_b["id"])

        tentativas = [
            ("PATCH plantao", "PATCH", f"/api/plantoes/{pa}", {"valor": "1.00"}),
            ("DELETE plantao", "DELETE", f"/api/plantoes/{pa}", None),
            ("PATCH executado", "PATCH", f"/api/plantoes/{pa}/executado", {"executado": True}),
            ("PATCH local", "PATCH", f"/api/locais/{la}", {"nome_local": "Invadido"}),
            ("DELETE local", "DELETE", f"/api/locais/{la}", None),
            ("GET financeiro local", "GET", f"/api/financeiro/local/{la}", None),
            ("GET bonificacoes", "GET", f"/api/bonificacoes?plantao_id={pa}", None),
            ("POST bonificacao", "POST", "/api/bonificacoes", {"plantao_id": pa, "valor": "9.00"}),
            ("PATCH bonificacao", "PATCH", f"/api/bonificacoes/{ba}", {"valor": "9.00"}),
            ("DELETE bonificacao", "DELETE", f"/api/bonificacoes/{ba}", None),
            ("PATCH plantao proprio p/ local de A", "PATCH", f"/api/plantoes/{plantao_b['id']}", {"local_id": la}),
        ]
        for nome, metodo, url, corpo in tentativas:
            with self.subTest(nome):
                r = self.client.request(metodo, url, headers=self.headers_b, json=corpo)
                self.assertIn(r.status_code, (403, 404), f"{nome}: {r.status_code} {r.text[:200]}")

        self.assertEqual(self._estado_de_a(), antes, "algum recurso de A mudou")


class TestIdorSyncPush(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_push_de_b_com_ids_de_a_nao_altera_nada_de_a(self):
        _, headers_a, *_ = h.criar_usuario(self.client)
        _, headers_b, *_ = h.criar_usuario(self.client)
        local_a = h.criar_local(self.client, headers_a, nome="Local de A")
        plantao_a = h.criar_plantao(self.client, headers_a, local_a["id"])
        bon_a = self.client.post("/api/bonificacoes", headers=headers_a, json={
            "plantao_id": plantao_a["id"], "valor": "50.00"}).json()["data"]
        futuro = h.agora_iso(timedelta(days=1))

        r = self.client.post("/api/sync/push", headers=headers_b, json={
            "locais": [{"uuid_cliente": "idor-l", "id": local_a["id"], "nome_local": "Invadido",
                        "data_inicio": "2026-01-01", "data_final": "2026-01-31", "atualizado_em": futuro}],
            "plantoes": [{"uuid_cliente": "idor-p", "id": plantao_a["id"], "deletado": True,
                          "data_inicio": "2026-07-17T07:00:00", "data_fim": "2026-07-17T19:00:00",
                          "atualizado_em": futuro}],
            "bonificacoes": [{"uuid_cliente": "idor-b", "id": bon_a["id"], "deletado": True,
                              "valor": "1.00", "atualizado_em": futuro}],
        })
        self.assertEqual(r.status_code, 200, r.text)

        locais_a = self.client.get("/api/locais", headers=headers_a).json()["data"]
        self.assertEqual([l["nome_local"] for l in locais_a], ["Local de A"])
        plantoes_a = h.listar_plantoes(self.client, headers_a)
        self.assertEqual([p["id"] for p in plantoes_a], [plantao_a["id"]])
        self.assertEqual([b["id"] for b in plantoes_a[0]["bonificacoes"]], [bon_a["id"]])
        # O local enviado por B virou um local NOVO de B, não uma edição do de A.
        locais_b = self.client.get("/api/locais", headers=headers_b).json()["data"]
        self.assertEqual([l["nome_local"] for l in locais_b], ["Invadido"])
        self.assertNotEqual(locais_b[0]["id"], local_a["id"])


class TestTerceiroERevogacao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def test_terceiro_nao_responde_amizade_alheia(self):
        _, headers_a, *_ = h.criar_usuario(self.client)
        id_b, headers_b, *_ = h.criar_usuario(self.client)
        _, headers_c, *_ = h.criar_usuario(self.client)
        amizade_id = self.client.post("/api/amizades", headers=headers_a,
                                      json={"destinatario_usuario_id": id_b}).json()["data"]["id"]
        for metodo, url in (("PATCH", f"/api/amizades/{amizade_id}/aceitar"),
                            ("PATCH", f"/api/amizades/{amizade_id}/rejeitar"),
                            ("DELETE", f"/api/amizades/{amizade_id}")):
            with self.subTest(url=url, metodo=metodo):
                self.assertEqual(self.client.request(metodo, url, headers=headers_c).status_code, 404)
        status = [a["status"] for a in self.client.get("/api/amizades", headers=headers_b).json()["data"]]
        self.assertEqual(status, ["pendente"])

    def test_terceiro_e_remetente_nao_respondem_solicitacao_de_troca(self):
        """Só leitura do fluxo de troca (validado em aparelho)."""
        id_a, headers_a, *_ = h.criar_usuario(self.client)
        id_b, headers_b, *_ = h.criar_usuario(self.client)
        _, headers_c, *_ = h.criar_usuario(self.client)
        h.tornar_amigos(self.client, id_a, headers_a, id_b, headers_b)
        local = h.criar_local(self.client, headers_a)
        plantao = h.criar_plantao(self.client, headers_a, local["id"])
        sid = self.client.post("/api/solicitacoes-troca", headers=headers_a, json={
            "destinatario_usuario_id": id_b, "tipo": "transferir", "plantao_transferido_id": plantao["id"],
        }).json()["data"]["id"]

        for quem, headers, metodo, url in (
            ("C aceita", headers_c, "PATCH", f"/api/solicitacoes-troca/{sid}/aceitar"),
            ("C rejeita", headers_c, "PATCH", f"/api/solicitacoes-troca/{sid}/rejeitar"),
            ("C cancela", headers_c, "DELETE", f"/api/solicitacoes-troca/{sid}"),
            ("A aceita a propria", headers_a, "PATCH", f"/api/solicitacoes-troca/{sid}/aceitar"),
        ):
            with self.subTest(quem):
                self.assertEqual(self.client.request(metodo, url, headers=headers).status_code, 404)
        lista = self.client.get("/api/solicitacoes-troca", headers=headers_b).json()["data"]
        self.assertEqual([s["status"] for s in lista], ["pendente"])
        self.assertFalse(any(p.get("recebido_via_transferencia") for p in h.listar_plantoes(self.client, headers_b)))

    def test_token_revogado_no_logout_nao_volta_a_valer(self):
        _, headers, *_ = h.criar_usuario(self.client)
        self.assertEqual(self.client.get("/api/plantoes", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        for metodo, url in (("GET", "/api/sync/pull"), ("GET", "/api/plantoes"), ("POST", "/api/auth/refresh")):
            with self.subTest(url=url):
                self.assertEqual(self.client.request(metodo, url, headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
