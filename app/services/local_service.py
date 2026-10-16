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
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.core.mascaras import mascarar_email
from app.models import Local, Plantao, User
from app.repositories.plantao_repository import LocalRepository, PlantaoRepository
from app.schemas.plantao_schema import LocalCreate, LocalCreateWithUsuario, LocalUpdate
from app.services.erros import NaoEncontrado, RequisicaoInvalida
from app.services.pagamento_service import PagamentoService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ConfigDePagamento:
    """O que determina a data de pagamento de um plantão do local."""

    dia: int | None
    util_corrido: bool
    inicio_ciclo: date
    fim_ciclo: date

    def datas_automaticas(self, data_plantao: date) -> set[date]:
        """Datas que esta configuração teria gravado sozinha (AUD-024)."""
        if self.dia is None:
            return set()
        return {
            PagamentoService.calcular_data_pagamento_plantao(
                data_plantao=data_plantao,
                indice_dia_pagamento=self.dia,
                data_inicio_ciclo=self.inicio_ciclo,
                data_final_ciclo=self.fim_ciclo,
                util_corrido=self.util_corrido,
            ),
            # Regra do recálculo antigo, que pode já estar gravada.
            PagamentoService.calcular_data_pagamento_por_dia_mes(
                data_plantao=data_plantao, dia_pagamento=self.dia, util_corrido=self.util_corrido,
            ),
        }


class LocalService:
    @staticmethod
    def criar(db: Session, usuario: User, payload: LocalCreate) -> Local:
        dados = payload.model_dump()
        dados["usuario_id"] = usuario.id
        local = LocalRepository.create(db, LocalCreateWithUsuario.model_validate(dados))
        # AUD-033: id do local, não o nome (dado do usuário, não do log).
        logger.info("Local %s criado para usuário %s", local.id, mascarar_email(usuario.email))
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

        # Mudar o dia de pagamento, o modo (dia útil x dia corrido) ou o ciclo
        # altera a data de pagamento dos plantões daquele local — por isso o
        # recálculo em lote abaixo. O ciclo entra desde o AUD-024: a regra da
        # criação depende dele.
        anterior = _ConfigDePagamento(local.dia_pagamento, local.util_corrido, local.data_inicio, local.data_final)
        novo = _ConfigDePagamento(
            dados.get("dia_pagamento", anterior.dia),
            dados.get("util_corrido", anterior.util_corrido),
            dados.get("data_inicio", anterior.inicio_ciclo),
            dados.get("data_final", anterior.fim_ciclo),
        )
        recalcular = novo != anterior

        try:
            for campo, valor in dados.items():
                if hasattr(local, campo):
                    setattr(local, campo, valor)

            if recalcular and novo.dia is not None:
                LocalService._recalcular_pagamentos(db, local, anterior)

            db.commit()
            db.refresh(local)
        except Exception:
            db.rollback()
            raise

        logger.info("Local %s atualizado por %s", local_id, mascarar_email(usuario.email))
        return local

    @staticmethod
    def _recalcular_pagamentos(db: Session, local: Local, anterior: "_ConfigDePagamento") -> None:
        """AUD-024: recalcula com a MESMA regra da criação
        (calcular_data_pagamento_plantao, com o ciclo do local). Antes usava
        calcular_data_pagamento_por_dia_mes — dia N corrido do mês seguinte ao
        plantão —, e a data mudava para um valor diferente do que um plantão
        novo teria.

        Só reescreve a data que era AUTOMÁTICA: nula, ou igual à que a
        configuração anterior do local produziria (pela regra da criação ou
        pela do recálculo antigo, que já pode estar gravada). Qualquer outra é
        data escolhida à mão e fica. Linhas cuja data não muda não são tocadas,
        para não carimbar atualizado_em à toa.
        """
        # AUD-006: só os plantões do dono do local. O clone que outro usuário
        # recebeu aponta para este local, mas editar o local não pode alterar
        # a data de pagamento nem o atualizado_em do plantão de outra pessoa.
        plantoes = PlantaoRepository.list_ids_e_datas_por_local(db, local.id, local.usuario_id)
        if not plantoes:
            return

        agora = datetime.now(UTC)
        mudancas = []
        for plantao_id, data_plantao, pagamento_atual in plantoes:
            if pagamento_atual is not None and pagamento_atual not in anterior.datas_automaticas(data_plantao):
                continue
            novo_pagamento = PagamentoService.calcular_data_pagamento_plantao(
                data_plantao=data_plantao,
                indice_dia_pagamento=local.dia_pagamento,
                data_inicio_ciclo=local.data_inicio,
                data_final_ciclo=local.data_final,
                util_corrido=local.util_corrido,
            )
            if novo_pagamento == pagamento_atual:
                continue
            mudancas.append({
                "id": plantao_id,
                "pagamento_plantao": novo_pagamento,
                # bulk_update_mappings ignora o onupdate do ORM — sem isto, esses
                # plantões ficariam com atualizado_em desatualizado e o pagamento
                # recalculado não apareceria em GET /api/sync/pull.
                "atualizado_em": agora,
            })
        if mudancas:
            db.bulk_update_mappings(Plantao, mudancas)

    @staticmethod
    def deletar(db: Session, usuario: User, local_id: int) -> None:
        if not LocalRepository.delete_by_user(db, local_id, usuario.id):
            raise NaoEncontrado("Local não encontrado")
        logger.info("Local %s deletado por %s", local_id, mascarar_email(usuario.email))
