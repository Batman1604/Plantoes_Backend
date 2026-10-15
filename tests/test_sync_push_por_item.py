"""AUD-007 (contrato K3): falha de um item do push não derruba o lote.

Os casos de schema (bonificação com descrição longa, plantão de 72 h, data com
"Z") estão em tests/auditoria/test_aud203_push_lote.py. Aqui fica o que eles
não cobrem: falha de BANCO ao gravar um item (SQLAlchemyError que não é
IntegrityError) e o FUSO-01 no campo data_fim do PATCH.
"""

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app
from app.services.local_service import LocalService
from tests.fixtures_usuario import criar_usuario_no_banco


def _usuario_direto_no_banco() -> dict:
    return criar_usuario_no_banco()[1]


def _local(uuid: str) -> dict:
    return {"uuid_cliente": uuid, "nome_local": "Hospital", "data_inicio": "2026-07-01",
            "data_final": "2026-07-31", "atualizado_em": datetime.now(UTC).isoformat()}


class TestFalhaDeBancoNumItem(unittest.TestCase):
    def test_falha_de_banco_vira_erro_do_item_e_os_outros_seguem(self):
        client = TestClient(app, raise_server_exceptions=False)
        headers = _usuario_direto_no_banco()
        criar_real = LocalService.criar
        chamadas = []

        def _criar(db, usuario, payload):
            chamadas.append(payload.uuid_cliente)
            if len(chamadas) == 1:
                raise OperationalError("INSERT", {}, Exception("conexão caiu"))
            return criar_real(db, usuario, payload)

        with patch("app.services.sync_service.LocalService.criar", side_effect=_criar), \
             self.assertLogs("app.services.sync_service", level="ERROR") as logs:
            resposta = client.post("/api/sync/push", headers=headers,
                                   json={"locais": [_local("falha-1"), _local("ok-2")]})

        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["data"]
        self.assertEqual([(e["uuid_cliente"], e["status_code"]) for e in dados["erros"]], [("falha-1", 500)])
        self.assertEqual([r["uuid_cliente"] for r in dados["locais_criados"]], ["ok-2"])
        self.assertTrue(any("Traceback" in linha for linha in logs.output), "a causa precisa ir para o log")


class TestFusoNaEdicao(unittest.TestCase):
    def test_patch_com_data_fim_com_fuso_responde_422__FUSO01(self):
        client = TestClient(app, raise_server_exceptions=False)
        headers = _usuario_direto_no_banco()
        local = client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital", "data_inicio": "2026-07-01", "data_final": "2026-07-31"}).json()["data"]
        plantao = client.post("/api/plantoes", headers=headers, json={
            "local_id": local["id"], "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00"}).json()["data"]

        resposta = client.patch(f"/api/plantoes/{plantao['id']}", headers=headers,
                                json={"data_fim": "2026-07-17T20:00:00-03:00"})
        self.assertEqual(resposta.status_code, 422, resposta.text)


class TestExclusaoIdempotente(unittest.TestCase):
    """AUD-014 (contrato K2): exclusão de item desconhecido volta em *_deletados
    para as quatro entidades (o caso do plantão está também na auditoria)."""

    def test_exclusao_de_desconhecidos_volta_em_deletados(self):
        client = TestClient(app)
        headers = _usuario_direto_no_banco()
        agora = datetime.now(UTC).isoformat()
        resposta = client.post("/api/sync/push", headers=headers, json={
            "locais": [{**_local("k2-local"), "deletado": True}],
            "trocas": [{"uuid_cliente": "k2-troca", "transferido_para": {"nome": "X", "telefone": "1"},
                        "atualizado_em": agora, "deletado": True}],
            "bonificacoes": [{"uuid_cliente": "k2-bon", "valor": "1.00", "atualizado_em": agora, "deletado": True}],
        })
        self.assertEqual(resposta.status_code, 200, resposta.text)
        dados = resposta.json()["data"]
        self.assertEqual(
            ([r["uuid_cliente"] for r in dados["locais_deletados"]],
             [r["uuid_cliente"] for r in dados["trocas_deletadas"]],
             [r["uuid_cliente"] for r in dados["bonificacoes_deletadas"]],
             dados["erros"]),
            (["k2-local"], ["k2-troca"], ["k2-bon"], []),
        )

    def test_segunda_exclusao_do_mesmo_local_tambem_volta_em_deletados(self):
        client = TestClient(app)
        headers = _usuario_direto_no_banco()
        criado = client.post("/api/sync/push", headers=headers, json={"locais": [_local("k2-dup")]}).json()["data"]
        local_id = criado["locais_criados"][0]["id"]
        for _ in range(2):
            excluir = {**_local("k2-dup"), "id": local_id, "deletado": True,
                       "atualizado_em": datetime.now(UTC).isoformat()}
            dados = client.post("/api/sync/push", headers=headers, json={"locais": [excluir]}).json()["data"]
            self.assertEqual([r["uuid_cliente"] for r in dados["locais_deletados"]], ["k2-dup"])


if __name__ == "__main__":
    unittest.main()
