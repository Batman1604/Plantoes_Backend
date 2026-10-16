"""AUD-202 / AUD-303 / AUD-502 (P1): quem enviou um plantão apaga ou altera o
plantão RECEBIDO pelo amigo ao excluir/editar o próprio local ou a conta.

O clone criado para B no aceite herda `local_id` do local de A
(aceitar_solicitacao_troca). `Local.plantoes` tem cascade delete-orphan e
`LocalRepository.delete_by_user` grava o tombstone com o `usuario_id` de A.
`LocalService._recalcular_pagamentos` percorre todos os plantões do local, de
qualquer usuário.

Também AUD-207 / AUD-310: excluir um plantão com solicitação de troca pendente
apaga a solicitação em cascata sem tombstone para o destinatário.

Estes testes SÓ observam o fluxo de troca (validado em aparelho); não o alteram.
Cenários: agente 2 S-02/S-08, agente 3 P3/P5, agente 5 S2/S3/S4.
"""

import unittest

import aud_helpers as h


class TestAud202LocalApagaRecebido(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def _cenario(self, **local_extra):
        id_a, headers_a, *_ = h.criar_usuario(self.client)
        id_b, headers_b, *_ = h.criar_usuario(self.client)
        h.tornar_amigos(self.client, id_a, headers_a, id_b, headers_b)
        local_a = h.criar_local(self.client, headers_a, **local_extra)
        plantao_a = h.criar_plantao(self.client, headers_a, local_a["id"], data="2026-03-15")
        _, clone_b = h.transferir_por_solicitacao(self.client, headers_a, id_b, headers_b, plantao_a["id"])
        self.assertEqual(clone_b["local_id"], local_a["id"], "premissa: o clone herda o local de A")
        return headers_a, id_b, headers_b, local_a, clone_b

    def _recebido_sobrevive_ou_tombstone(self, headers_b, clone_b, marcador):
        ainda_existe = any(p["id"] == clone_b["id"] for p in h.listar_plantoes(self.client, headers_b))
        exclusoes_b = [
            e for e in h.pull(self.client, headers_b, since=marcador)["exclusoes"]
            if e["entidade"] == "plantao" and e["id"] == clone_b["id"]
        ]
        self.assertTrue(
            ainda_existe or exclusoes_b,
            "o plantão recebido por B sumiu sem que o pull de B recebesse a exclusão",
        )
        self.assertTrue(ainda_existe, "excluir o local de A não deveria apagar o plantão que B recebeu")

    def test_excluir_local_pelo_rest_nao_apaga_recebido__AUD202(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão."""
        headers_a, _, headers_b, local_a, clone_b = self._cenario()
        marcador = h.pull(self.client, headers_b)["servidor_timestamp"]

        resposta = self.client.delete(f"/api/locais/{local_a['id']}", headers=headers_a)
        self.assertEqual(resposta.status_code, 200, resposta.text)

        self._recebido_sobrevive_ou_tombstone(headers_b, clone_b, marcador)

    def test_excluir_local_pelo_sync_nao_apaga_recebido__AUD303(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (mesmo defeito, caminho /api/sync/push)."""
        headers_a, _, headers_b, local_a, clone_b = self._cenario()
        marcador = h.pull(self.client, headers_b)["servidor_timestamp"]

        from datetime import timedelta
        resposta = self.client.post("/api/sync/push", headers=headers_a, json={"locais": [{
            "uuid_cliente": local_a.get("uuid_cliente") or "aud303-local",
            "id": local_a["id"],
            "nome_local": local_a["nome_local"],
            "data_final": local_a["data_final"],
            "atualizado_em": h.agora_iso(timedelta(minutes=1)),
            "deletado": True,
        }]})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(len(resposta.json()["data"]["locais_deletados"]), 1)

        self._recebido_sobrevive_ou_tombstone(headers_b, clone_b, marcador)

    def test_excluir_conta_de_a_nao_apaga_recebido__AUD502(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (cascata ORM User -> Local -> Plantao)."""
        headers_a, _, headers_b, _, clone_b = self._cenario()

        resposta = self.client.request("DELETE", "/api/usuarios", headers=headers_a, json={"senha_atual": h.SENHA})
        self.assertEqual(resposta.status_code, 200, resposta.text)

        ids_b = [p["id"] for p in h.listar_plantoes(self.client, headers_b)]
        self.assertIn(clone_b["id"], ids_b)

    def test_editar_dia_pagamento_de_a_nao_altera_recebido__AUD502(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: o recálculo em lote reescreve o pagamento do clone de B."""
        headers_a, _, headers_b, local_a, clone_b = self._cenario(
            data_inicio="2026-01-01", data_final="2026-12-31", dia_pagamento=5,
        )
        antes = next(p for p in h.listar_plantoes(self.client, headers_b) if p["id"] == clone_b["id"])

        resposta = self.client.patch(f"/api/locais/{local_a['id']}", headers=headers_a, json={"dia_pagamento": 20})
        self.assertEqual(resposta.status_code, 200, resposta.text)

        depois = next(p for p in h.listar_plantoes(self.client, headers_b) if p["id"] == clone_b["id"])
        self.assertEqual(
            (depois["pagamento_plantao"], depois["atualizado_em"]),
            (antes["pagamento_plantao"], antes["atualizado_em"]),
            "editar o local de A alterou o plantão que pertence a B",
        )


class TestAud207CascataSolicitacao(unittest.TestCase):
    """A cascata plantao -> solicitacoes_troca existe só no banco
    (`ForeignKey(..., ondelete="CASCADE")`, sem cascade no ORM). O SQLite da
    suíte não liga `PRAGMA foreign_keys`, então aqui ela é ligada SÓ durante
    este teste, para reproduzir o que o Postgres faz em produção."""

    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente()

    def setUp(self):
        from sqlalchemy import event
        from app.core.database import engine

        def _fk_on(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        engine.dispose()
        event.listen(engine, "connect", _fk_on)

        def _desfazer():
            event.remove(engine, "connect", _fk_on)
            engine.dispose()

        self.addCleanup(_desfazer)

    def test_excluir_plantao_com_solicitacao_pendente_gera_exclusao_para_b__AUD207(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (AUD-207/AUD-310)."""
        id_a, headers_a, *_ = h.criar_usuario(self.client)
        id_b, headers_b, *_ = h.criar_usuario(self.client)
        h.tornar_amigos(self.client, id_a, headers_a, id_b, headers_b)
        local_a = h.criar_local(self.client, headers_a)
        plantao_a = h.criar_plantao(self.client, headers_a, local_a["id"])
        criada = self.client.post("/api/solicitacoes-troca", headers=headers_a, json={
            "destinatario_usuario_id": id_b, "tipo": "transferir", "plantao_transferido_id": plantao_a["id"],
        })
        self.assertEqual(criada.status_code, 201, criada.text)
        solicitacao_id = criada.json()["data"]["id"]

        # Controle com dente: B via a solicitação antes da exclusão.
        self.assertEqual(len(self.client.get("/api/solicitacoes-troca", headers=headers_b).json()["data"]), 1)
        marcador = h.pull(self.client, headers_b)["servidor_timestamp"]

        resposta = self.client.delete(f"/api/plantoes/{plantao_a['id']}", headers=headers_a)
        self.assertEqual(resposta.status_code, 200, resposta.text)

        # Premissa: a solicitação sumiu do servidor (cascata).
        self.assertEqual(self.client.get("/api/solicitacoes-troca", headers=headers_b).json()["data"], [])
        exclusoes = h.pull(self.client, headers_b, since=marcador)["exclusoes"]
        self.assertIn(
            ("solicitacao_troca", solicitacao_id),
            [(e["entidade"], e["id"]) for e in exclusoes],
            "o aparelho de B fica com a solicitação fantasma para sempre",
        )


if __name__ == "__main__":
    unittest.main()
