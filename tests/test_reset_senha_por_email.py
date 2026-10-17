"""Redefinição de senha com o código do Supabase Auth (fase Supabase + Render).

O código do Supabase só passa UMA vez, mas o app confere em
/auth/verificar-codigo-senha e manda o mesmo código de novo em
/auth/reset-password. Conferido lá, ele vira aqui o token de reset de uso
único. O Supabase nunca é chamado: `httpx.post` é substituído."""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import CodigoResetSenha
from tests.fixtures_usuario import criar_usuario_no_banco
from tests.test_cadastro_por_email import CERTO, SupabaseFalso


class TestResetSenhaPorEmail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.supabase = SupabaseFalso()
        for alvo in (
            patch.object(settings, "supabase_url", "https://projeto-de-teste.supabase.co"),
            patch.object(settings, "supabase_anon_key", "anon-de-teste"),
            patch("app.services.email_otp_service.httpx.post", side_effect=self.supabase),
        ):
            alvo.start()
            self.addCleanup(alvo.stop)
        # O canal antigo (SMTP/log) não pode ser usado com o Supabase configurado.
        smtp = patch("app.routes.plantao_routes.EmailService.enviar_reset_senha")
        self.smtp = smtp.start()
        self.addCleanup(smtp.stop)

    def _usuario(self, email):
        usuario_id, _ = criar_usuario_no_banco(email, senha="SenhaAntiga123")
        return usuario_id

    def _esqueci(self, email):
        return self.client.post("/api/auth/forgot-password", json={"email": email})

    def _verificar(self, email, codigo):
        return self.client.post("/api/auth/verificar-codigo-senha", json={"email": email, "code": codigo})

    def _redefinir(self, email, codigo, senha="SenhaNova456"):
        return self.client.post("/api/auth/reset-password", json={"email": email, "code": codigo, "newPassword": senha})

    def _verificacoes(self):
        return self.supabase.caminhos().count("verify")

    def test_fluxo_do_app_verificar_e_depois_redefinir_com_o_mesmo_codigo(self):
        email = "reset-supabase@example.com"
        self._usuario(email)
        resposta = self._esqueci(email)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(self.supabase.chamadas, [("otp", {"email": email, "create_user": True})])
        self.smtp.assert_not_called()

        self.assertEqual(self._verificar(email, "000000").status_code, 400)
        self.assertEqual(self._verificar(email, CERTO).status_code, 200)
        self.assertEqual(self._verificacoes(), 2)

        # O Supabase não aceitaria o mesmo código de novo: daqui em diante é o
        # token de uso único guardado aqui, sem nova consulta.
        self.assertEqual(self._verificar(email, CERTO).status_code, 200)
        reset = self._redefinir(email, CERTO)
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self._verificacoes(), 2)

        self.assertEqual(self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaNova456"}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaAntiga123"}).status_code, 401)
        # Uso único: consumido pelo reset.
        self.assertEqual(self._redefinir(email, CERTO, "OutraSenha789").status_code, 400)

    def test_redefinir_direto_com_o_codigo_original(self):
        email = "reset-direto@example.com"
        self._usuario(email)
        self._esqueci(email)
        self.assertEqual(self._redefinir(email, "111111").status_code, 400)
        self.assertEqual(self._redefinir(email, CERTO).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": email, "senha": "SenhaNova456"}).status_code, 200)

    def test_token_ganha_prazo_para_digitar_a_senha_e_expira(self):
        email = "reset-prazo@example.com"
        usuario_id = self._usuario(email)
        self._esqueci(email)
        with SessionLocal() as db:
            # Código conferido a 30 s de expirar: o token ganha prazo próprio.
            registro = db.query(CodigoResetSenha).filter_by(usuario_id=usuario_id).one()
            registro.expira_em = datetime.now(UTC) + timedelta(seconds=30)
            db.commit()
        self.assertEqual(self._verificar(email, CERTO).status_code, 200)
        with SessionLocal() as db:
            registro = db.query(CodigoResetSenha).filter_by(usuario_id=usuario_id).one()
            self.assertEqual(registro.codigo, CERTO)
            expira_em = registro.expira_em.replace(tzinfo=UTC)
            self.assertGreater(expira_em, datetime.now(UTC) + timedelta(minutes=9))
            self.assertLess(expira_em, datetime.now(UTC) + timedelta(minutes=11))
            registro.expira_em = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self._redefinir(email, CERTO).status_code, 400)

    def test_cinco_erros_matam_o_codigo_do_nosso_lado(self):
        email = "reset-tentativas@example.com"
        self._usuario(email)
        self._esqueci(email)
        for _ in range(5):
            self.assertEqual(self._verificar(email, "000000").status_code, 400)
        antes = self._verificacoes()
        self.assertEqual(self._redefinir(email, CERTO).status_code, 400)
        self.assertEqual(self._verificacoes(), antes)

    def test_email_inexistente_continua_404_sem_envio(self):
        self.assertEqual(self._esqueci("ninguem-supabase@example.com").status_code, 404)
        self.assertEqual(self.supabase.chamadas, [])

    def test_limite_e_falha_do_supabase(self):
        email = "reset-falhas@example.com"
        self._usuario(email)
        self.supabase.status_envio = 429
        self.supabase.corpo_erro = {"msg": "For security purposes, you can only request this after 45 seconds."}
        limite = self._esqueci(email)
        self.assertEqual(limite.status_code, 429, limite.text)
        self.assertIn("Aguarde 45 segundos", limite.json()["message"])

        self.supabase.status_envio = 200
        self.assertEqual(self._esqueci(email).status_code, 200)
        self.supabase.status_verificacao = 500
        with self.assertLogs("app.services.email_otp_service", level="ERROR"):
            self.assertEqual(self._verificar(email, CERTO).status_code, 502)
        self.supabase.status_verificacao = None
        self.assertEqual(self._verificar(email, CERTO).status_code, 200)


if __name__ == "__main__":
    unittest.main()
