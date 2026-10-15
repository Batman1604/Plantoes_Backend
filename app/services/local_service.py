"""Regra de negócio de locais.

Extraído dos handlers de rota para que a rota HTTP e a sincronização offline
usem o MESMO caminho sem que uma dependa da outra. Antes, SyncService importava
`criar_local`, `atualizar_local` e `deletar_local` de app.routes.plantao_routes
e os chamava como funções: a camada de serviço dependia da de apresentação, e
qualquer mudança na assinatura de uma rota quebrava a sincronização em silêncio
(ver auditoria A-13).

Os métodos levantam erros de domínio (app.services.erros), nunca HTTPException.
Quem traduz para HTTP é a rota; quem traduz para SyncErro é o SyncService.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Local, Plantao, User
from app.repositories.plantao_repository import LocalRepository, PlantaoRepository
from app.schemas.plantao_schema import LocalCreate, LocalCreateWithUsuario, LocalUpdate
from app.services.erros import NaoEncontrado, RequisicaoInvalida
from app.services.pagamento_service import PagamentoService

logger = logging.getLogger(__name__)


class LocalService:
    @staticmethod
    def criar(db: Session, usuario: User, payload: LocalCreate) -> Local:
        dados = payload.model_dump()
        dados["usuario_id"] = usuario.id
        local = LocalRepository.create(db, LocalCreateWithUsuario.model_validate(dados))
        logger.info("Local criado para usuário %s: %s", usuario.email, local.nome_local)
        return local

    @staticmethod
    def obter_do_usuario(db: Session, usuario: User, local_id: int) -> Local:
        local = LocalRepository.get_by_id(db, local_id)
        if local is None or local.usuario_id != usuario.id:
            # Mesma resposta para inexistente e alheio — ver NaoEncontrado.
            raise NaoEncontrado("Local não encontrado")
        return local

    @staticmethod
    def atualizar(db: Session, usuario: User, local_id: int, payload: LocalUpdate) -> Local:
        dados = payload.model_dump(exclude_unset=True)
        if not dados:
            raise RequisicaoInvalida("Nenhum campo enviado para atualização.")

        local = LocalService.obter_do_usuario(db, usuario, local_id)

        # Mudar o dia de pagamento ou o modo (dia útil x dia corrido) altera a
        # data de pagamento de TODOS os plantões daquele local, não só dos
        # futuros — por isso o recálculo em lote abaixo.
        dia_anterior = local.dia_pagamento
        dia_novo = dados.get("dia_pagamento", dia_anterior)
        util_anterior = local.util_corrido
        util_novo = dados.get("util_corrido", util_anterior)
        recalcular = (dia_novo != dia_anterior) or (util_novo != util_anterior)

        try:
            for campo, valor in dados.items():
                if hasattr(local, campo):
                    setattr(local, campo, valor)

            if recalcular and dia_novo is not None:
                LocalService._recalcular_pagamentos(db, local, dia_novo, util_novo)

            db.commit()
            db.refresh(local)
        except Exception:
            db.rollback()
            raise

        logger.info("Local %s atualizado por %s", local_id, usuario.email)
        return local

    @staticmethod
    def _recalcular_pagamentos(db: Session, local: Local, dia_pagamento: int, util_corrido: bool) -> None:
        # AUD-006: só os plantões do dono do local. O clone que outro usuário
        # recebeu aponta para este local, mas editar o local não pode alterar
        # a data de pagamento nem o atualizado_em do plantão de outra pessoa.
        plantoes = PlantaoRepository.list_ids_e_datas_por_local(db, local.id, local.usuario_id)
        if not plantoes:
            return

        db.bulk_update_mappings(
            Plantao,
            [
                {
                    "id": plantao_id,
                    "pagamento_plantao": PagamentoService.calcular_data_pagamento_por_dia_mes(
                        data_plantao=data_plantao,
                        dia_pagamento=dia_pagamento,
                        util_corrido=util_corrido,
                    ),
                    # bulk_update_mappings ignora o onupdate do ORM — sem isto, esses
                    # plantões ficariam com atualizado_em desatualizado e o pagamento
                    # recalculado não apareceria em GET /api/sync/pull.
                    "atualizado_em": datetime.now(UTC),
                }
                for plantao_id, data_plantao in plantoes
            ],
        )

    @staticmethod
    def deletar(db: Session, usuario: User, local_id: int) -> None:
        if not LocalRepository.delete_by_user(db, local_id, usuario.id):
            raise NaoEncontrado("Local não encontrado")
        logger.info("Local %s deletado por %s", local_id, usuario.email)
