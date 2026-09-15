"""Máscaras para dados pessoais em log.

Telefone e email identificam uma pessoa. Um log de aplicação é copiado para
agregadores, fica retido por meses e costuma ser visível a mais gente do que o
banco — registrar o dado completo ali é espalhá-lo sem necessidade. O que o log
precisa é permitir CORRELACIONAR eventos, não reconstituir o contato.
"""


def mascarar_telefone(telefone: str | None) -> str:
    """Mantém apenas os quatro últimos dígitos."""
    if not telefone:
        return "(sem telefone)"
    apenas_digitos = "".join(c for c in telefone if c.isdigit())
    if len(apenas_digitos) <= 4:
        return "*" * len(apenas_digitos)
    return "*" * (len(apenas_digitos) - 4) + apenas_digitos[-4:]


def mascarar_email(email: str | None) -> str:
    """Mantém a primeira letra e o domínio: 'a***@exemplo.com'."""
    if not email or "@" not in email:
        return "(sem email)"
    usuario, _, dominio = email.partition("@")
    if not usuario:
        return f"***@{dominio}"
    return f"{usuario[0]}{'*' * max(len(usuario) - 1, 1)}@{dominio}"
