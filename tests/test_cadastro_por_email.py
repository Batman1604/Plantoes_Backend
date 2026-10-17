"""Cadastro com o código por e-mail do Supabase Auth (fase Supabase + Render).

/auth/cadastro, /auth/reenviar-cadastro e /auth/confirmar-cadastro com
SUPABASE_URL/SUPABASE_ANON_KEY definidas. O Supabase nunca é chamado: o
`httpx.post` do EmailOtpService é substituído por um falso que aceita só o
código CERTO."""

import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.main import app
from app.models import User
from app.repositories.plantao_repository import CadastroPendenteRepository

CERTO = "424242"
_SUPABASE = "https://projeto-de-teste.supabase.co"


class SupabaseFalso:
    """Imita /auth/v1/otp e /auth/v1/verify. `status_envio`/`status_verificacao`
    forçam uma resposta (429, 500...)."""

    def __init__(self):
        self.chamadas: list[tuple[str, dict]] = []
        self.status_envio = 200
        self.status_verificacao = None
        self.corpo_erro = {}

    def __call__(self, url, json, headers, timeout):
        caminho = url.rsplit("/auth/v1/", 1)[1]
        self.chamadas.append((caminho, json))
        pedido = httpx.Request("POST", url)
        if caminho == "otp":
            return httpx.Response(self.status_envio, json=self.corpo_erro, request=pedido)
        if self.status_verificacao is not None:
            return httpx.Response(self.status_verificacao, json=self.corpo_erro, request=pedido)
        if json["token"] == CERTO:
            return httpx.Response(200, json={"access_token": "descartado"}, request=pedido)
        return httpx.Response(403, json={"error_code": "otp_expired"}, request=pedido)

    def caminhos(self):
        return [c for c, _ in self.chamadas]


class TestCadastroPorEmail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.supabase = SupabaseFalso()
        for alvo in (
            patch.object(settings, "supabase_url", _SUPABASE),
            patch.object(settings, "supabase_anon_key", "anon-de-teste"),
            patch("app.services.email_otp_service.httpx.post", side_effect=self.supabase),
        ):
            alvo.start()
            self.addCleanup(alvo.stop)
        # O canal antigo não pode ser usado quando o Supabase está configurado.
        sms = patch("app.services.sms_service.SmsService.enviar_codigo_verificacao")
        self.sms = sms.start()
        self.addCleanup(sms.stop)

    def _cadastrar(self, email, telefone):
        return self.client.post(
            "/api/auth/cadastro",
            json={"nome": "Usuário E-mail", "email": email, "telefone": telefone, "senha": "SenhaForte123"},
        )

    def _confirmar(self, email, codigo):
        return self.client.post("/api/auth/confirmar-cadastro", json={"email": email, "codigo": codigo})

    def test_cadastro_pede_o_envio_ao_supabase_e_confirma_marcando_o_email(self):
        resposta = self._cadastrar("Novo.Email@Example.com", "11977770001")
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["canal"], "email")
        self.assertEqual(self.supabase.chamadas, [("otp", {"email": "novo.email@example.com", "create_user": True})])
        self.sms.assert_not_called()

        self.assertEqual(self._confirmar("novo.email@example.com", "000000").status_code, 400)

        with patch("app.routes.plantao_routes.EmailService.enviar_verificacao") as email_de_verificacao:
            certo = self._confirmar("novo.email@example.com", CERTO)
        self.assertEqual(certo.status_code, 201, certo.text)
        self.assertEqual(self.supabase.chamadas[-1], ("verify", {"type": "email", "email": "novo.email@example.com", "token": CERTO}))
        usuario = certo.json()["data"]["usuario"]
        self.assertTrue(usuario["email_verificado"])
        # O telefone não foi provado por código (AUD-011/AUD-058).
        self.assertFalse(usuario["telefone_verificado"])
        # E-mail já verificado pelo código: nada de segundo e-mail de verificação.
        email_de_verificacao.assert_not_called()
        with SessionLocal() as db:
            self.assertIsNotNone(db.query(User).filter_by(email="novo.email@example.com").one_or_none())

        login = self.client.post("/api/auth/login", json={"email": "novo.email@example.com", "senha": "SenhaForte123"})
        self.assertEqual(login.status_code, 200, login.text)

    def test_cinco_erros_matam_o_codigo_do_nosso_lado(self):
        email = "tentativas-email@example.com"
        self._cadastrar(email, "11977770002")
        for _ in range(5):
            self.assertEqual(self._confirmar(email, "000000").status_code, 400)
        verificacoes = self.supabase.caminhos().count("verify")
        # Nem o certo vale, e o Supabase nem é consultado (ele não limita erros por código).
        self.assertEqual(self._confirmar(email, CERTO).status_code, 400)
        self.assertEqual(self.supabase.caminhos().count("verify"), verificacoes)

        # Reenviar pede outro envio ao Supabase e zera o contador.
        reenvio = self.client.post("/api/auth/reenviar-cadastro", json={"email": email})
        self.assertEqual(reenvio.status_code, 200, reenvio.text)
        self.assertEqual(reenvio.json()["canal"], "email")
        self.assertEqual(self.supabase.caminhos()[-1], "otp")
        self.assertEqual(self._confirmar(email, CERTO).status_code, 201)

    def test_limite_do_supabase_vira_429_com_os_segundos(self):
        self.supabase.status_envio = 429
        self.supabase.corpo_erro = {"msg": "For security purposes, you can only request this after 37 seconds."}
        resposta = self._cadastrar("limite@example.com", "11977770003")
        self.assertEqual(resposta.status_code, 429, resposta.text)
        self.assertIn("Aguarde 37 segundos", resposta.json()["message"])

        reenvio = self.client.post("/api/auth/reenviar-cadastro", json={"email": "limite@example.com"})
        self.assertEqual(reenvio.status_code, 429, reenvio.text)

    def test_supabase_fora_do_ar_e_502_e_nao_gasta_tentativa(self):
        self.supabase.status_envio = 500
        with self.assertLogs("app.services.email_otp_service", level="ERROR"):
            resposta = self._cadastrar("fora@example.com", "11977770004")
        self.assertEqual(resposta.status_code, 502, resposta.text)
        self.assertIn("e-mail", resposta.json()["message"])

        self.supabase.status_envio = 200
        self.assertEqual(self.client.post("/api/auth/reenviar-cadastro", json={"email": "fora@example.com"}).status_code, 200)
        self.supabase.status_verificacao = 503
        with self.assertLogs("app.services.email_otp_service", level="ERROR"):
            for _ in range(6):
                self.assertEqual(self._confirmar("fora@example.com", CERTO).status_code, 502)
        self.supabase.status_verificacao = None
        self.assertEqual(self._confirmar("fora@example.com", CERTO).status_code, 201)


class TestCadastroSemSupabase(unittest.TestCase):
    """Fallback: sem as variáveis, o canal antigo segue igual."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_canal_whatsapp_e_supabase_intocado(self):
        with (
            patch("app.services.email_otp_service.httpx.post") as post,
            patch("app.routes.plantao_routes.secrets.randbelow", return_value=135246),
            patch("app.services.sms_service.SmsService.enviar_codigo_verificacao", return_value=True),
        ):
            resposta = self.client.post(
                "/api/auth/cadastro",
                json={"nome": "Usuário Zap", "email": "zap@example.com", "telefone": "11977770005", "senha": "SenhaForte123"},
            )
            self.assertEqual(resposta.status_code, 200, resposta.text)
            self.assertEqual(resposta.json()["canal"], "whatsapp")
            certo = self.client.post("/api/auth/confirmar-cadastro", json={"email": "zap@example.com", "codigo": "135246"})
            post.assert_not_called()
        self.assertEqual(certo.status_code, 201, certo.text)
        self.assertTrue(certo.json()["data"]["usuario"]["telefone_verificado"])
        self.assertFalse(certo.json()["data"]["usuario"]["email_verificado"])

    def test_pendente_do_supabase_nao_confere_sem_supabase(self):
        """Pendente gravado com o Supabase ligado (coluna `codigo` vazia) e o
        Supabase desligado depois: nenhum código confere, nem o vazio."""
        from datetime import UTC, datetime, timedelta

        with SessionLocal() as db:
            CadastroPendenteRepository.upsert(
                db, nome="X Y", email="orfao@example.com", telefone="11977770006",
                senha_hash="h", codigo="", expira_em=datetime.now(UTC) + timedelta(minutes=10),
            )
            self.assertIsNone(CadastroPendenteRepository.conferir(db, "orfao@example.com", ""))
            self.assertIsNone(CadastroPendenteRepository.conferir(db, "orfao@example.com", "123456"))


if __name__ == "__main__":
    unittest.main()
