"""Envio do código de verificação (auditoria C-10).

O stub de SMS registrava a mensagem COMPLETA — com o código de seis dígitos —
em nível INFO, e retornava True. Duas consequências: todo código de verificação
ia parar em texto puro no log de produção, e, sem credenciais de WhatsApp
configuradas, o cadastro respondia sucesso sem que nada fosse entregue. Nenhum
usuário novo conseguia se cadastrar, e o servidor dizia que tinha conseguido.
"""

import logging
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.sms_service import SmsService
from tests.fixtures_usuario import criar_usuario_no_banco


class TestEnvioCodigo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_stub_de_sms_nao_afirma_sucesso(self):
        # Sem provedor real configurado, o envio NÃO aconteceu. Retornar True
        # aqui é o que fazia a rota seguir adiante e responder sucesso.
        self.assertFalse(SmsService._enviar_sms("11999999999", "qualquer mensagem"))

    def test_codigo_nunca_aparece_no_log(self):
        codigo = "654321"
        mensagem = f"Seu código de verificação do app Plantões é: *{codigo}*"

        with self.assertLogs("app.services.sms_service", level=logging.DEBUG) as capturado:
            SmsService._enviar_sms("11999999999", mensagem)

        registro = "\n".join(capturado.output)
        self.assertNotIn(codigo, registro, "o código não pode ser registrado em log")
        self.assertNotIn(mensagem, registro, "o corpo da mensagem não pode ir para o log")

    def test_cadastro_falha_alto_quando_nenhum_canal_esta_configurado(self):
        sufixo = uuid4().hex[:8]
        resposta = self.client.post("/api/auth/cadastro", json={
            "nome": "Usuário Cadastro",
            "email": f"cad-{sufixo}@example.com",
            "telefone": f"119{uuid4().int % 100000000:08d}",
            "senha": "SenhaForte123",
        })

        # Sem WhatsApp configurado no ambiente de teste, o certo é recusar de
        # forma explícita — e não prometer um código que nunca chega.
        self.assertEqual(resposta.status_code, 502, resposta.text)
        self.assertFalse(resposta.json()["success"])

    def test_verificacao_de_codigo_por_whatsapp_sem_credenciais_retorna_falso(self):
        self.assertFalse(SmsService.enviar_codigo_verificacao("11999999999", "123456"))


class TestRotasDeReenvioDizemAVerdade(unittest.TestCase):
    """Resíduo do C-10: as rotas de reenvio respondiam 200 "código enviado"
    com o envio falhando (resultado ignorado, exceção engolida)."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_reenviar_verificacao_telefone_sem_canal_responde_502(self):
        _, headers = criar_usuario_no_banco(telefone=f"119{uuid4().int % 100000000:08d}")
        r = self.client.post("/api/auth/reenviar-verificacao-telefone", headers=headers)
        self.assertEqual(r.status_code, 502, r.text)

    def test_reenviar_verificacao_telefone_com_envio_responde_200(self):
        _, headers = criar_usuario_no_banco(telefone=f"119{uuid4().int % 100000000:08d}")
        with patch("app.services.sms_service.SmsService.enviar_codigo_verificacao", return_value=True):
            r = self.client.post("/api/auth/reenviar-verificacao-telefone", headers=headers)
        self.assertEqual(r.status_code, 200, r.text)

    def test_reenviar_verificacao_email_sem_smtp_responde_502(self):
        _, headers = criar_usuario_no_banco()
        r = self.client.post("/api/auth/reenviar-verificacao", headers=headers)
        self.assertEqual(r.status_code, 502, r.text)

    def test_forgot_password_em_producao_sem_smtp_responde_502(self):
        email = f"prod-{uuid4().hex[:8]}@example.com"
        criar_usuario_no_banco(email)
        with patch("app.services.email_service.settings.environment", "production"):
            r = self.client.post("/api/auth/forgot-password", json={"email": email})
        self.assertEqual(r.status_code, 502, r.text)

    def test_forgot_password_fora_de_producao_entrega_o_codigo_no_log(self):
        """O fluxo de desenvolvimento continua: sem SMTP, o código vai ao log
        do servidor, e isso conta como enviado."""
        email = f"dev-{uuid4().hex[:8]}@example.com"
        criar_usuario_no_banco(email)
        with patch("app.routes.plantao_routes.secrets.randbelow", return_value=246810), \
             self.assertLogs("app.services.email_service", level=logging.WARNING) as capturado:
            r = self.client.post("/api/auth/forgot-password", json={"email": email})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("246810", "\n".join(capturado.output))


if __name__ == "__main__":
    unittest.main()
