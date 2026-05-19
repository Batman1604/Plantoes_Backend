from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WhatsappSessao


class WhatsappRepository:
    @staticmethod
    def get_by_telefone(db: Session, telefone: str) -> WhatsappSessao | None:
        return db.scalar(select(WhatsappSessao).where(WhatsappSessao.telefone == telefone))

    @staticmethod
    def criar(db: Session, telefone: str) -> WhatsappSessao:
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
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def vincular_usuario(db: Session, sessao: WhatsappSessao, usuario_id: int) -> None:
        sessao.usuario_id = usuario_id
        sessao.estado = "autenticado"
        sessao.codigo_verificacao = None
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()

    @staticmethod
    def resetar_sessao(db: Session, sessao: WhatsappSessao) -> None:
        sessao.usuario_id = None
        sessao.estado = "aguardando_email"
        sessao.historico = "[]"
        sessao.codigo_verificacao = None
        sessao.atualizado_em = datetime.now(UTC)
        db.commit()
