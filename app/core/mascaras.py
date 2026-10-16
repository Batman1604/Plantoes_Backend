"""Máscaras para dados pessoais em log.

Telefone e email identificam uma pessoa. Um log de aplicação é copiado para
agregadores, fica retido por meses e costuma ser visível a mais gente do que o
banco — registrar o dado completo ali é espalhá-lo sem necessidade. O que o log
precisa é permitir CORRELACIONAR eventos, não reconstituir o contato.
"""

import logging


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


def mascarar_query_string(caminho: str) -> str:
    """Troca os valores da query string por '***', mantendo as chaves:
    '/api/usuarios/busca?email=a%40b.com' -> '/api/usuarios/busca?email=***'.
    Um parâmetro sem '=' vira só '***' (o próprio nome poderia ser o dado)."""
    base, separador, query = caminho.partition("?")
    if not separador:
        return caminho
    partes = [f"{par.split('=', 1)[0]}=***" if "=" in par else "***" for par in query.split("&") if par]
    return f"{base}?{'&'.join(partes)}"


class FiltroQueryStringDoAccessLog(logging.Filter):
    """AUD-033: o access log do uvicorn grava o caminho COM a query string, e
    /api/usuarios/busca leva nela o e-mail e o telefone procurados. Mover a
    busca para o corpo mudaria o contrato com o app; este filtro mascara os
    valores no log, em todas as rotas, sem mudar a requisição.

    O uvicorn registra com args (cliente, método, caminho, versão, status);
    o caminho é o terceiro."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = args[:2] + (mascarar_query_string(args[2]),) + args[3:]
        return True
