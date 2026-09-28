"""Regra de negócio de trocas manuais (transferência por telefone).

Extraído dos handlers de rota pelo mesmo motivo de LocalService e
BonificacaoService — ver auditoria A-13.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Plantao, Troca, User
from app.repositories.plantao_repository import LocalRepository, TrocaRepository
from app.schemas.plantao_schema import TrocaOperacaoCreate
from app.services.erros import Conflito, NaoEncontrado, SemPermissao

logger = logging.getLogger(__name__)


@dataclass
class ResultadoDaTroca:
    """O que a operação produziu, em objetos de domínio.

    A rota serializa isto em TrocaOperacaoData e o SyncService lê os ids. Antes,
    o serviço devolvia o próprio envelope HTTP e o sync tinha que desembrulhar
    `resposta.data.troca_id`.
    """

    troca: Troca
    plantao_transferido: Plantao
    plantao_recebido: Plantao | None


class TrocaService:
    @staticmethod
    def registrar(db: Session, usuario: User, payload: TrocaOperacaoCreate) -> ResultadoDaTroca:
        plantao = db.scalar(select(Plantao).where(Plantao.id == payload.plantao_transferido_id))
        if plantao is None:
            raise NaoEncontrado("Plantão transferido não encontrado.")
        if plantao.usuario_id != usuario.id:
            raise SemPermissao("Plantão não pertence ao usuário autenticado.")
        if plantao.transferido:
            raise Conflito("Este plantão já foi transferido e não pode ser transferido novamente.")

        recebido_payload = payload.plantao_recebido
        if recebido_payload is not None:
            local = LocalRepository.get_by_id(db, recebido_payload.local_id)
            if local is None:
                raise NaoEncontrado("Local do plantão recebido não encontrado.")
            if local.usuario_id != usuario.id:
                raise SemPermissao("Local do plantão recebido não pertence ao usuário autenticado.")

        recebido = None
        try:
            # Não chame repositórios que executam commit aqui: as três gravações
            # precisam confirmar juntas ou serem revertidas juntas.
            plantao.transferido = True

            if recebido_payload is not None:
                recebido = Plantao(
                    usuario_id=usuario.id,
                    local_id=recebido_payload.local_id,
                    valor=recebido_payload.valor,
                    data_inicio=recebido_payload.data_inicio,
                    horario_inicio=recebido_payload.horario_inicio,
                    data_fim=recebido_payload.data_fim,
                    horario_fim=recebido_payload.horario_fim,
                    sobreaviso=recebido_payload.sobreaviso,
                    descricao=recebido_payload.descricao,
                    recorrencia=None,
                    notificacao_ativa=True,
                    notificacao_antecedencia_minutos=60,
                    notificado=False,
                    executado=False,
                    transferido=False,
                    recebido_via_transferencia=True,
                    uuid_cliente=recebido_payload.uuid_cliente,
                )
                db.add(recebido)
                db.flush()

            troca = Troca(
                usuario_id=usuario.id,
                local_id=recebido.local_id if recebido is not None else plantao.local_id,
                plantao_id_transferido=plantao.id,
                plantao_id_recebido=recebido.id if recebido is not None else None,
                nome_transferir=payload.transferido_para.nome,
                telefone_transferir=payload.transferido_para.telefone,
                observacao_transferir=payload.transferido_para.observacao,
                uuid_cliente=payload.uuid_cliente,
                nome_receber=(recebido_payload.recebido_de.nome if recebido_payload else None),
                telefone_receber=(recebido_payload.recebido_de.telefone if recebido_payload else None),
                observacao_receber=(recebido_payload.recebido_de.observacao if recebido_payload else None),
            )
            db.add(troca)
            db.flush()
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Falha transacional ao registrar troca")
            raise

        return ResultadoDaTroca(troca=troca, plantao_transferido=plantao, plantao_recebido=recebido)

    @staticmethod
    def deletar(db: Session, usuario: User, troca_id: int) -> None:
        troca = TrocaRepository.get_by_id(db, troca_id)
        if troca is None:
            raise NaoEncontrado("Troca não encontrada.")
        # Autoriza pelo dono da TROCA, não pelo dono do local que ela referencia
        # — os dois divergem em plantão recebido por transferência (ver C-08).
        if troca.usuario_id != usuario.id:
            raise SemPermissao("Sem permissão para deletar esta troca.")
        TrocaRepository.delete(db, troca_id)
