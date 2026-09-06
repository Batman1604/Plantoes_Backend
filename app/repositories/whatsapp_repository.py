from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WhatsappSessao

CODIGO_VALIDADE_MINUTOS = 10
MAX_TENTATIVAS_CODIGO = 5


class WhatsappRepository:
    @staticmethod
    def get_by_telefone(db: Session, telefone: str) -> WhatsappSessao | None:
        """Recupera uma sessão de WhatsApp existente associada ao telefone."""
        return db.scalar(select(WhatsappSessao).where(WhatsappSessao.telefone == telefone))

    @staticmethod
    def criar(db: Session, telefone: str) -> WhatsappSessao:
        """Cria uma nova sessão de WhatsApp para um usuário que ainda não conversou com o bot."""
        sessao = WhatsappSessao(telefone=telefone)
        db.add(sessao)
        db.commit()
        db.refresh(sessao)
        return sessao

    @staticmethod
    def atualizar_estado(db: Session, sessao: WhatsappSessao, estado: str) -> None:
        sessao.estado = estado
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def atualizar_historico(db: Session, sessao: WhatsappSessao, historico_json: str) -> None:
        sessao.historico = historico_json
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def salvar_codigo(db: Session, sessao: WhatsappSessao, codigo: str) -> None:
        sessao.codigo_verificacao = codigo
        sessao.codigo_expira_em = datetime.now(UTC) + timedelta(minutes=CODIGO_VALIDADE_MINUTOS)
        sessao.tentativas_codigo = 0
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def codigo_valido(sessao: WhatsappSessao) -> bool:
        """Verifica se ainda há um código pendente, não expirado e com tentativas disponíveis."""
        if sessao.codigo_verificacao is None:
            return False
        if sessao.tentativas_codigo >= MAX_TENTATIVAS_CODIGO:
            return False
        expira_em = sessao.codigo_expira_em
        if expira_em is None:
            return False
        if expira_em.tzinfo is None:
            # SQLite não preserva timezone ao ler de volta a coluna.
            expira_em = expira_em.replace(tzinfo=UTC)
        return expira_em > datetime.now(UTC)

    @staticmethod
    def registrar_tentativa_invalida(db: Session, sessao: WhatsappSessao) -> None:
        sessao.tentativas_codigo += 1
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def vincular_usuario(db: Session, sessao: WhatsappSessao, usuario_id: int) -> None:
        sessao.usuario_id = usuario_id
        sessao.estado = "autenticado"
        sessao.codigo_verificacao = None
        sessao.codigo_expira_em = None
        sessao.tentativas_codigo = 0
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def resetar_sessao(db: Session, sessao: WhatsappSessao) -> None:
        sessao.usuario_id = None
        sessao.estado = "aguardando_email"
        sessao.historico = "[]"
        sessao.codigo_verificacao = None
        sessao.codigo_expira_em = None
        sessao.tentativas_codigo = 0
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()
