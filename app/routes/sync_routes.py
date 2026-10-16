import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.mascaras import mascarar_email
from app.core.rate_limiter import rate_limit, rate_limit_por_usuario
from app.routes.plantao_routes import get_current_user
from app.schemas.plantao_schema import APIResponse, SyncPullResponse, SyncPushRequest, SyncPushResponse
from app.services.sync_service import SyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post(
    "/push",
    response_model=APIResponse[SyncPushResponse],
    summary="Enviar alterações feitas offline",
    description=(
        "Recebe o estado completo (não um diff) de cada local/plantão alterado no "
        "dispositivo enquanto offline — criações, edições e exclusões. Cada item precisa de "
        "um uuid_cliente estável gerado no dispositivo. Aplica 'last-write-wins' comparando "
        "atualizado_em: se o servidor tiver uma versão igual ou mais recente, o item entra "
        "em `conflitos` na resposta e NÃO é sobrescrito — cabe ao app decidir o que fazer "
        "(ex.: pedir a versão mais nova com /pull e mesclar manualmente). Reaproveita as "
        "mesmas regras de POST/PATCH/DELETE /api/locais e /api/plantoes (conflito de "
        "horário, limite do plano free, etc.); erros de um item não cancelam os demais — "
        "aparecem em `erros`."
    ),
)
def push(
    payload: SyncPushRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    # A-12: o push aplica lotes inteiros no banco. Teto por IP generoso (NAT
    # do hospital) e por conta (o motor do app empurra em rajadas curtas).
    _ip=Depends(rate_limit(300, 60)),
    _usuario=Depends(rate_limit_por_usuario(get_current_user, 60, 60)),
):
    resultado = SyncService.push(db, current_user, payload.locais, payload.plantoes, payload.trocas, payload.bonificacoes)
    logger.info(
        "Sync push de %s: %s locais / %s plantões / %s trocas / %s bonificações criados, "
        "%s/%s/-/%s atualizados, %s/%s/%s/%s excluídos, %s conflitos, %s erros",
        mascarar_email(current_user.email),
        len(resultado.locais_criados), len(resultado.plantoes_criados),
        len(resultado.trocas_criadas), len(resultado.bonificacoes_criadas),
        len(resultado.locais_atualizados), len(resultado.plantoes_atualizados), len(resultado.bonificacoes_atualizadas),
        len(resultado.locais_deletados), len(resultado.plantoes_deletados),
        len(resultado.trocas_deletadas), len(resultado.bonificacoes_deletadas),
        len(resultado.conflitos), len(resultado.erros),
    )
    return APIResponse(success=True, message="Sincronização enviada.", data=resultado)


@router.get(
    "/pull",
    response_model=APIResponse[SyncPullResponse],
    summary="Buscar alterações feitas na nuvem",
    description=(
        "Retorna tudo que mudou (criado, editado ou excluído) para o usuário autenticado "
        "desde `since`. Omita `since` para uma sincronização completa (primeira vez no "
        "dispositivo ou reinstalação). Guarde `servidor_timestamp` da resposta e use como "
        "`since` na próxima chamada — não use o relógio do próprio dispositivo como cursor, "
        "para não perder alterações por causa de diferença de fuso/relógio."
    ),
)
def pull(
    since: datetime | None = Query(default=None, description="Timestamp da última sincronização bem-sucedida (ISO 8601)."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    resultado = SyncService.pull(db, current_user, since)
    return APIResponse(success=True, message="Sincronização recebida.", data=resultado)
