# Backend Plantões (FastAPI + PostgreSQL)

API para armazenar usuários e seus plantões com:
- valores
- horários
- locais
- recorrência
- dados de notificação
- dia de pagamento

## Requisitos
- Python 3.11+
- PostgreSQL

## Configuração
1. Crie o banco no PostgreSQL (exemplo):
   ```sql
   CREATE DATABASE plantoes;
   ```
2. Configure a variável de ambiente `DATABASE_URL`:
   ```bash
   export DATABASE_URL="postgresql+psycopg://postgres:Adaj1982@localhost:5432/plantoes"
   ```
3. Instale dependências:
   ```bash
   pip install -r requirement.txt
   ```

## Rodar
```bash
uvicorn app.main:app --reload
```

## Endpoints principais
- `POST /api/usuarios`
- `GET /api/usuarios`
- `POST /api/plantoes`
- `GET /api/plantoes?usuario_id=<id>`
- `GET /health`


## Integração com Plantoes_app
- CORS está habilitado no backend para permitir chamadas do app frontend durante desenvolvimento.
- Garanta que este backend esteja rodando em `http://localhost:8000` (padrão do uvicorn) e que o app use os endpoints em `/api`.
