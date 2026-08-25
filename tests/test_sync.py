import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_sync.db"

from fastapi.testclient import TestClient

from app.main import app


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_sync.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def criar_usuario(self, email):
        response = self.client.post("/api/usuarios", json={
            "nome": "Usuário Teste", "email": email, "senha": "SenhaForte123",
        })
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()["data"]
        return {"Authorization": f"Bearer {data['access_token']}"}, data["usuario"]["id"]

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
        self.assertEqual(response.status_code, 422)

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
    # push: bonificação (sempre aninhada em um item de plantão)
    # ---------------------------------------------------------------

    def test_push_cria_plantao_com_bonificacao_aninhada(self):
        headers, _ = self.criar_usuario("push_bonificacao_criar@example.com")
        local = self.criar_local(headers)
        agora = _iso(datetime.now(UTC))
        payload = {
            "plantoes": [{
                "uuid_cliente": "plantao-bonificacao-1",
                "local_id": local["id"],
                "data_inicio": "2026-04-01T08:00:00",
                "data_fim": "2026-04-01T16:00:00",
                "valor": "600.00",
                "atualizado_em": agora,
                "bonificacoes": [{
                    "uuid_cliente": "bonificacao-uuid-1",
                    "valor": "50.00",
                    "descricao": "Plantão extra",
                    "atualizado_em": agora,
                }],
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["plantoes_criados"]), 1)
        self.assertEqual(len(data["bonificacoes_criadas"]), 1)
        self.assertEqual(data["erros"], [])

        plantao_id = data["plantoes_criados"][0]["id"]
        bonificacoes = self.client.get(
            "/api/bonificacoes", headers=headers, params={"plantao_id": plantao_id}
        ).json()["data"]
        self.assertEqual(len(bonificacoes), 1)
        self.assertEqual(str(bonificacoes[0]["valor"]), "50.00")

    def test_push_atualiza_e_deleta_bonificacao_existente(self):
        headers, _ = self.criar_usuario("push_bonificacao_ciclo@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])
        base = datetime.now(UTC)

        def push_plantao(bonificacoes, minutos):
            payload = {
                "plantoes": [{
                    "uuid_cliente": "plantao-bonificacao-ciclo",
                    "id": plantao["id"],
                    "local_id": local["id"],
                    "data_inicio": "2026-07-17T07:00:00",
                    "data_fim": "2026-07-17T19:00:00",
                    "valor": "1200.00",
                    "atualizado_em": _iso(base + timedelta(minutes=minutos)),
                    "bonificacoes": bonificacoes,
                }],
            }
            response = self.client.post("/api/sync/push", headers=headers, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["data"]

        criacao = push_plantao([{
            "uuid_cliente": "bonificacao-ciclo-1",
            "valor": "80.00",
            "atualizado_em": _iso(base),
        }], minutos=1)
        self.assertEqual(len(criacao["bonificacoes_criadas"]), 1)
        bonificacao_id = criacao["bonificacoes_criadas"][0]["id"]

        atualizacao = push_plantao([{
            "uuid_cliente": "bonificacao-ciclo-1",
            "id": bonificacao_id,
            "valor": "95.00",
            "atualizado_em": _iso(base + timedelta(minutes=2)),
        }], minutos=3)
        self.assertEqual(len(atualizacao["bonificacoes_atualizadas"]), 1)
        bonificacoes = self.client.get(
            "/api/bonificacoes", headers=headers, params={"plantao_id": plantao["id"]}
        ).json()["data"]
        self.assertEqual(str(bonificacoes[0]["valor"]), "95.00")

        exclusao = push_plantao([{
            "uuid_cliente": "bonificacao-ciclo-1",
            "id": bonificacao_id,
            "valor": "95.00",
            "atualizado_em": _iso(base + timedelta(minutes=4)),
            "deletado": True,
        }], minutos=5)
        self.assertEqual(len(exclusao["bonificacoes_deletadas"]), 1)
        bonificacoes = self.client.get(
            "/api/bonificacoes", headers=headers, params={"plantao_id": plantao["id"]}
        ).json()["data"]
        self.assertEqual(bonificacoes, [])

    def test_push_bonificacao_conflito_mais_antiga_nao_e_aplicada(self):
        headers, _ = self.criar_usuario("push_bonificacao_conflito@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criacao = self.client.post("/api/sync/push", headers=headers, json={
            "plantoes": [{
                "uuid_cliente": "plantao-bonificacao-conflito",
                "id": plantao["id"],
                "local_id": local["id"],
                "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00",
                "valor": "1200.00",
                "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
                "bonificacoes": [{
                    "uuid_cliente": "bonificacao-conflito-1",
                    "valor": "80.00",
                    "atualizado_em": _iso(datetime.now(UTC)),
                }],
            }],
        }).json()["data"]
        bonificacao_id = criacao["bonificacoes_criadas"][0]["id"]

        # Edita "ao vivo" pra empurrar o atualizado_em do servidor pra frente.
        self.client.patch(f"/api/bonificacoes/{bonificacao_id}", headers=headers, json={"valor": "120.00"})

        payload = {
            "plantoes": [{
                "uuid_cliente": "plantao-bonificacao-conflito",
                "id": plantao["id"],
                "local_id": local["id"],
                "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00",
                "valor": "1200.00",
                "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=2)),
                "bonificacoes": [{
                    "uuid_cliente": "bonificacao-conflito-1",
                    "id": bonificacao_id,
                    "valor": "1.00",
                    "atualizado_em": _iso(datetime.now(UTC) - timedelta(days=1)),
                }],
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        conflitos_bonificacao = [c for c in data["conflitos"] if c["entidade"] == "bonificacao"]
        self.assertEqual(len(conflitos_bonificacao), 1)
        self.assertEqual(data["bonificacoes_atualizadas"], [])

        bonificacoes = self.client.get(
            "/api/bonificacoes", headers=headers, params={"plantao_id": plantao["id"]}
        ).json()["data"]
        self.assertEqual(str(bonificacoes[0]["valor"]), "120.00")

    # ---------------------------------------------------------------
    # push: troca (transferência manual — nome/telefone preenchidos à mão)
    # ---------------------------------------------------------------

    def test_push_cria_troca_transferencia_manual(self):
        headers, usuario_id = self.criar_usuario("push_troca_criar@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        payload = {
            "trocas": [{
                "uuid_cliente": "troca-uuid-1",
                "plantao_transferido_id": plantao["id"],
                "nome_transferir": "Fulano de Tal",
                "telefone_transferir": "11999999999",
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["trocas_criadas"]), 1)
        self.assertEqual(data["erros"], [])

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(len(trocas), 1)
        self.assertEqual(trocas[0]["nome_transferir"], "Fulano de Tal")
        self.assertEqual(trocas[0]["plantao_id_transferido"], plantao["id"])

    def test_push_cria_plantao_e_troca_no_mesmo_lote_via_uuid(self):
        headers, usuario_id = self.criar_usuario("push_troca_mesmo_lote@example.com")
        local = self.criar_local(headers)
        agora = _iso(datetime.now(UTC))

        payload = {
            "plantoes": [{
                "uuid_cliente": "plantao-para-troca",
                "local_id": local["id"],
                "data_inicio": "2026-05-01T08:00:00",
                "data_fim": "2026-05-01T16:00:00",
                "valor": "700.00",
                "atualizado_em": agora,
            }],
            "trocas": [{
                "uuid_cliente": "troca-uuid-2",
                "plantao_transferido_uuid_cliente": "plantao-para-troca",
                "nome_transferir": "Beltrano",
                "telefone_transferir": "11888887777",
                "atualizado_em": agora,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["plantoes_criados"]), 1)
        self.assertEqual(len(data["trocas_criadas"]), 1)
        self.assertEqual(data["erros"], [])

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(trocas[0]["plantao_id_transferido"], data["plantoes_criados"][0]["id"])

    def test_push_deleta_troca_gera_tombstone(self):
        headers, usuario_id = self.criar_usuario("push_troca_deletar@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criacao = self.client.post("/api/sync/push", headers=headers, json={
            "trocas": [{
                "uuid_cliente": "troca-uuid-deletar",
                "plantao_transferido_id": plantao["id"],
                "nome_transferir": "Fulano",
                "telefone_transferir": "11999999999",
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        }).json()["data"]
        troca_id = criacao["trocas_criadas"][0]["id"]

        marcador = datetime.now(UTC)
        payload = {
            "trocas": [{
                "uuid_cliente": "troca-uuid-deletar",
                "id": troca_id,
                "nome_transferir": "Fulano",
                "telefone_transferir": "11999999999",
                "atualizado_em": _iso(datetime.now(UTC) + timedelta(minutes=1)),
                "deletado": True,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["trocas_deletadas"]), 1)

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(trocas, [])

        pull = self.client.get("/api/sync/pull", headers=headers, params={"since": _iso(marcador)})
        self.assertEqual(pull.status_code, 200, pull.text)
        exclusoes = [e for e in pull.json()["data"]["exclusoes"] if e["entidade"] == "troca"]
        self.assertEqual(len(exclusoes), 1)
        self.assertEqual(exclusoes[0]["id"], troca_id)

    def test_push_troca_conflito_mais_antiga_nao_e_aplicada(self):
        headers, usuario_id = self.criar_usuario("push_troca_conflito@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        criada = self.client.post("/api/trocas", headers=headers, json={
            "plantao_transferido_id": plantao["id"],
            "transferido_para": {"nome": "Ciclano", "telefone": "11888888888"},
        })
        self.assertEqual(criada.status_code, 201, criada.text)
        troca_id = criada.json()["data"]["troca_id"]

        payload = {
            "trocas": [{
                "uuid_cliente": "troca-conflito-1",
                "id": troca_id,
                "nome_transferir": "Ciclano",
                "telefone_transferir": "11888888888",
                "atualizado_em": _iso(datetime.now(UTC) - timedelta(days=1)),
                "deletado": True,
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        conflitos_troca = [c for c in data["conflitos"] if c["entidade"] == "troca"]
        self.assertEqual(len(conflitos_troca), 1)
        self.assertEqual(data["trocas_deletadas"], [])

        trocas = self.client.get("/api/trocas", headers=headers, params={"usuario_id": usuario_id}).json()["data"]
        self.assertEqual(len(trocas), 1)

    def test_push_troca_plantao_ja_transferido_retorna_erro(self):
        headers, _ = self.criar_usuario("push_troca_ja_transferida@example.com")
        local = self.criar_local(headers)
        plantao = self.criar_plantao(headers, local["id"])

        self.client.post("/api/sync/push", headers=headers, json={
            "trocas": [{
                "uuid_cliente": "troca-uuid-primeira",
                "plantao_transferido_id": plantao["id"],
                "nome_transferir": "Fulano",
                "telefone_transferir": "11999999999",
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        })

        payload = {
            "trocas": [{
                "uuid_cliente": "troca-uuid-segunda",
                "plantao_transferido_id": plantao["id"],
                "nome_transferir": "Outra Pessoa",
                "telefone_transferir": "11777777777",
                "atualizado_em": _iso(datetime.now(UTC)),
            }],
        }
        response = self.client.post("/api/sync/push", headers=headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(data["trocas_criadas"], [])
        self.assertEqual(len(data["erros"]), 1)
        self.assertEqual(data["erros"][0]["entidade"], "troca")

    # ---------------------------------------------------------------
    # pull: filtro por since
    # ---------------------------------------------------------------

    def test_pull_retorna_apenas_alteracoes_desde_since(self):
        headers, _ = self.criar_usuario("pull_since@example.com")
        local = self.criar_local(headers)
        self.criar_plantao(headers, local["id"], data_inicio="2026-03-01T08:00:00", data_fim="2026-03-01T16:00:00")

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


if __name__ == "__main__":
    unittest.main()
