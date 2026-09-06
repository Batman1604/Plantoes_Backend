# Backend Plantões (FastAPI + PostgreSQL)

API para armazenar usuários e seus plantões com:
- valores
- horários
- recorrência
- dados de notificação
- locais vinculados por `local_id`
- usuário dono do plantão em `usuario_id`
- autenticação por JWT

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
   - `plantoes.usuario_id` -> `usuarios.id`
   - A aplicação cria automaticamente essas foreign keys na inicialização.
3. Configure as variáveis de ambiente:
   ```bash
   export DATABASE_URL="INSIRA SUA SENHA DO BANCO"
   export JWT_SECRET_KEY="troque-essa-chave-em-producao"
   # opcional
   export JWT_EXPIRE_MINUTES="1440"
   ```
4. Instale dependências:
   ```bash
   pip install -r requirement.txt
   ```

## Rodar
```bash
uvicorn app.main:app --reload
```
## Caso não funcione a primeira opção
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Fluxo de autenticação JWT
1. Crie um usuário em `POST /api/usuarios`.
2. Faça login em `POST /api/auth/login` com `email` e `senha`.
3. O backend retorna `access_token` (Bearer).
4. Envie header `Authorization: Bearer <token>` nos endpoints protegidos.
5. Para validar sessão, consulte `GET /api/auth/status`.

## Endpoints principais
- Públicos:
  - `POST /api/usuarios` (envie `senha`; backend salva apenas o hash)
  - `POST /api/auth/login`
  - `GET /health`
- Protegidos (JWT obrigatório):
  - `GET /api/auth/status`
  - `GET /api/usuarios`
  - `POST /api/locais`
  - `GET /api/locais?usuario_id=<id>`
  - `POST /api/plantoes`
  - `GET /api/plantoes?usuario_id=<id>`

## Integração com Plantoes_app
- CORS está habilitado no backend para permitir chamadas do app frontend durante desenvolvimento.
- Garanta que este backend esteja rodando em `http://localhost:8000` (padrão do uvicorn) e que o app use os endpoints em `/api`.
