"""AUD-011 e AUD-058: cadastro legado removido; e-mail e telefone normalizados."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models import User
from tests.fixtures_usuario import SENHA, criar_usuario_no_banco


class TestCadastroLegadoRemovido(unittest.TestCase):
    def test_post_api_usuarios_nao_cria_conta__AUD011(self):
        client = TestClient(app)
        email = f"legado-{uuid4().hex[:8]}@example.com"
        resposta = client.post("/api/usuarios", json={
            "nome": "Legado", "email": email, "telefone": "11912345678", "senha": SENHA,
        })
        self.assertIn(resposta.status_code, (404, 405), resposta.text)
        with SessionLocal() as db:
            self.assertIsNone(db.query(User).filter(User.email == email).first())


class TestNormalizacaoEmail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_email_gravado_em_minusculas_e_sem_espacos__AUD058(self):
        sufixo = uuid4().hex[:8]
        uid, _ = criar_usuario_no_banco(f"  Maria.{sufixo}@Example.COM ")
        with SessionLocal() as db:
            self.assertEqual(db.get(User, uid).email, f"maria.{sufixo}@example.com")

    def test_login_e_reset_com_outra_caixa__AUD058(self):
        sufixo = uuid4().hex[:8]
        criar_usuario_no_banco(f"joana.{sufixo}@example.com")
        login = self.client.post("/api/auth/login", json={"email": f"Joana.{sufixo}@Example.com", "senha": SENHA})
        self.assertEqual(login.status_code, 200, login.text)
        with patch("app.services.email_service.EmailService.enviar_reset_senha", return_value=True):
            reset = self.client.post("/api/auth/forgot-password", json={"email": f"JOANA.{sufixo}@example.com"})
        self.assertEqual(reset.status_code, 200, reset.text)

    def test_cadastro_com_outra_caixa_conflita__AUD058(self):
        sufixo = uuid4().hex[:8]
        criar_usuario_no_banco(f"pedro.{sufixo}@example.com")
        with patch("app.services.sms_service.SmsService.enviar_codigo_verificacao", return_value=True):
            resposta = self.client.post("/api/auth/cadastro", json={
                "nome": "Pedro", "email": f"Pedro.{sufixo}@example.com",
                "telefone": f"119{uuid4().int % 100000000:08d}", "senha": SENHA,
            })
        self.assertEqual(resposta.status_code, 409, resposta.text)


class TestTelefoneDaTransferencia(unittest.TestCase):
    """`get_by_phone` (usado pela transferência legada) compara o número
    normalizado e só reconhece quem verificou o telefone."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _transferir_para(self, telefone_digitado):
        _, headers = criar_usuario_no_banco()
        local = self.client.post("/api/locais", headers=headers, json={
            "nome_local": "Hospital Tel", "data_inicio": "2026-01-01", "data_final": "2026-01-31",
        }).json()["data"]
        plantao = self.client.post("/api/plantoes", headers=headers, json={
            "local_id": local["id"], "data_inicio": "2026-07-17T07:00:00",
            "data_fim": "2026-07-17T19:00:00", "valor": "1200.00",
        }).json()["data"]
        with patch("app.services.sms_service.SmsService.enviar", return_value=True):
            resposta = self.client.post(f"/api/plantoes/{plantao['id']}/transferir", headers=headers, json={
                "destinatario_nome": "Colega", "destinatario_telefone": telefone_digitado, "canal": "sms",
            })
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()["data"]["destinatario_usuario_id"]

    def test_numero_com_mascara_e_ddi_encontra_o_dono_verificado__AUD058(self):
        numero = f"119{uuid4().int % 100000000:08d}"
        uid, _ = criar_usuario_no_banco(telefone=numero, telefone_verificado=True)
        digitado = f"+55 ({numero[:2]}) {numero[2:7]}-{numero[7:]}"
        self.assertEqual(self._transferir_para(digitado), uid)

    def test_numero_nao_verificado_nao_recebe__AUD011(self):
        numero = f"119{uuid4().int % 100000000:08d}"
        criar_usuario_no_banco(telefone=numero, telefone_verificado=False)
        self.assertIsNone(self._transferir_para(numero))


if __name__ == "__main__":
    unittest.main()
