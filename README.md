# Backend Plantões (FastAPI + PostgreSQL)

API para armazenar usuários e seus plantões com:
- valores
- horários
- recorrência
- dados de notificação
- locais vinculados por `local_id`

## Requisitos
- Python 3.11+
- PostgreSQL

## Configuração
1. Crie o banco no PostgreSQL (exemplo):
   ```sql
   CREATE DATABASE plantoes;
   ```
2. Garanta as relações entre tabelas:
   - `locais.usuario_id` -> `usuarios.id`
   - `plantoes.local_id` -> `locais.id`
   - A aplicação cria automaticamente essas foreign keys na inicialização.
3. Configure a variável de ambiente `DATABASE_URL`:
   ```bash
   export DATABASE_URL="postgresql+psycopg://postgres:Adaj1982@localhost:5432/plantoes"
   ```
4. Instale dependências:
   ```bash
   pip install -r requirement.txt
   ```

## Rodar
```bash
uvicorn app.main:app --reload
```

## Endpoints principais
- `POST /api/usuarios` (envie `senha`; backend salva apenas o hash)
- `POST /api/auth/login`
- `GET /api/usuarios`
- `POST /api/locais`
- `GET /api/locais?usuario_id=<id>`
- `POST /api/plantoes`
- `GET /api/plantoes?usuario_id=<id>`
- `GET /health`


## Integração com Plantoes_app
- CORS está habilitado no backend para permitir chamadas do app frontend durante desenvolvimento.
- Garanta que este backend esteja rodando em `http://localhost:8000` (padrão do uvicorn) e que o app use os endpoints em `/api`.
