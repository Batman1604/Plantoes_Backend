import os
import unittest
from pathlib import Path


from fastapi.testclient import TestClient

from app.main import app


class TestAmizades(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        db_file = Path("test_amizades.db")
        try:
            if db_file.exists():
                db_file.unlink()
        except PermissionError:
            pass

    def criar_usuario(self, email, telefone=None, nome="Usuário Teste"):
        payload = {"nome": nome, "email": email, "senha": "SenhaForte123"}
        if telefone is not None:
            payload["telefone"] = telefone
        response = self.client.post("/api/usuarios", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()["data"]
        headers = {"Authorization": f"Bearer {data['access_token']}"}
        return headers, data["usuario"]["id"]

    def enviar_amizade(self, headers, destinatario_id):
        return self.client.post(
            "/api/amizades", headers=headers, json={"destinatario_usuario_id": destinatario_id}
        )

    # ---------------------------------------------------------------
    # Busca de usuário por telefone (GAP da seção 1)
    # ---------------------------------------------------------------

    def test_busca_por_telefone_sucesso(self):
        _, alvo_id = self.criar_usuario("alvo_busca@example.com", telefone="11988887777")
        buscador, _ = self.criar_usuario("buscador_busca@example.com")

        response = self.client.get("/api/usuarios/busca", headers=buscador, params={"telefone": "11988887777"})
        self.assertEqual(response.status_code, 200, response.text)
        encontrados = response.json()["data"]
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0]["id"], alvo_id)

    def test_busca_por_telefone_nao_encontrado(self):
        buscador, _ = self.criar_usuario("buscador_naoexiste@example.com")
        response = self.client.get("/api/usuarios/busca", headers=buscador, params={"telefone": "11900000000"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"], [])

    def test_busca_por_telefone_com_mascara_e_ddi(self):
        _, alvo_id = self.criar_usuario("alvo_mascara@example.com", telefone="11977776666")
        buscador, _ = self.criar_usuario("buscador_mascara@example.com")

        variantes = ["+55 (11) 97777-6666", "5511977776666", "(11) 97777-6666"]
        for variante in variantes:
            response = self.client.get("/api/usuarios/busca", headers=buscador, params={"telefone": variante})
            self.assertEqual(response.status_code, 200, response.text)
            encontrados = response.json()["data"]
            self.assertEqual(len(encontrados), 1, f"falhou para variante {variante!r}: {encontrados}")
            self.assertEqual(encontrados[0]["id"], alvo_id)

    def test_busca_por_telefone_nao_retorna_o_proprio_usuario(self):
        headers, meu_id = self.criar_usuario("eu_mesmo_telefone@example.com", telefone="11955554444")
        response = self.client.get("/api/usuarios/busca", headers=headers, params={"telefone": "11955554444"})
        self.assertEqual(response.status_code, 200, response.text)
        ids = [u["id"] for u in response.json()["data"]]
        self.assertNotIn(meu_id, ids)

    def test_busca_sem_email_e_sem_telefone_retorna_400(self):
        headers, _ = self.criar_usuario("busca_vazia@example.com")
        response = self.client.get("/api/usuarios/busca", headers=headers)
        self.assertEqual(response.status_code, 400)

    def test_busca_com_email_e_telefone_juntos_retorna_400(self):
        headers, _ = self.criar_usuario("busca_ambigua@example.com")
        response = self.client.get(
            "/api/usuarios/busca", headers=headers, params={"email": "x@example.com", "telefone": "11999999999"}
        )
        self.assertEqual(response.status_code, 400)

    # ---------------------------------------------------------------
    # POST /api/amizades
    # ---------------------------------------------------------------

    def test_criar_amizade_normal(self):
        remetente, _ = self.criar_usuario("rem_normal@example.com")
        _, dest_id = self.criar_usuario("dest_normal@example.com")
        response = self.enviar_amizade(remetente, dest_id)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["data"]["status"], "pendente")

    def test_criar_amizade_duplicada_retorna_409(self):
        remetente, _ = self.criar_usuario("rem_dup@example.com")
        _, dest_id = self.criar_usuario("dest_dup@example.com")
        self.assertEqual(self.enviar_amizade(remetente, dest_id).status_code, 201)
        duplicada = self.enviar_amizade(remetente, dest_id)
        self.assertEqual(duplicada.status_code, 409)

    def test_criar_amizade_para_si_mesmo_retorna_400(self):
        headers, meu_id = self.criar_usuario("solo@example.com")
        response = self.enviar_amizade(headers, meu_id)
        self.assertEqual(response.status_code, 400)

    def test_criar_amizade_com_quem_ja_e_amigo_retorna_409(self):
        remetente, _ = self.criar_usuario("rem_amigo@example.com")
        destinatario, dest_id = self.criar_usuario("dest_amigo@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]
        aceitar = self.client.patch(f"/api/amizades/{criada['id']}/aceitar", headers=destinatario)
        self.assertEqual(aceitar.status_code, 200, aceitar.text)

        nova_tentativa = self.enviar_amizade(remetente, dest_id)
        self.assertEqual(nova_tentativa.status_code, 409)

    # ---------------------------------------------------------------
    # PATCH /api/amizades/:id/aceitar e /rejeitar
    # ---------------------------------------------------------------

    def test_aceitar_amizade_sucesso(self):
        remetente, _ = self.criar_usuario("rem_aceitar@example.com")
        destinatario, dest_id = self.criar_usuario("dest_aceitar@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]

        response = self.client.patch(f"/api/amizades/{criada['id']}/aceitar", headers=destinatario)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["status"], "aceito")

    def test_aceitar_amizade_por_quem_nao_e_destinatario_retorna_403(self):
        remetente, _ = self.criar_usuario("rem_403aceitar@example.com")
        _, dest_id = self.criar_usuario("dest_403aceitar@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]

        response = self.client.patch(f"/api/amizades/{criada['id']}/aceitar", headers=remetente)
        self.assertEqual(response.status_code, 403)

    def test_rejeitar_amizade_sucesso(self):
        remetente, _ = self.criar_usuario("rem_rejeitar@example.com")
        destinatario, dest_id = self.criar_usuario("dest_rejeitar@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]

        response = self.client.patch(f"/api/amizades/{criada['id']}/rejeitar", headers=destinatario)
        self.assertEqual(response.status_code, 200, response.text)

    def test_rejeitar_amizade_por_quem_nao_e_destinatario_retorna_403(self):
        remetente, _ = self.criar_usuario("rem_403rejeitar@example.com")
        _, dest_id = self.criar_usuario("dest_403rejeitar@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]

        response = self.client.patch(f"/api/amizades/{criada['id']}/rejeitar", headers=remetente)
        self.assertEqual(response.status_code, 403)

    # ---------------------------------------------------------------
    # DELETE /api/amizades/:id (aceita e pendente)
    # ---------------------------------------------------------------

    def test_cancelar_amizade_pendente_pelo_remetente_sucesso(self):
        remetente, _ = self.criar_usuario("rem_cancelar@example.com")
        _, dest_id = self.criar_usuario("dest_cancelar@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]

        response = self.client.delete(f"/api/amizades/{criada['id']}", headers=remetente)
        self.assertEqual(response.status_code, 204, response.text)

    def test_cancelar_amizade_pendente_pelo_destinatario_retorna_403(self):
        remetente, _ = self.criar_usuario("rem_403cancelar@example.com")
        destinatario, dest_id = self.criar_usuario("dest_403cancelar@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]

        response = self.client.delete(f"/api/amizades/{criada['id']}", headers=destinatario)
        self.assertEqual(response.status_code, 403)

    def test_deletar_amizade_inexistente_retorna_404(self):
        headers, _ = self.criar_usuario("qualquer_404@example.com")
        response = self.client.delete("/api/amizades/999999", headers=headers)
        self.assertEqual(response.status_code, 404)

    def test_remover_amizade_aceita_por_qualquer_lado(self):
        remetente, _ = self.criar_usuario("rem_aceita_del@example.com")
        destinatario, dest_id = self.criar_usuario("dest_aceita_del@example.com")
        criada = self.enviar_amizade(remetente, dest_id).json()["data"]
        self.client.patch(f"/api/amizades/{criada['id']}/aceitar", headers=destinatario)

        response = self.client.delete(f"/api/amizades/{criada['id']}", headers=destinatario)
        self.assertEqual(response.status_code, 204, response.text)

    # ---------------------------------------------------------------
    # GET /api/amizades — escopo e conteúdo
    # ---------------------------------------------------------------

    def test_listar_amizades_retorna_apenas_do_usuario_autenticado(self):
        a, a_id = self.criar_usuario("a_escopo@example.com")
        b, b_id = self.criar_usuario("b_escopo@example.com", telefone="11911112222")
        c, c_id = self.criar_usuario("c_escopo@example.com")

        self.enviar_amizade(a, b_id)
        self.enviar_amizade(c, b_id)

        resposta_a = self.client.get("/api/amizades", headers=a)
        self.assertEqual(resposta_a.status_code, 200)
        itens_a = resposta_a.json()["data"]
        self.assertEqual(len(itens_a), 1)
        self.assertEqual(itens_a[0]["amigo"]["id"], b_id)
        self.assertIn("criado_em", itens_a[0])
        # A solicitação de A para B está PENDENTE: B nunca consentiu, então o
        # telefone dele não pode acompanhar a listagem (ver auditoria C-09).
        # Esta asserção afirmava o contrário e, com isso, fixava o vazamento.
        self.assertIsNone(itens_a[0]["amigo"].get("telefone"))

        ids_visiveis_por_a = {item["amigo"]["id"] for item in itens_a}
        self.assertNotIn(c_id, ids_visiveis_por_a)

        resposta_c = self.client.get("/api/amizades", headers=c)
        itens_c = resposta_c.json()["data"]
        self.assertEqual(len(itens_c), 1)
        self.assertEqual(itens_c[0]["amigo"]["id"], b_id)

    # ---------------------------------------------------------------
    # GET /api/solicitacoes-troca — escopo
    # ---------------------------------------------------------------

    def _tornar_amigos(self, headers_a, id_b, headers_b):
        criada = self.enviar_amizade(headers_a, id_b).json()["data"]
        aceitar = self.client.patch(f"/api/amizades/{criada['id']}/aceitar", headers=headers_b)
        self.assertEqual(aceitar.status_code, 200, aceitar.text)

    def _criar_local_e_plantao(self, headers):
        local = self.client.post(
            "/api/locais",
            headers=headers,
            json={"nome_local": "Hospital Teste", "data_inicio": "2026-01-01", "data_final": "2026-01-31"},
        ).json()["data"]
        plantao = self.client.post(
            "/api/plantoes",
            headers=headers,
            json={
                "local_id": local["id"],
                "data_inicio": "2026-09-10",
                "horario_inicio": "07:00",
                "data_fim": "2026-09-10",
                "horario_fim": "19:00",
                "valor": "1000.00",
            },
        ).json()["data"]
        return local, plantao

    def test_listar_solicitacoes_troca_retorna_apenas_do_usuario_autenticado(self):
        a, a_id = self.criar_usuario("a_troca_escopo@example.com")
        b, b_id = self.criar_usuario("b_troca_escopo@example.com")
        outro, _ = self.criar_usuario("outro_troca_escopo@example.com")

        self._tornar_amigos(a, b_id, b)
        _, plantao = self._criar_local_e_plantao(a)

        criada = self.client.post(
            "/api/solicitacoes-troca",
            headers=a,
            json={"destinatario_usuario_id": b_id, "tipo": "transferir", "plantao_transferido_id": plantao["id"]},
        )
        self.assertEqual(criada.status_code, 201, criada.text)

        resposta_outro = self.client.get("/api/solicitacoes-troca", headers=outro)
        self.assertEqual(resposta_outro.json()["data"], [])

        resposta_a = self.client.get("/api/solicitacoes-troca", headers=a)
        itens_a = resposta_a.json()["data"]
        self.assertEqual(len(itens_a), 1)
        self.assertIn("criado_em", itens_a[0])

        resposta_b = self.client.get("/api/solicitacoes-troca", headers=b)
        self.assertEqual(len(resposta_b.json()["data"]), 1)


if __name__ == "__main__":
    unittest.main()
