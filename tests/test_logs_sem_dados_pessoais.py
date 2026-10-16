"""AUD-033: nenhum e-mail, telefone, nome de terceiro ou senha completos no log."""

import logging
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from tests.fixtures_usuario import SENHA, criar_usuario_no_banco


class TestLogsSemDadosPessoais(unittest.TestCase):
    def test_fluxos_comuns_nao_registram_dados_pessoais(self):
        client = TestClient(app)
        sufixo = uuid4().hex[:8]
        email_a = f"medica.{sufixo}@example.com"
        email_b = f"colega.{sufixo}@example.com"
        email_novo = f"novo.{sufixo}@example.com"
        telefone_terceiro = f"119{uuid4().int % 100000000:08d}"
        telefone_novo = f"119{uuid4().int % 100000000:08d}"
        nome_terceiro = f"Fulano{sufixo}"
        senha_curta = f"x{sufixo[:3]}"

        with self.assertLogs(level=logging.INFO) as capturado:
            id_a, headers_a = criar_usuario_no_banco(email_a)
            id_b, headers_b = criar_usuario_no_banco(email_b)
            client.post("/api/auth/login", json={"email": email_a, "senha": SENHA})
            client.post("/api/auth/login", json={"email": email_a, "senha": senha_curta})  # 422

            with patch("app.routes.plantao_routes.secrets.randbelow", return_value=123456), \
                 patch("app.services.sms_service.SmsService.enviar_codigo_verificacao", return_value=True):
                client.post("/api/auth/cadastro", json={
                    "nome": "Novo", "email": email_novo, "telefone": telefone_novo, "senha": SENHA,
                })
            client.post("/api/auth/confirmar-cadastro", json={"email": email_novo, "codigo": "123456"})

            amizade = client.post("/api/amizades", headers=headers_a, json={"destinatario_usuario_id": id_b})
            client.patch(f"/api/amizades/{amizade.json()['data']['id']}/aceitar", headers=headers_b)

            local = client.post("/api/locais", headers=headers_a, json={
                "nome_local": "Hospital Log", "data_inicio": "2026-01-01", "data_final": "2026-01-31",
            }).json()["data"]
            plantao = client.post("/api/plantoes", headers=headers_a, json={
                "local_id": local["id"], "data_inicio": "2026-07-17T07:00:00",
                "data_fim": "2026-07-17T19:00:00", "valor": "1200.00",
            }).json()["data"]
            client.post("/api/solicitacoes-troca", headers=headers_a, json={
                "destinatario_usuario_id": id_b, "tipo": "transferir", "plantao_transferido_id": plantao["id"],
            })
            with patch("app.services.sms_service.SmsService.enviar", return_value=True):
                client.post(f"/api/plantoes/{plantao['id']}/transferir", headers=headers_a, json={
                    "destinatario_nome": nome_terceiro, "destinatario_telefone": telefone_terceiro, "canal": "sms",
                })
            client.post("/api/sync/push", headers=headers_a, json={})
            logging.getLogger(__name__).info("fim")

        texto = "\n".join(capturado.output)
        for dado in (email_a, email_b, email_novo, telefone_terceiro, telefone_novo, nome_terceiro, senha_curta):
            self.assertNotIn(dado, texto)
        # Controle com dente: os fluxos de fato registraram algo mascarado.
        self.assertIn(f"m{'*' * (len(email_a.split('@')[0]) - 1)}@example.com", texto)


if __name__ == "__main__":
    unittest.main()
