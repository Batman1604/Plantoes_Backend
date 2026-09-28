"""Erros de domínio, independentes da camada HTTP.

Existem para que a regra de negócio possa recusar uma operação sem que o
serviço precise conhecer FastAPI. Antes, a regra vivia dentro dos handlers de
rota e levantava HTTPException — o que obrigava o SyncService a importar e
chamar os próprios handlers, e a tratar HTTPException como fluxo de controle
(ver auditoria A-13).

Sobre `status_http`: idealmente um erro de domínio não saberia nada de HTTP. A
exceção é deliberada e tem motivo concreto — o protocolo de sincronização já
expõe status HTTP ao cliente. SyncErro.status_code é lido pelo app Flutter, que
trata 410 como "excluído em outro dispositivo" e converge sozinho (ver
SyncEngine._applyPushResponse). Esse mapeamento precisa existir em algum lugar;
mantê-lo junto do erro evita a alternativa pior, que seria repetir a mesma
tabela de tradução na camada de rotas e de novo no serviço de sincronização.
"""


class ErroDeDominio(Exception):
    """Base de toda recusa vinda da regra de negócio."""

    status_http = 400

    def __init__(self, mensagem: str):
        super().__init__(mensagem)
        self.mensagem = mensagem


class NaoEncontrado(ErroDeDominio):
    """Recurso inexistente, ou que não pertence a quem pediu.

    Os dois casos devolvem a MESMA resposta de propósito: distinguir "não
    existe" de "não é seu" contaria a um atacante quais ids existem.
    """

    status_http = 404


class Conflito(ErroDeDominio):
    """A operação contradiz o estado atual (ex.: plantão já transferido)."""

    status_http = 409


class RequisicaoInvalida(ErroDeDominio):
    """Entrada aceita pelo schema, mas sem sentido para a regra de negócio."""

    status_http = 400


class SemPermissao(ErroDeDominio):
    """O recurso existe, mas não pertence a quem pediu.

    Usado apenas onde o comportamento atual já devolvia 403 — bonificação, por
    exemplo, responde 404 ao criar sobre plantão alheio e 403 ao editar ou
    apagar. A inconsistência é anterior a esta refatoração e foi preservada de
    propósito: mudar status de resposta quebraria o cliente. Em código novo,
    prefira NaoEncontrado, que não revela a existência do recurso.
    """

    status_http = 403
