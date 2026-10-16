"""AUD-203 / AUD-304 (P1): um item inválido no push derruba o lote inteiro.

`SyncService._push_plantao` monta `PlantaoCreate(...)` FORA do `try`. A
validação de duração (> 48 h) e a recusa de fuso (`recusar_fuso`) levantam
`pydantic.ValidationError`, que não está na tupla capturada
(ErroDeDominio, HTTPException, IntegrityError) e vira 500 — depois de o local
do mesmo lote já ter sido gravado com commit.

Também aqui, por serem do mesmo contrato de push/edição:
- AUD-208: exclusão de registro desconhecido não recebe resposta nenhuma.
- FUSO-01 residual (S-18): PATCH só com `data_inicio` com "Z" dá 500.
- AUD-212: PATCH só com `valor` sobrescreve o pagamento definido à mão.

Cenários: agente 2 S-03/S-04/S-09/S-11/S-18, agente 3 P1/P2.
"""

import unittest
from datetime import timedelta

import aud_helpers as h


class TestAud203PushLote(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # raise_server_exceptions=False: queremos ver o 500 que o app devolveria.
        cls.client = h.cliente(raise_server_exceptions=False)

    def _push(self, headers, **corpo):
        return self.client.post("/api/sync/push", headers=headers, json=corpo)

    def test_plantao_de_72h_vira_erro_do_item_e_nao_500__AUD203(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (500; o local do mesmo lote fica gravado)."""
        _, headers, *_ = h.criar_usuario(self.client)
        agora = h.agora_iso()
        resposta = self._push(
            headers,
            locais=[{"uuid_cliente": "aud203-local", "nome_local": "Hospital Lote",
                     "data_inicio": "2026-07-01", "data_final": "2026-07-31", "atualizado_em": agora}],
            plantoes=[{"uuid_cliente": "aud203-p72", "local_uuid_cliente": "aud203-local",
                       "data_inicio": "2026-07-17T07:00:00", "data_fim": "2026-07-20T07:00:00",
                       "valor": "100.00", "atualizado_em": agora}],
        )
        locais_gravados = self.client.get("/api/locais", headers=headers).json()["data"]
        self.assertEqual(
            resposta.status_code, 200,
            f"push devolveu {resposta.status_code}; locais já gravados no servidor: {len(locais_gravados)}",
        )
        dados = resposta.json()["data"]
        self.assertEqual(len(dados["locais_criados"]), 1)
        self.assertEqual([e["uuid_cliente"] for e in dados["erros"]], ["aud203-p72"])

    def test_controle_mesmo_lote_so_com_o_local_passa(self):
        """Controle (PASSA): sem o plantão inválido, o mesmo local é aceito."""
        _, headers, *_ = h.criar_usuario(self.client)
        resposta = self._push(headers, locais=[{
            "uuid_cliente": "aud203-local-ok", "nome_local": "Hospital Lote",
            "data_inicio": "2026-07-01", "data_final": "2026-07-31", "atualizado_em": h.agora_iso(),
        }])
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(len(resposta.json()["data"]["locais_criados"]), 1)

    def test_plantao_novo_com_Z_vira_erro_do_item__AUD203(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (500 em vez de erro isolado 422)."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        resposta = self._push(headers, plantoes=[{
            "uuid_cliente": "aud203-z", "local_id": local["id"],
            "data_inicio": "2026-07-17T07:00:00Z", "data_fim": "2026-07-17T19:00:00Z",
            "atualizado_em": h.agora_iso(),
        }])
        self.assertEqual(resposta.status_code, 200, resposta.text[:300])
        self.assertEqual([e["uuid_cliente"] for e in resposta.json()["data"]["erros"]], ["aud203-z"])

    def test_bonificacao_com_descricao_longa_nao_derruba_o_lote__AUD304(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (422 no corpo inteiro; o local válido não é criado)."""
        _, headers, *_ = h.criar_usuario(self.client)
        agora = h.agora_iso()
        resposta = self._push(
            headers,
            locais=[{"uuid_cliente": "aud304-local", "nome_local": "Hospital Lote",
                     "data_inicio": "2026-07-01", "data_final": "2026-07-31", "atualizado_em": agora}],
            bonificacoes=[{"uuid_cliente": "aud304-bon", "plantao_uuid_cliente": "inexistente",
                           "valor": "10.00", "descricao": "x" * 501, "atualizado_em": agora}],
        )
        self.assertEqual(resposta.status_code, 200, resposta.text[:300])
        dados = resposta.json()["data"]
        self.assertEqual(len(dados["locais_criados"]), 1)
        self.assertEqual([e["uuid_cliente"] for e in dados["erros"]], ["aud304-bon"])

    def test_exclusao_de_plantao_desconhecido_recebe_resposta__AUD208(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: o item não aparece em lugar nenhum da resposta."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        resposta = self._push(headers, plantoes=[{
            "uuid_cliente": "aud208-fantasma", "local_id": local["id"],
            "data_inicio": "2026-07-17T07:00:00", "data_fim": "2026-07-17T19:00:00",
            "atualizado_em": h.agora_iso(), "deletado": True,
        }])
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["data"]
        mencionado = [r["uuid_cliente"] for r in dados["plantoes_deletados"]] + \
                     [e["uuid_cliente"] for e in dados["erros"] if e["status_code"] in (404, 410)]
        self.assertIn("aud208-fantasma", mencionado)


class TestEdicaoRest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = h.cliente(raise_server_exceptions=False)

    def test_patch_so_data_inicio_com_Z_responde_422__FUSO01(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão (500 por TypeError naive x aware em ajustar_fim_intervalo)."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        plantao = h.criar_plantao(self.client, headers, local["id"])
        resposta = self.client.patch(f"/api/plantoes/{plantao['id']}", headers=headers,
                                     json={"data_inicio": "2026-07-17T08:00:00Z"})
        self.assertEqual(resposta.status_code, 422, resposta.text[:300])

    def test_controle_patch_sem_Z_responde_200(self):
        """Controle (PASSA)."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers)
        plantao = h.criar_plantao(self.client, headers, local["id"])
        resposta = self.client.patch(f"/api/plantoes/{plantao['id']}", headers=headers,
                                     json={"data_inicio": "2026-07-17T08:00:00"})
        self.assertEqual(resposta.status_code, 200, resposta.text[:300])

    def test_patch_so_do_valor_mantem_pagamento_manual__AUD212(self):
        """Falhava antes da correção (2026-10-15); hoje é regressão: o PATCH recalcula e sobrescreve a data escolhida à mão."""
        _, headers, *_ = h.criar_usuario(self.client)
        local = h.criar_local(self.client, headers, data_inicio="2026-01-01",
                              data_final="2026-12-31", dia_pagamento=5)
        plantao = h.criar_plantao(self.client, headers, local["id"], pagamento_plantao="2026-09-30")
        self.assertEqual(plantao["pagamento_plantao"], "2026-09-30", "premissa: a data manual foi aceita")

        resposta = self.client.patch(f"/api/plantoes/{plantao['id']}", headers=headers, json={"valor": "1500.00"})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        atual = next(p for p in h.listar_plantoes(self.client, headers) if p["id"] == plantao["id"])
        self.assertEqual(atual["valor"], "1500.00")
        self.assertEqual(atual["pagamento_plantao"], "2026-09-30")


if __name__ == "__main__":
    unittest.main()
