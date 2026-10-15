"""Usuário de teste criado direto no banco.

Os testes novos não usam POST /api/usuarios (cadastro legado, que será
removido por decisão do usuário): gravam o usuário pelo ORM e emitem o token
como o login faria.
"""

from uuid import uuid4

from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.models import User

SENHA = "SenhaForte123"


def criar_usuario_no_banco(email: str | None = None, nome: str = "Usuario Teste") -> tuple[int, dict]:
    """Devolve (id, headers com Bearer)."""
    with SessionLocal() as db:
        usuario = User(
            nome=nome,
            email=(email or f"t-{uuid4().hex[:10]}@example.com").lower(),
            senha_hash=hash_password(SENHA),
        )
        db.add(usuario)
        db.commit()
        return usuario.id, {"Authorization": f"Bearer {create_access_token(usuario.id)}"}
