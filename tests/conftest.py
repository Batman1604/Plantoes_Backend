"""Isolamento da suíte de testes.

Problema que este arquivo resolve: cada módulo de teste faz
`os.environ["DATABASE_URL"] = "sqlite:///./test_<nome>.db"` no topo, antes de
importar `app.main`. Mas `app.main` — e portanto `app.core.database.engine` — só
é importado UMA vez em todo o processo: o primeiro módulo que o pytest coleta
define o banco de TODOS os outros. Na prática só dois arquivos .db chegavam a
ser criados, e os módulos seguintes rodavam em cima dos dados uns dos outros.

Somado a isso, o `tearDownClass` de cada módulo tenta apagar o próprio .db com
`except PermissionError: pass` — no Windows o SQLite mantém o handle aberto
(o engine nunca é descartado), a exclusão falha, o `pass` engole o erro e o
banco sobrevive. A execução seguinte começava com os dados da anterior, e a
suíte saía de 26 para 76 falhas sem que nada no código tivesse mudado.

A correção tem duas partes:

1. `DATABASE_URL` é fixado aqui, antes de qualquer módulo de teste ser
   importado (o pytest carrega o conftest primeiro), então o banco usado é
   sempre o mesmo e é sempre conhecido — em vez de depender da ordem de coleta.

2. Um fixture autouse de escopo de módulo limpa todas as tabelas entre módulos.
   A limpeza é por MÓDULO e não por teste de propósito: vários testes montam
   estado em `setUpClass` e o reutilizam nos testes da classe — limpar a cada
   teste quebraria esses fluxos sem revelar nenhum bug real.
"""

import os
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "test_suite.db"

# Precisa acontecer no import do conftest, antes de qualquer `from app.main import app`.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"

# Mesmo problema de ordem, outra variável: test_auth_oauth.py definia estes dois no
# topo do próprio módulo, mas `settings` (app.core.config) é instanciado uma única vez,
# no primeiro import. Se qualquer outro módulo de teste importasse `app.main` antes,
# `google_client_id`/`apple_client_id` já estavam vazios, e OAuthService falhava fechado
# com "Login com Google não está configurado no servidor" — os 10 testes de OAuth
# passavam sozinhos e falhavam com 401 dentro da suíte. Definidos aqui, valem sempre.
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client")
os.environ.setdefault("APPLE_CLIENT_ID", "test-apple-client")

# AUD-012: ENVIRONMENT e JWT_SECRET_KEY passaram a ser obrigatórios, sem default.
# Fixados aqui (e não com setdefault) para a suíte nunca herdar o ambiente de
# quem a roda — nem um .env de produção, nem um segredo real.
os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET_KEY"] = "segredo-da-suite-de-testes-nao-usar-fora-dela"
# A suíte nunca chama o Supabase de verdade: vazias aqui, um .env ou ambiente
# com o projeto real não desvia os testes para ele. Os testes do canal por
# e-mail ligam as duas com patch e substituem o httpx.post.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""

# Um banco deixado para trás por uma execução anterior (ver item 2 do docstring)
# contamina esta — começa sempre do zero.
if _DB_PATH.exists():
    try:
        _DB_PATH.unlink()
    except PermissionError:
        pass

import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _chaves_estrangeiras_ligadas():
    """Liga `PRAGMA foreign_keys` em toda conexão SQLite da suíte (plano 5.11).

    O SQLite vem com as FKs DESLIGADAS. Sem isto, nenhum `ondelete` do
    schema (CASCADE, SET NULL) acontecia nos testes, enquanto no Postgres
    acontece — a exclusão em cascata de solicitações de troca (AUD-025), por
    exemplo, passava despercebida. O pragma vale por conexão, então entra
    como listener do engine, e o pool é descartado para que nenhuma conexão
    aberta antes fique sem ele.
    """
    from sqlalchemy import event

    from app.core.database import engine

    def _ligar(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", _ligar)
    engine.dispose()
    yield
    event.remove(engine, "connect", _ligar)
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _banco_limpo_por_modulo():
    """Garante o schema e zera todas as tabelas antes de cada módulo de teste."""
    from app.core.database import Base, engine
    from app import models  # noqa: F401  (registra as tabelas em Base.metadata)

    # create_all antes de limpar: um módulo que não importa app.main (como
    # test_financeiro_service, que exercita o serviço direto) roda sem nenhuma
    # tabela criada quando executado isoladamente, e a limpeza falhava com
    # "no such table". Era mais uma dependência da ordem de coleta — o módulo
    # só funcionava se OUTRO tivesse importado app.main antes dele.
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        # Ordem reversa de criação respeita as chaves estrangeiras.
        for tabela in reversed(Base.metadata.sorted_tables):
            conn.execute(tabela.delete())
    yield
