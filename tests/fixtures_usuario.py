"""Usuário de teste criado direto no banco.

Os testes não usam mais POST /api/usuarios (cadastro legado, removido por
decisão do usuário — AUD-011): gravam o usuário pelo ORM e emitem o token
como o login faria.

Os defaults reproduzem o que a rota legada gravava (e-mail e telefone NÃO
verificados), para que os testes migrados continuem exercitando o mesmo
estado de conta.
"""

from uuid import uuid4

from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.models import User

SENHA = "SenhaForte123"


def criar_usuario_no_banco(
    email: str | None = None,
    nome: str = "Usuario Teste",
    *,
    telefone: str | None = None,
    telefone_verificado: bool = False,
    email_verificado: bool = False,
    senha: str = SENHA,
) -> tuple[int, dict]:
    """Devolve (id, headers com Bearer)."""
    with SessionLocal() as db:
        usuario = User(
            nome=nome,
            email=email or f"t-{uuid4().hex[:10]}@example.com",
            telefone=telefone,
            telefone_verificado=telefone_verificado,
            email_verificado=email_verificado,
            senha_hash=hash_password(senha),
        )
        db.add(usuario)
        db.commit()
        return usuario.id, {"Authorization": f"Bearer {create_access_token(usuario.id)}"}
