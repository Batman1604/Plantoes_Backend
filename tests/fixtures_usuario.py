"""Usuário de teste criado direto no banco.

Os testes não usam mais POST /api/usuarios (cadastro legado, removido por
decisão do usuário — AUD-011): gravam o usuário pelo ORM e emitem o token
como o login faria.

Os defaults reproduzem o que a rota legada gravava (e-mail e telefone NÃO
verificados), para que os testes migrados continuem exercitando o mesmo
estado de conta.
"""

from functools import lru_cache
from uuid import uuid4

from app.core.database import SessionLocal
from app.core.security import create_access_token, hash_password
from app.models import User

SENHA = "SenhaForte123"


@lru_cache(maxsize=None)
def _hash(senha: str) -> str:
    """Um hash por senha distinta, reaproveitado entre os usuários de teste.
    Com 600.000 iterações (AUD-059), refazer o hash a cada usuário somava
    minutos à suíte; o hash continua real, só o salt se repete."""
    return hash_password(senha)


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
            senha_hash=_hash(senha),
        )
        db.add(usuario)
        db.commit()
        return usuario.id, {"Authorization": f"Bearer {create_access_token(usuario.id)}"}
