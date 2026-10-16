"""Helpers compartilhados pelos testes da auditoria 2026-10.

Usuários fictícios com e-mail/telefone aleatórios (uuid4); nenhum dado real.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

SENHA = "SenhaForte123"


def cliente(raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def telefone_aleatorio() -> str:
    return f"119{uuid4().int % 100000000:08d}"


def criar_usuario(client: TestClient, telefone: str | None = None, email: str | None = None):
    """Cria um usuário direto no banco, no mesmo estado que a rota legada
    `POST /api/usuarios` gravava (e-mail e telefone NÃO verificados).
    A rota foi removida (AUD-011); só o setup mudou, as asserções não.
    Devolve (id, headers, email, telefone, token)."""
    from tests.fixtures_usuario import criar_usuario_no_banco

    sufixo = uuid4().hex[:10]
    email = email or f"aud-{sufixo}@example.com"
    usuario_id, headers = criar_usuario_no_banco(email, "Usuario Auditoria", telefone=telefone, senha=SENHA)
    token = headers["Authorization"].removeprefix("Bearer ")
    return usuario_id, headers, email, telefone, token


def criar_local(client, headers, nome="Hospital Auditoria", **extra):
    corpo = {"nome_local": nome, "data_inicio": "2026-01-01", "data_final": "2026-01-31"}
    corpo.update(extra)
    resposta = client.post("/api/locais", headers=headers, json=corpo)
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["data"]


def criar_plantao(client, headers, local_id, data="2026-07-17", inicio="07:00", fim="19:00", **extra):
    corpo = {
        "local_id": local_id,
        "data_inicio": f"{data}T{inicio}:00",
        "data_fim": f"{data}T{fim}:00",
        "valor": "1200.00",
    }
    corpo.update(extra)
    resposta = client.post("/api/plantoes", headers=headers, json=corpo)
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["data"]


def listar_plantoes(client, headers):
    resposta = client.get("/api/plantoes", headers=headers)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["data"]


def tornar_amigos(client, id_a, headers_a, id_b, headers_b):
    criada = client.post("/api/amizades", headers=headers_a, json={"destinatario_usuario_id": id_b})
    assert criada.status_code == 201, criada.text
    amizade_id = criada.json()["data"]["id"]
    aceita = client.patch(f"/api/amizades/{amizade_id}/aceitar", headers=headers_b)
    assert aceita.status_code == 200, aceita.text
    return amizade_id


def transferir_por_solicitacao(client, headers_a, id_b, headers_b, plantao_id):
    """A pede para transferir `plantao_id` a B; B aceita. Devolve (solicitacao_id, clone de B).
    Só exercita o fluxo de troca já existente (validado em aparelho) — não o altera."""
    criada = client.post("/api/solicitacoes-troca", headers=headers_a, json={
        "destinatario_usuario_id": id_b,
        "tipo": "transferir",
        "plantao_transferido_id": plantao_id,
    })
    assert criada.status_code == 201, criada.text
    solicitacao_id = criada.json()["data"]["id"]
    aceita = client.patch(f"/api/solicitacoes-troca/{solicitacao_id}/aceitar", headers=headers_b)
    assert aceita.status_code == 200, aceita.text
    recebidos = [p for p in listar_plantoes(client, headers_b) if p.get("recebido_via_transferencia")]
    assert len(recebidos) == 1, recebidos
    return solicitacao_id, recebidos[0]


def agora_iso(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def pull(client, headers, since: str | None = None):
    params = {"since": since} if since else None
    resposta = client.get("/api/sync/pull", headers=headers, params=params)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["data"]
