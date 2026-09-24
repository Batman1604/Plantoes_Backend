from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


# Base para todos os modelos SQLAlchemy da aplicação.
class Base(DeclarativeBase):
    pass


# Conexão com o banco de dados e fábrica de sessões.
#
# pool_pre_ping testa a conexão com um SELECT 1 antes de entregá-la. Sem isso, a
# primeira requisição depois de um período ocioso falha com "server closed the
# connection unexpectedly": Postgres gerenciado (e qualquer proxy no caminho)
# derruba conexões paradas, e o pool só descobre ao tentar usar. É o clássico
# "só o primeiro acesso da manhã dá erro".
#
# pool_recycle recicla a conexão antes que o servidor a considere velha, o que
# reduz a chance de cair no pre_ping. 1800s fica confortavelmente abaixo dos
# limites usuais de idle timeout.
#
# SQLite (usado pela suíte de testes) não tem pool de rede e rejeita estes
# argumentos, então eles só são aplicados quando há de fato um servidor.
_e_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    future=True,
    **(
        {}
        if _e_sqlite
        else {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
        }
    ),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def instante_para_o_banco(valor: datetime | None) -> datetime | None:
    """Ajusta um instante UTC ao formato que o dialeto em uso guarda nas colunas.

    Existe para permitir filtrar por data em SQL. As colunas são declaradas
    DateTime(timezone=True): o Postgres devolve e compara valores com fuso, mas
    o SQLite — usado pela suíte de testes — não tem tipo de data e guarda tudo
    naive. Comparar um valor com fuso contra uma coluna naive falha ou dá
    resultado errado, dependendo do caminho.

    Era justamente essa divergência que levava SyncService.pull a carregar todas
    as linhas do usuário e filtrar em Python. O custo disso cresce com o
    histórico inteiro, mesmo quando nada mudou: medido em 22 ms para 300
    plantões e 52 ms para 1.500, devolvendo zero itens nos dois casos (ver
    auditoria A-04).
    """
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    if engine.dialect.name == "postgresql":
        return valor
    return valor.astimezone(UTC).replace(tzinfo=None)


def get_db():
    """Dependência do FastAPI para obter uma sessão de banco de dados por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
