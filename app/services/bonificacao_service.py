"""Regra de negócio de bonificações.

Extraído dos handlers de rota pelo mesmo motivo de LocalService: permitir que a
rota HTTP e a sincronização offline usem o mesmo caminho sem que uma dependa da
outra (ver auditoria A-13).
"""

import logging

from sqlalchemy.orm import Session

from app.models import Bonificacao, User
from app.repositories.plantao_repository import (
    BonificacaoRepository,
    FinanceiroRepository,
    PlantaoRepository,
    SyncRepository,
)
from app.schemas.plantao_schema import BonificacaoCreate, BonificacaoUpdate
from app.services.erros import NaoEncontrado, RequisicaoInvalida, SemPermissao

logger = logging.getLogger(__name__)


def _sincronizar_financeiro(db: Session, plantao) -> None:
    """O total do mês em `financeiro` é derivado dos plantões e bonificações;
    toda escrita de bonificação precisa reprocessar o mês afetado."""
    FinanceiroRepository.sync_mes(db, plantao.local_id, plantao.data_inicio.month, plantao.data_inicio.year)


class BonificacaoService:
    @staticmethod
    def criar(db: Session, usuario: User, payload: BonificacaoCreate) -> Bonificacao:
        plantao = PlantaoRepository.get_by_id_and_user(db, payload.plantao_id, usuario.id)
        if plantao is None:
            raise NaoEncontrado("Plantão não encontrado.")

        bonificacao = BonificacaoRepository.create(db, payload)
        _sincronizar_financeiro(db, plantao)
        return bonificacao

    @staticmethod
    def _plantao_dono(db: Session, usuario: User, bonificacao_id: int, acao: str):
        """Devolve (bonificação, plantão) validando o dono.

        `acao` entra na mensagem apenas para preservar o texto que cada rota já
        devolvia ("editar" / "deletar").

        Note a assimetria de status, preservada do comportamento anterior: aqui
        é 403 quando a bonificação existe mas o plantão é de outra pessoa,
        enquanto `criar` devolve 404. Ver o comentário em SemPermissao.
        """
        bonificacao = BonificacaoRepository.get_by_id(db, bonificacao_id)
        if bonificacao is None:
            raise NaoEncontrado("Bonificação não encontrada.")

        plantao = PlantaoRepository.get_by_id_and_user(db, bonificacao.plantao_id, usuario.id)
        if plantao is None:
            raise SemPermissao(f"Sem permissão para {acao} esta bonificação.")
        return bonificacao, plantao

    @staticmethod
    def atualizar(db: Session, usuario: User, bonificacao_id: int, payload: BonificacaoUpdate) -> Bonificacao:
        _, plantao = BonificacaoService._plantao_dono(db, usuario, bonificacao_id, "editar")

        dados = payload.model_dump(exclude_unset=True)
        if not dados:
            raise RequisicaoInvalida("Nenhum campo enviado para atualização.")

        bonificacao = BonificacaoRepository.update(db, bonificacao_id, **dados)
        _sincronizar_financeiro(db, plantao)
        return bonificacao

    @staticmethod
    def deletar(db: Session, usuario: User, bonificacao_id: int) -> None:
        bonificacao, plantao = BonificacaoService._plantao_dono(db, usuario, bonificacao_id, "deletar")

        SyncRepository.registrar_exclusao(
            db, usuario.id, "bonificacao", bonificacao.id, bonificacao.uuid_cliente
        )
        BonificacaoRepository.delete(db, bonificacao_id)
        _sincronizar_financeiro(db, plantao)
