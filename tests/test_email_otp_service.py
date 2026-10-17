"""Código por e-mail via Supabase Auth (app/services/email_otp_service.py).

O Supabase NUNCA é chamado de verdade: `httpx.post` do módulo é substituído e
as respostas imitam as do Supabase Auth (GoTrue)."""

import logging
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services.email_otp_service import EmailOtpService, FalhaNoSupabase, LimiteDeEnvio

_URL = "https://projeto-de-teste.supabase.co"
_ANON = "chave-anon-de-teste"
_EMAIL = "fulano.de.tal@example.com"


def _resposta(status: int, corpo=None, headers=None) -> httpx.Response:
    return httpx.Response(status, json=corpo if corpo is not None else {}, headers=headers,
                          request=httpx.Request("POST", _URL))


class _ComSupabase(unittest.TestCase):
    def setUp(self):
        for nome, valor in (("supabase_url", _URL + "/"), ("supabase_anon_key", _ANON)):
            p = patch.object(settings, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def _post(self, resposta=None, erro=None):
        p = patch("app.services.email_otp_service.httpx.post", return_value=resposta, side_effect=erro)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _sem_email_completo_no_log(self, registros):
        for registro in registros:
            self.assertNotIn(_EMAIL, registro.getMessage())


class TestEnviar(_ComSupabase):
    def test_chama_otp_com_create_user_e_apikey(self):
        post = self._post(_resposta(200))
        EmailOtpService.enviar(_EMAIL)
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], f"{_URL}/auth/v1/otp")
        self.assertEqual(kwargs["json"], {"email": _EMAIL, "create_user": True})
        self.assertEqual(kwargs["headers"], {"apikey": _ANON})
        self.assertIn("timeout", kwargs)

    def test_429_repassa_os_segundos_da_mensagem(self):
        self._post(_resposta(429, {"code": 429, "error_code": "over_email_send_rate_limit",
                                   "msg": "For security purposes, you can only request this after 37 seconds."}))
        with self.assertRaises(LimiteDeEnvio) as ctx:
            EmailOtpService.enviar(_EMAIL)
        self.assertEqual(ctx.exception.segundos, 37)

    def test_429_le_retry_after(self):
        self._post(_resposta(429, {"msg": "email rate limit exceeded"}, headers={"Retry-After": "12"}))
        with self.assertRaises(LimiteDeEnvio) as ctx:
            EmailOtpService.enviar(_EMAIL)
        self.assertEqual(ctx.exception.segundos, 12)

    def test_429_sem_prazo_informado(self):
        self._post(_resposta(429, {"msg": "email rate limit exceeded"}))
        with self.assertRaises(LimiteDeEnvio) as ctx:
            EmailOtpService.enviar(_EMAIL)
        self.assertIsNone(ctx.exception.segundos)

    def test_5xx_falha_alto_com_email_mascarado(self):
        self._post(_resposta(500, {"msg": "Error sending magic link email"}))
        with self.assertLogs("app.services.email_otp_service", level="ERROR") as logs, \
                self.assertRaises(FalhaNoSupabase):
            EmailOtpService.enviar(_EMAIL)
        self._sem_email_completo_no_log(logs.records)
        self.assertIn("f*", logs.output[0])

    def test_timeout_registra_excecao_e_falha(self):
        self._post(erro=httpx.ReadTimeout("timeout"))
        with self.assertLogs("app.services.email_otp_service", level="ERROR") as logs, \
                self.assertRaises(FalhaNoSupabase):
            EmailOtpService.enviar(_EMAIL)
        self.assertIsNotNone(logs.records[0].exc_info, "timeout precisa ir ao log com traceback")
        self._sem_email_completo_no_log(logs.records)

    def test_4xx_recusado_e_falha_sem_vazar_a_mensagem(self):
        # A mensagem do Supabase traz o e-mail inteiro; só o código do erro vai ao log.
        self._post(_resposta(400, {"error_code": "email_address_not_authorized",
                                   "msg": f'Email address "{_EMAIL}" cannot be used as it is not authorized'}))
        with self.assertLogs("app.services.email_otp_service", level="ERROR") as logs, \
                self.assertRaises(FalhaNoSupabase):
            EmailOtpService.enviar(_EMAIL)
        self._sem_email_completo_no_log(logs.records)
        self.assertIn("email_address_not_authorized", logs.output[0])


class TestVerificar(_ComSupabase):
    def test_codigo_aceito(self):
        post = self._post(_resposta(200, {"access_token": "sessao-do-supabase-descartada"}))
        self.assertTrue(EmailOtpService.verificar(_EMAIL, "123456"))
        args, kwargs = post.call_args
        self.assertEqual(args[0], f"{_URL}/auth/v1/verify")
        self.assertEqual(kwargs["json"], {"type": "email", "email": _EMAIL, "token": "123456"})
        self.assertEqual(kwargs["headers"], {"apikey": _ANON})

    def test_codigo_errado_ou_expirado_devolve_false(self):
        self._post(_resposta(403, {"code": 403, "error_code": "otp_expired",
                                   "msg": "Token has expired or is invalid"}))
        self.assertFalse(EmailOtpService.verificar(_EMAIL, "000000"))

    def test_outro_4xx_devolve_false_mas_fica_no_log(self):
        self._post(_resposta(422, {"error_code": "validation_failed"}))
        with self.assertLogs("app.services.email_otp_service", level="WARNING") as logs:
            self.assertFalse(EmailOtpService.verificar(_EMAIL, "000000"))
        self.assertIn("validation_failed", logs.output[0])

    def test_chave_errada_e_falha_nao_codigo_invalido(self):
        self._post(_resposta(401, {"message": "Invalid API key"}))
        with self.assertLogs("app.services.email_otp_service", level="ERROR"), \
                self.assertRaises(FalhaNoSupabase):
            EmailOtpService.verificar(_EMAIL, "123456")

    def test_429_na_verificacao(self):
        self._post(_resposta(429, {"msg": "Request rate limit reached"}))
        with self.assertRaises(LimiteDeEnvio):
            EmailOtpService.verificar(_EMAIL, "123456")


class TestConfiguracao(unittest.TestCase):
    _BASE = dict(_env_file=None, environment="test", database_url="sqlite://", jwt_secret_key="x")

    def test_sem_supabase_nao_esta_configurado(self):
        with patch.object(settings, "supabase_url", ""), patch.object(settings, "supabase_anon_key", ""):
            self.assertFalse(EmailOtpService.configurado())

    def test_so_uma_das_variaveis_derruba_a_configuracao(self):
        with self.assertRaises(ValidationError):
            Settings(**self._BASE, supabase_url=_URL)
        with self.assertRaises(ValidationError):
            Settings(**self._BASE, supabase_anon_key=_ANON)
        self.assertTrue(Settings(**self._BASE, supabase_url=_URL, supabase_anon_key=_ANON).supabase_url)


if __name__ == "__main__":
    unittest.main()
