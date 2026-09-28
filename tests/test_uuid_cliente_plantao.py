"""uuid_cliente de plantão criado pelo servidor.

Plantão que nasce NO SERVIDOR não tem dispositivo por trás para gerar o
identificador: é o caso do plantão recebido numa troca/transferência. Esses
caminhos gravavam uuid_cliente nulo — o Postgres aceita vários NULL numa
coluna UNIQUE, então nada reclamava.

O app, recebendo nulo no pull, gerava um uuid aleatório NOVO a cada ciclo e
o escrevia por cima da linha. Como a fila de sincronização referencia a linha
por uuid_cliente, a edição de um plantão recebido ficava órfã: o push não
achava a linha, pulava em silêncio, e a entrada nunca saía da fila — a
alteração não chegava ao servidor e o contador de pendências nunca zerava,
o que ainda impedia o logout.
"""

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import Plantao


class TestUuidClientePlantao(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def criar_usuario(self):
        sufixo = uuid4().hex[:8]
        resposta = self.client.post("/api/usuarios", json={
            "nome": "Usuário Uuid",
            "email": f"uuid-{sufixo}@example.com",
            "senha": "SenhaForte123",
        })
        self.assertEqual(resposta.status_code, 201, resposta.text)
        return {"Authorization": f"Bearer {resposta.json()['data']['access_token']}"}

    def criar_local_e_plantao(self, headers):
        local = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Uuid",
            "data_inicio": "2026-01-01",
            "data_final": "2026-01-31",
        }).json()["data"]
        plantao = self.client.post("/api/plantoes", headers=headers, json={
            "local_id": local["id"], "data_inicio": "2026-07-17",
            "horario_inicio": "07:00", "data_fim": "2026-07-17",
            "horario_fim": "19:00", "valor": "1200.00",
        }).json()["data"]
        return local, plantao

    def payload_recebido(self, local_id, uuid_cliente=None):
        recebido = {
            "local_id": local_id, "data_inicio": "2026-07-20",
            "horario_inicio": "07:00", "data_fim": "2026-07-20",
            "horario_fim": "19:00", "valor": 800, "sobreaviso": False,
            "recebido_de": {"nome": "João", "telefone": "11999999999"},
        }
        if uuid_cliente is not None:
            recebido["uuid_cliente"] = uuid_cliente
        return recebido

    def criar_troca(self, headers, plantao_id, recebido):
        resposta = self.client.post("/api/trocas", headers=headers, json={
            "plantao_transferido_id": plantao_id,
            "transferido_para": {
                "nome": "Maria", "telefone": "+5511999999999",
                "observacao": "Cobertura",
            },
            "plantao_recebido": recebido,
        })
        self.assertEqual(resposta.status_code, 201, resposta.text)
        return resposta.json()["data"]

    def uuid_no_banco(self, plantao_id):
        with SessionLocal() as db:
            return db.get(Plantao, plantao_id).uuid_cliente

    def test_plantao_recebido_sem_uuid_do_cliente_recebe_um_do_servidor(self):
        headers = self.criar_usuario()
        local, plantao = self.criar_local_e_plantao(headers)

        # O cliente não manda uuid_cliente — é o caso real do app que aceita
        # uma troca pela rota online, e também o do payload de sync que vem
        # sem o campo (TrocaService repassa None).
        data = self.criar_troca(
            headers, plantao["id"], self.payload_recebido(local["id"])
        )

        uuid_gravado = self.uuid_no_banco(data["plantao_recebido"]["id"])

        self.assertIsNotNone(
            uuid_gravado,
            "plantão recebido sem uuid_cliente faz o app inventar um novo a "
            "cada pull, trocando a identidade da linha e prendendo a fila",
        )
        self.assertNotEqual(uuid_gravado, "")

    def test_uuid_enviado_pelo_cliente_e_preservado(self):
        # A outra metade da invariante: o default não pode atropelar o
        # identificador que o dispositivo já escolheu, senão o app perde a
        # correlação entre o que criou offline e o id que o servidor atribuiu.
        headers = self.criar_usuario()
        local, plantao = self.criar_local_e_plantao(headers)
        escolhido = str(uuid4())

        data = self.criar_troca(
            headers, plantao["id"],
            self.payload_recebido(local["id"], uuid_cliente=escolhido),
        )

        self.assertEqual(self.uuid_no_banco(data["plantao_recebido"]["id"]), escolhido)

    def test_dois_plantoes_recebidos_nao_colidem(self):
        # uuid_cliente é UNIQUE. Se o default gerasse valor fixo ou repetido,
        # a segunda troca falharia — e o modo de falha seria um 500 no meio do
        # fluxo de recebimento, não um dado errado silencioso.
        headers = self.criar_usuario()
        local, primeiro = self.criar_local_e_plantao(headers)
        _, segundo = self.criar_local_e_plantao(headers)

        um = self.criar_troca(headers, primeiro["id"], self.payload_recebido(local["id"]))
        dois = self.criar_troca(headers, segundo["id"], self.payload_recebido(local["id"]))

        self.assertNotEqual(
            self.uuid_no_banco(um["plantao_recebido"]["id"]),
            self.uuid_no_banco(dois["plantao_recebido"]["id"]),
        )
