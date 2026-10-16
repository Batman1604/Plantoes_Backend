import os
import unittest
from datetime import UTC, datetime, timedelta


from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import criar_usuario_no_banco


def _envelhecer_plantao(plantao_id: int) -> None:
    from sqlalchemy import update

    from app.core.database import SessionLocal
    from app.models import Plantao

    with SessionLocal() as db:
        db.execute(update(Plantao).where(Plantao.id == plantao_id)
                   .values(atualizado_em=datetime.now(UTC) - timedelta(minutes=1)))
        db.commit()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def criar_usuario(self, email):
        # AUD-011: POST /api/usuarios foi removido; o usuário é gravado direto no banco.
        usuario_id, headers = criar_usuario_no_banco(email)
        return headers, usuario_id

    def criar_local(self, headers, nome="Hospital Teste"):
        response = self.client.post("/api/locais", headers=headers, json={
            "nome_local": nome, "data_inicio": "2026-01-01", "data_final": "2026-01-31",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]

    def criar_plantao(self, headers, local_id, **overrides):
        payload = {
            "local_id": local_id,
            "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00",
            "valor": "1200.00",
        }
        payload.update(overrides)
        response = self.client.post("/api/plantoes", headers=headers, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]

    # ---------------------------------------------------------------
    # push: criação
    # ---------------------------------------------------------------

    def test_push_cria_local_e_plantao_no_mesmo_lote_via_uuid(self):
        headers, _ = self.criar_usuario("push_criacao@example.com")
        agora = _iso(datetime.now(UTC))
        payload = {
            "locais": [{
                "uuid_cliente": "local-uuid-1",
                "nome_local": "Hospital Offline",
                "data_inicio": "2026-02-01",
                "data_final": "2026-02-28",
                "atualizado_em": agora,
            }],
            "plantoes": [{
                "uuid_cliente": "plantao-uuid-1",
                "local_uuid_cliente": "local-uuid-1",
                "data_inicio": "2026-02-05T08:00:00",
                "data_fim": "2026-02-05T16:00:00",
                "valor": "500.00",
                "atualizado_em": agora,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["locais_criados"]), 1)
        self.assertEqual(len(data["plantoes_criados"]), 1)
        self.assertEqual(data["erros"], [])
        self.assertEqual(data["conflitos"], [])

        plantoes = self.client.get("/api/plantoes", headers=headers).json()["data"]
        self.assertEqual(len(plantoes), 1)
        self.assertEqual(str(plantoes[0]["valor"]), "500.00")
        self.assertEqual(plantoes[0]["uuid_cliente"], "plantao-uuid-1")

    def test_push_plantao_sem_local_id_nem_uuid_retorna_422(self):
        headers, _ = self.criar_usuario("push_invalido@example.com")
        payload = {
            "plantoes": [{
                "uuid_cliente": "plantao-sem-local",
                "data_inicio": "2026-02-05T08:00:00",
                "data_fim": "2026-02-05T16:00:00",
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        # AUD-007 (contrato K3): o item inválido vira erro 422 DELE, não do lote.
        self.assertEqual(response.status_code, 200, response.text)
        erros = response.json()["data"]["erros"]
        self.assertEqual([(e["uuid_cliente"], e["status_code"]) for e in erros], [("plantao-sem-local", 422)])

    # ---------------------------------------------------------------
    # push: edição com last-write-wins
    # ---------------------------------------------------------------

    def test_push_edicao_mais_recente_e_aplicada(self):
        headers, _ = self.criar_usuario("push_edicao@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        mais_novo = datetime.now(UTC) + timedelta(hours=1)
        payload = {
            "plantoes": [{
                "uuid_cliente": "edicao-uuid-1",
                "id": plantao["id"],
                "local_id": local["id"],
                "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00",
                "valor": "999.00",
                "atualizado_em": _iso(mais_novo),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["plantoes_atualizados"]), 1)

        atualizado = self.client.get("/api/plantoes", headers=headers).json()["data"][0]
        self.assertEqual(str(atualizado["valor"]), "999.00")

    def test_push_edicao_mais_antiga_vira_conflito_e_nao_e_aplicada(self):
        headers, _ = self.criar_usuario("push_conflito@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        # Edita normalmente pela rota "ao vivo" para o atualizado_em do servidor avançar.
        self.client.patch(f"/api/plantoes/{plantao['id']}", headers=headers, json={"valor": "1300.00"})

        mais_antigo = datetime.now(UTC) - timedelta(days=1)
        payload = {
            "plantoes": [{
                "uuid_cliente": "conflito-uuid-1",
                "id": plantao["id"],
                "local_id": local["id"],
                "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00",
                "valor": "1.00",
                "atualizado_em": _iso(mais_antigo),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["conflitos"]), 1)
        self.assertEqual(data["plantoes_atualizados"], [])

        estado = self.client.get("/api/plantoes", headers=headers).json()["data"][0]
        self.assertEqual(str(estado["valor"]), "1300.00")

    # ---------------------------------------------------------------
    # push: exclusão + tombstone no pull
    # ---------------------------------------------------------------

    def test_push_exclusao_gera_tombstone_visivel_no_pull(self):
        headers, _ = self.criar_usuario("push_exclusao@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        marcador = datetime.now(UTC)
        payload = {
            "plantoes": [{
                "uuid_cliente": "exclusao-uuid-1",
                "id": plantao["id"],
                "local_id": local["id"],
                "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00",
                "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
                "deletado": True,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["plantoes_deletados"]), 1)

        plantoes = self.client.get("/api/plantoes", headers=headers).json()["data"]
        self.assertEqual(plantoes, [])

        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": _iso(marcador)})
        self.assertEqual(pull.status_code, 200, pull.text)
        exclusoes = pull.json()["data"]["exclusoes"]
        self.assertEqual(len(exclusoes), 1)
        self.assertEqual(exclusoes[0]["entidade"], "plantao")
        self.assertEqual(exclusoes[0]["id"], plantao["id"])

    # ---------------------------------------------------------------
    # pull: filtro por since
    # ---------------------------------------------------------------

    def test_pull_retorna_apenas_alteracoes_desde_since(self):
        headers, _ = self.criar_usuario("pull_since@example.com")
        local = self.criar_local(headers)
        antigo = self.criar_plantao(headers, local["id"], data_inicio="2026-03-01T08:00:00", data_fim="2026-03-01T16:00:00")
        # AUD-023: o pull recua o `since` em 5 s para não perder commit tardio,
        # então o plantão "antigo" precisa estar mais velho que essa margem.
        _envelhecer_plantao(antigo["id"])

        marco = self.client.get("/api/sync/pull", headers=headers).json()["data"]["servidor_timestamp"]

        self.criar_plantao(headers, local["id"], data_inicio="2026-03-02T08:00:00", data_fim="2026-03-02T16:00:00")

        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": marco})
        self.assertEqual(pull.status_code, 200, pull.text)
        plantoes = pull.json()["data"]["plantoes"]
        self.assertEqual(len(plantoes), 1)
        self.assertEqual(str(plantoes[0]["data_inicio"]), "2026-03-02")

        pull_completo = self.client.get("/api/sync/pull", headers=headers)
        self.assertEqual(len(pull_completo.json()["data"]["plantoes"]), 2)

    # ---------------------------------------------------------------
    # segurança: não é possível alterar dado de outro usuário via id
    # ---------------------------------------------------------------

    def test_push_nao_permite_alterar_plantao_de_outro_usuario_via_id(self):
        dono_headers, _ = self.criar_usuario("dono_sync@example.com")
        local_dono = self.criar_local(dono_headers)
        plantao_dono = self.criar_plantao(dono_headers, local_dono["id"])

        atacante_headers, _ = self.criar_usuario("atacante_sync@example.com")
        local_atacante = self.criar_local(atacante_headers, nome="Hospital do Atacante")

        payload = {
            "plantoes": [{
                "uuid_cliente": "ataque-uuid-1",
                "id": plantao_dono["id"],
                "local_id": local_atacante["id"],
                "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00",
                "valor": "1.00",
                "atualizado_em": _iso(datetime.now(UTC) + timedelta(hours=1)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=atacante_headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        # Não conta como "atualização" do plantão do dono: vira uma criação nova do atacante.
        self.assertEqual(len(data["plantoes_criados"]), 1)
        self.assertEqual(data["plantoes_atualizados"], [])

        plantao_dono_ainda = self.client.get("/api/plantoes", headers=dono_headers).json()["data"][0]
        self.assertEqual(plantao_dono_ainda["id"], plantao_dono["id"])
        self.assertEqual(str(plantao_dono_ainda["valor"]), str(plantao_dono["valor"]))

    def test_push_sem_autenticacao_retorna_401(self):
        response = self.client.post("/api/sync/push", json={"locais": [], "plantoes": []})
        self.assertEqual(response.status_code, 401)

    def test_pull_sem_autenticacao_retorna_401(self):
        response = self.client.get("/api/sync/pull")
        self.assertEqual(response.status_code, 401)

    # ---------------------------------------------------------------
    # pull: amizades/solicitações também aparecem
    # ---------------------------------------------------------------

    def test_pull_inclui_amizades_e_configuracoes(self):
        a_headers, a_id = self.criar_usuario("pull_amigo_a@example.com")
        b_headers, b_id = self.criar_usuario("pull_amigo_b@example.com")

        criada = self.client.post("/api/amizades", headers=a_headers, json={"destinatario_usuario_id": b_id}).json()["data"]
        self.client.patch(f"/api/amizades/{criada['id']}/aceitar", headers=b_headers)

        pull_a = self.client.get("/api/sync/pull", headers=a_headers)
        self.assertEqual(pull_a.status_code, 200, pull_a.text)
        amizades = pull_a.json()["data"]["amizades"]
        self.assertEqual(len(amizades), 1)
        self.assertEqual(amizades[0]["amigo"]["id"], b_id)
        self.assertEqual(amizades[0]["status"], "aceito")
        self.assertEqual(len(pull_a.json()["data"]["amigo_configuracoes"]), 1)

    # ---------------------------------------------------------------
    # push: troca manual (transferência/recebimento offline)
    # ---------------------------------------------------------------

    def test_push_troca_cria_e_marca_plantao_como_transferido(self):
        headers, usuario_id = self.criar_usuario("push_troca_cria@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        payload = {
            "trocas": [{
                "uuid_cliente": "troca-uuid-1",
                "plantao_transferido_id": plantao["id"],
                "transferido_para": {"nome": "Fulano", "telefone": "11999990000"},
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["trocas_criadas"]), 1)
        self.assertEqual(data["trocas_criadas"][0]["uuid_cliente"], "troca-uuid-1")
        self.assertEqual(data["erros"], [])
        self.assertEqual(data["conflitos"], [])

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(len(trocas), 1)
        self.assertEqual(trocas[0]["nome_transferir"], "Fulano")
        self.assertEqual(trocas[0]["uuid_cliente"], "troca-uuid-1")

        plantoes = self.client.get("/api/plantoes", headers=headers).json()["data"]
        self.assertEqual(len(plantoes), 1)
        self.assertTrue(plantoes[0]["transferido"])
        self.assertEqual(plantoes[0]["tipo_troca"], "transferido")

    def test_push_troca_reenvio_com_mesmo_uuid_e_idempotente(self):
        headers, usuario_id = self.criar_usuario("push_troca_idempotente@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        item_troca = {
            "uuid_cliente": "troca-idempotente-1",
            "plantao_transferido_id": plantao["id"],
            "transferido_para": {"nome": "Ciclana", "telefone": "11988887777"},
            "atualizado_em": _iso(datetime.now(UTC)),
        }
        primeira = self.client.post("/api/sync/push", headers=headers, json={"trocas": [item_troca]})
        self.assertEqual(primeira.status_code, 200, primeira.text)
        primeiro_id = primeira.json()["data"]["trocas_criadas"][0]["id"]

        # Reenvio do mesmo uuid_cliente (ex.: app não recebeu a resposta e tenta de novo) —
        # não pode duplicar a troca nem marcar o plantão como transferido uma segunda vez.
        segunda = self.client.post("/api/sync/push", headers=headers, json={"trocas": [item_troca]})
        self.assertEqual(segunda.status_code, 200, segunda.text)
        segundo_resultado = segunda.json()["data"]
        self.assertEqual(len(segundo_resultado["trocas_criadas"]), 1)
        self.assertEqual(segundo_resultado["trocas_criadas"][0]["id"], primeiro_id)
        self.assertEqual(segundo_resultado["erros"], [])

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(len(trocas), 1)

    def test_push_troca_com_plantao_recebido_no_mesmo_lote_via_uuid(self):
        headers, _ = self.criar_usuario("push_troca_recebido@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])
        agora = _iso(datetime.now(UTC))

        payload = {
            "trocas": [{
                "uuid_cliente": "troca-com-recebido-1",
                "plantao_transferido_id": plantao["id"],
                "transferido_para": {"nome": "Beltrano", "telefone": "11977776666"},
                "plantao_recebido": {
                    "uuid_cliente": "plantao-recebido-uuid-1",
                    "local_id": local["id"],
                    "data_inicio": "2026-08-01",
                    "horario_inicio": "08:00:00",
                    "data_fim": "2026-08-01",
                    "horario_fim": "20:00:00",
                    "valor": "700.00",
                    "recebido_de": {"nome": "Beltrano", "telefone": "11977776666"},
                },
                "atualizado_em": agora,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["trocas_criadas"]), 1)
        self.assertEqual(data["erros"], [])

        recebidos = [p for p in self.client.get("/api/plantoes", headers=headers).json()["data"] if p.get("recebido_via_transferencia")]
        self.assertEqual(len(recebidos), 1)
        self.assertEqual(recebidos[0]["uuid_cliente"], "plantao-recebido-uuid-1")

    def test_push_troca_sem_referencia_plantao_retorna_422(self):
        headers, _ = self.criar_usuario("push_troca_invalida@example.com")
        payload = {
            "trocas": [{
                "uuid_cliente": "troca-sem-plantao",
                "transferido_para": {"nome": "Fulano", "telefone": "11999990000"},
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        # AUD-007 (contrato K3): o item inválido vira erro 422 DELE, não do lote.
        self.assertEqual(response.status_code, 200, response.text)
        erros = response.json()["data"]["erros"]
        self.assertEqual([(e["uuid_cliente"], e["status_code"]) for e in erros], [("troca-sem-plantao", 422)])

    def test_push_troca_deletada_gera_tombstone_visivel_no_pull(self):
        headers, usuario_id = self.criar_usuario("push_troca_delete@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criacao = self.client.post("/api/sync/push", headers=headers, json={"trocas": [{
            "uuid_cliente": "troca-delete-1",
            "plantao_transferido_id": plantao["id"],
            "transferido_para": {"nome": "Fulano", "telefone": "11999990000"},
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})
        troca_id = criacao.json()["data"]["trocas_criadas"][0]["id"]

        marcador = datetime.now(UTC)
        delete_payload = {
            "trocas": [{
                "uuid_cliente": "troca-delete-1",
                "id": troca_id,
                "plantao_transferido_id": plantao["id"],
                "transferido_para": {"nome": "Fulano", "telefone": "11999990000"},
                "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
                "deletado": True,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=delete_payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["trocas_deletadas"]), 1)

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(trocas, [])

        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": _iso(marcador)})
        exclusoes = [e for e in pull.json()["data"]["exclusoes"] if e["entidade"] == "troca"]
        self.assertEqual(len(exclusoes), 1)
        self.assertEqual(exclusoes[0]["id"], troca_id)
        self.assertEqual(exclusoes[0]["uuid_cliente"], "troca-delete-1")

    # ---------------------------------------------------------------
    # pull: troca precisa refletir atualização pós-criação (bug corrigido nesta entrega —
    # antes o filtro usava criado_em e uma troca alterada depois de criada nunca aparecia
    # numa sincronização incremental de outro dispositivo)
    # ---------------------------------------------------------------

    def test_pull_troca_usa_atualizado_em_e_nao_criado_em(self):
        headers, _ = self.criar_usuario("pull_troca_atualizado@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criacao = self.client.post("/api/sync/push", headers=headers, json={"trocas": [{
            "uuid_cliente": "troca-atualizada-1",
            "plantao_transferido_id": plantao["id"],
            "transferido_para": {"nome": "Fulano", "telefone": "11999990000"},
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})
        troca_id = criacao.json()["data"]["trocas_criadas"][0]["id"]

        marco = datetime.now(UTC)

        # Simula uma alteração futura na troca sem tocar criado_em, para provar que o pull
        # usa atualizado_em (a mesma coluna já usada para locais/plantões) como cursor —
        # não criado_em, que ficaria parado no momento da criação.
        from app.core.database import SessionLocal
        from app.models import Troca

        with SessionLocal() as db:
            troca = db.get(Troca, troca_id)
            troca.atualizado_em = datetime.now(UTC) + timedelta(hours=1)
            db.commit()

        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": _iso(marco)})
        self.assertEqual(pull.status_code, 200, pull.text)
        trocas = pull.json()["data"]["trocas"]
        self.assertEqual(len(trocas), 1)
        self.assertEqual(trocas[0]["id"], troca_id)

    # ---------------------------------------------------------------
    # push/pull: bonificação
    # ---------------------------------------------------------------

    def test_push_bonificacao_cria_atualiza_e_deleta(self):
        headers, _ = self.criar_usuario("push_bonificacao@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criacao = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-uuid-1",
            "plantao_id": plantao["id"],
            "valor": "100.00",
            "descricao": "Adicional noturno",
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})
        self.assertEqual(criacao.status_code, 200, criacao.text)
        criacao_data = criacao.json()["data"]
        self.assertEqual(len(criacao_data["bonificacoes_criadas"]), 1)
        bonificacao_id = criacao_data["bonificacoes_criadas"][0]["id"]

        atualizacao = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-uuid-1",
            "id": bonificacao_id,
            "plantao_id": plantao["id"],
            "valor": "150.00",
            "descricao": "Adicional noturno revisado",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
        }]})
        self.assertEqual(atualizacao.status_code, 200, atualizacao.text)
        self.assertEqual(len(atualizacao.json()["data"]["bonificacoes_atualizadas"]), 1)

        bonificacoes = self.client.get("/api/bonificacoes", headers=headers, params={"plantao_id": plantao["id"]}).json()["data"]
        self.assertEqual(len(bonificacoes), 1)
        self.assertEqual(str(bonificacoes[0]["valor"]), "150.00")
        self.assertEqual(bonificacoes[0]["uuid_cliente"], "bonificacao-uuid-1")

        marcador = datetime.now(UTC)
        exclusao = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-uuid-1",
            "id": bonificacao_id,
            "plantao_id": plantao["id"],
            "valor": "150.00",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=2)),
            "deletado": True,
        }]})
        self.assertEqual(exclusao.status_code, 200, exclusao.text)
        self.assertEqual(len(exclusao.json()["data"]["bonificacoes_deletadas"]), 1)

        bonificacoes_apos = self.client.get("/api/bonificacoes", headers=headers, params={"plantao_id": plantao["id"]}).json()["data"]
        self.assertEqual(bonificacoes_apos, [])

        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": _iso(marcador)})
        exclusoes = [e for e in pull.json()["data"]["exclusoes"] if e["entidade"] == "bonificacao"]
        self.assertEqual(len(exclusoes), 1)
        self.assertEqual(exclusoes[0]["id"], bonificacao_id)

    def test_push_bonificacao_conflito_quando_servidor_mais_recente(self):
        headers, _ = self.criar_usuario("push_bonificacao_conflito@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criada = self.client.post("/api/bonificacoes", headers=headers, json={
            "plantao_id": plantao["id"], "valor": "200.00", "descricao": "Original",
        }).json()["data"]

        payload = {
            "bonificacoes": [{
                "uuid_cliente": "bonificacao-conflito-1",
                "id": criada["id"],
                "plantao_id": plantao["id"],
                "valor": "1.00",
                "atualizado_em": _iso(datetime.now(UTC) - timedelta(days=1)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["conflitos"]), 1)
        self.assertEqual(data["bonificacoes_atualizadas"], [])

        estado = self.client.get("/api/bonificacoes", headers=headers, params={"plantao_id": plantao["id"]}).json()["data"][0]
        self.assertEqual(str(estado["valor"]), "200.00")

    def test_pull_bonificacao_aparece_isolada_e_dentro_do_plantao(self):
        headers, _ = self.criar_usuario("pull_bonificacao@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-pull-1",
            "plantao_id": plantao["id"],
            "valor": "80.00",
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})

        pull = self.client.get("/api/sync/pull", headers=headers)
        self.assertEqual(pull.status_code, 200, pull.text)
        pull_data = pull.json()["data"]

        bonificacoes_top = pull_data["bonificacoes"]
        self.assertEqual(len(bonificacoes_top), 1)
        self.assertEqual(bonificacoes_top[0]["uuid_cliente"], "bonificacao-pull-1")

        plantao_pull = next(p for p in pull_data["plantoes"] if p["id"] == plantao["id"])
        self.assertEqual(len(plantao_pull["bonificacoes"]), 1)
        self.assertEqual(plantao_pull["bonificacoes"][0]["uuid_cliente"], "bonificacao-pull-1")


    # ---------------------------------------------------------------
    # push: ressurreição de registro excluído (bug corrigido nesta entrega — um dispositivo
    # que ainda não processou o tombstone de exclusão de outro dispositivo não pode
    # recriar o registro só porque ainda tem uma cópia local desatualizada dele)
    # ---------------------------------------------------------------

    def test_push_local_excluido_nao_ressuscita_via_reenvio_de_dispositivo_desatualizado(self):
        headers, _ = self.criar_usuario("push_local_resurrect@example.com")
        criacao = self.client.post("/api/sync/push", headers=headers, json={"locais": [{
            "uuid_cliente": "local-resurrect-1",
            "nome_local": "Hospital Fantasma",
            "data_inicio": "2026-03-01",
            "data_final": "2026-03-31",
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})
        self.assertEqual(criacao.status_code, 200, criacao.text)
        local_id = criacao.json()["data"]["locais_criados"][0]["id"]

        exclusao = self.client.post("/api/sync/push", headers=headers, json={"locais": [{
            "uuid_cliente": "local-resurrect-1",
            "id": local_id,
            "nome_local": "Hospital Fantasma",
            "data_final": "2026-03-31",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
            "deletado": True,
        }]})
        self.assertEqual(exclusao.status_code, 200, exclusao.text)
        self.assertEqual(len(exclusao.json()["data"]["locais_deletados"]), 1)

        # Um segundo dispositivo, que ainda não processou o tombstone, reenvia o mesmo
        # local (upsert, sem id — só conhece o uuid_cliente) achando que ainda precisa
        # subir ao servidor.
        reenvio = self.client.post("/api/sync/push", headers=headers, json={"locais": [{
            "uuid_cliente": "local-resurrect-1",
            "nome_local": "Hospital Fantasma",
            "data_inicio": "2026-03-01",
            "data_final": "2026-03-31",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=2)),
        }]})
        self.assertEqual(reenvio.status_code, 200, reenvio.text)
        data = reenvio.json()["data"]
        self.assertEqual(data["locais_criados"], [])
        self.assertEqual(len(data["erros"]), 1)
        self.assertEqual(data["erros"][0]["status_code"], 410)
        self.assertEqual(data["erros"][0]["uuid_cliente"], "local-resurrect-1")

        locais = self.client.get("/api/locais", headers=headers).json()["data"]
        self.assertEqual(locais, [])

    def test_push_plantao_excluido_nao_ressuscita_via_reenvio_de_dispositivo_desatualizado(self):
        headers, _ = self.criar_usuario("push_plantao_resurrect@example.com")
        local = self.criar_local(headers)

        criacao = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": "plantao-resurrect-1",
            "local_id": local["id"],
            "data_inicio": "2026-04-05T08:00:00",
            "data_fim": "2026-04-05T16:00:00",
            "valor": "300.00",
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})
        self.assertEqual(criacao.status_code, 200, criacao.text)
        plantao_id = criacao.json()["data"]["plantoes_criados"][0]["id"]

        exclusao = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": "plantao-resurrect-1",
            "id": plantao_id,
            "local_id": local["id"],
            "data_inicio": "2026-04-05T08:00:00",
            "data_fim": "2026-04-05T16:00:00",
            "valor": "300.00",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
            "deletado": True,
        }]})
        self.assertEqual(exclusao.status_code, 200, exclusao.text)
        self.assertEqual(len(exclusao.json()["data"]["plantoes_deletados"]), 1)

        reenvio = self.client.post("/api/sync/push", headers=headers, json={"plantoes": [{
            "uuid_cliente": "plantao-resurrect-1",
            "local_id": local["id"],
            "data_inicio": "2026-04-05T08:00:00",
            "data_fim": "2026-04-05T16:00:00",
            "valor": "300.00",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=2)),
        }]})
        self.assertEqual(reenvio.status_code, 200, reenvio.text)
        data = reenvio.json()["data"]
        self.assertEqual(data["plantoes_criados"], [])
        self.assertEqual(len(data["erros"]), 1)
        self.assertEqual(data["erros"][0]["status_code"], 410)
        self.assertEqual(data["erros"][0]["uuid_cliente"], "plantao-resurrect-1")

    def test_push_bonificacao_excluida_nao_ressuscita_via_reenvio_de_dispositivo_desatualizado(self):
        headers, _ = self.criar_usuario("push_bonificacao_resurrect@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criacao = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-resurrect-1",
            "plantao_id": plantao["id"],
            "valor": "50.00",
            "atualizado_em": _iso(datetime.now(UTC)),
        }]})
        self.assertEqual(criacao.status_code, 200, criacao.text)
        bonificacao_id = criacao.json()["data"]["bonificacoes_criadas"][0]["id"]

        exclusao = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-resurrect-1",
            "id": bonificacao_id,
            "plantao_id": plantao["id"],
            "valor": "50.00",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
            "deletado": True,
        }]})
        self.assertEqual(exclusao.status_code, 200, exclusao.text)
        self.assertEqual(len(exclusao.json()["data"]["bonificacoes_deletadas"]), 1)

        reenvio = self.client.post("/api/sync/push", headers=headers, json={"bonificacoes": [{
            "uuid_cliente": "bonificacao-resurrect-1",
            "plantao_id": plantao["id"],
            "valor": "50.00",
            "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=2)),
        }]})
        self.assertEqual(reenvio.status_code, 200, reenvio.text)
        data = reenvio.json()["data"]
        self.assertEqual(data["bonificacoes_criadas"], [])
        self.assertEqual(len(data["erros"]), 1)
        self.assertEqual(data["erros"][0]["status_code"], 410)
        self.assertEqual(data["erros"][0]["uuid_cliente"], "bonificacao-resurrect-1")


if __name__ == "__main__":
    unittest.main()
