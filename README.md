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
3. Configure as variáveis de ambiente copiando `.env.example` para `.env`.
   São obrigatórias `DATABASE_URL`, `ENVIRONMENT` (`development`, `test` ou
   `production`, exatamente) e `JWT_SECRET_KEY`; sem elas o servidor não sobe
   (AUD-012). Em produção, `ALLOWED_ORIGINS` também.
4. Instale dependências:
   ```bash
   pip install -r requirements.txt -r requirements-dev.txt
   ```

## Migrar o banco

O schema é versionado pelo Alembic. Rode ANTES de subir a aplicação, e a cada
atualização do código:

```bash
alembic upgrade head
```

A aplicação confere isso na inicialização: em produção ela recusa subir se o
banco não estiver na última migração, porque uma coluna que falta não dá erro
na hora — aparece como UndefinedColumn no meio de uma requisição do usuário.
Fora de produção, apenas avisa.

Para criar uma migração depois de alterar `app/models.py`:

```bash
alembic revision --autogenerate -m "descrição curta"
```

Revise o arquivo gerado antes de aplicar: o autogenerate acerta colunas e
índices, mas não adivinha renomeação nem migração de dados.

## Rodar
```bash
uvicorn app.main:app --reload
```
## Caso não funcione a primeira opção
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Limite de requisições

O limitador (`app/core/rate_limiter.py`) conta em memória, **por processo**:
com vários workers do uvicorn ou várias réplicas, cada processo conta sozinho
e o limite efetivo é multiplicado pelo número de processos (A-12). Ele age
em produção (ou com `RATE_LIMIT_ENABLED=1`), por IP e, em `/api/sync/push`,
criação de `/api/solicitacoes-troca`, `/api/feedback` e criação de
`/api/amizades`, também por usuário autenticado.

Atrás de proxy reverso, defina `TRUSTED_PROXY_IPS` com o IP do proxy
(separados por vírgula). Só então o `X-Forwarded-For` é lido; de qualquer
outra origem ele é ignorado, porque é forjável. Sem isso, todos os usuários
dividem o limite do IP do proxy.

No Render o proxy não tem IP fixo: use `TRUSTED_PROXY_IPS=*` (o `render.yaml`
já faz isso). Com `*`, vale só o **último** item do `X-Forwarded-For`, o que o
proxy acrescentou; os anteriores vêm do cliente e são forjáveis.

## Fluxo de autenticação JWT
1. Cadastre-se em `POST /api/auth/cadastro` e confirme o código em
   `POST /api/auth/confirmar-cadastro` (o antigo `POST /api/usuarios`, que
   criava a conta sem verificação, foi removido — AUD-011).
2. Faça login em `POST /api/auth/login` com `email` e `senha`.
3. O backend retorna `access_token` (Bearer).
4. Envie header `Authorization: Bearer <token>` nos endpoints protegidos.
5. Para validar sessão, consulte `GET /api/auth/status`.

## Endpoints principais
- Públicos:
  - `POST /api/auth/cadastro` e `POST /api/auth/confirmar-cadastro`
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

## Deploy no Render + Supabase

```
App ──HTTPS──► backend no Render ──Postgres (Session pooler)──► Supabase
                      └──HTTPS──► Supabase Auth: só envia e confere o código por e-mail
```

O app nunca fala com o Supabase. Usuários, sessões (JWT próprio) e tabelas
ficam no backend; o Supabase Auth só entrega e confere o código de 6 dígitos do
cadastro e da redefinição de senha. O backend usa só a URL do projeto e a chave
**anon** (pública por natureza). A `service_role` não é usada em lugar nenhum:
não a coloque no Render.

### 1. Supabase: dados de conexão

1. **Região do projeto:** Project Settings → General. Ela também aparece no
   host do pooler (`aws-<N>-us-east-1...` = Virgínia; `us-west-1`/`us-west-2`
   = Oregon). O `render.yaml` usa `region: oregon`, porque o projeto atual é
   us-west-2. Se o projeto estiver no leste (`us-east-1`), troque para
   `virginia` **antes** de criar o Blueprint: a região do serviço não muda depois.
2. **`DATABASE_URL`:** botão **Connect** → **Session pooler** (porta 5432,
   funciona em IPv4). Não use a conexão direta (`db.<ref>.supabase.co`, só
   IPv6) nem o Transaction pooler (6543, recusa os prepared statements do
   psycopg 3). Na string copiada:
   - troque `postgresql://` por `postgresql+psycopg://`;
   - ponha a senha do banco no lugar de `[YOUR-PASSWORD]`, com caracteres
     especiais percent-encoded (`@` vira `%40`, `#` vira `%23`...);
   - acrescente `?sslmode=require` no fim.

   Fica assim:
   `postgresql+psycopg://postgres.<ref>:<SENHA>@aws-<N>-<regiao>.pooler.supabase.com:5432/postgres?sslmode=require`
3. **`SUPABASE_URL`:** `https://<ref>.supabase.co` (Project Settings → Data API).
4. **`SUPABASE_ANON_KEY`:** Project Settings → API Keys → chave `anon`
   (*public*). Nunca a `service_role`/*secret*.

### 2. Supabase: painel de Authentication

1. **Sign In / Providers → Email:** habilitado. "Allow new users to sign up"
   precisa ficar **ligado**: o backend pede o código com `create_user: true`, e
   o Supabase cria o usuário dele na primeira vez. "Confirm email" pode ficar
   ligado.
2. **Email OTP Expiration** (no mesmo provedor Email): **600** segundos (10
   minutos), a mesma validade que o backend aplica do lado dele. O padrão do
   Supabase é 3600; vale o menor dos dois. Os dois são **maiores que os 60 s**
   da contagem regressiva da tela de código do app: depois dela o código
   ainda vale até os 10 minutos. **Email OTP Length:** 6.
3. **Emails → Templates:** o corpo precisa ter `{{ .Token }}` (o código), não
   só o link. Cole o mesmo template em **"Magic Link"** e em **"Confirm
   signup"**: o Supabase usa o "Confirm signup" no primeiro código de um e-mail
   que ele ainda não conhece (com "Confirm email" ligado) e o "Magic Link" nos
   seguintes.

   Assunto: `Seu código do Plantões: {{ .Token }}`

   Corpo:

   ```html
   <h2>Seu código do Plantões</h2>
   <p>Digite este código no app para continuar:</p>
   <p style="font-size:32px;font-weight:bold;letter-spacing:8px;margin:24px 0">{{ .Token }}</p>
   <p>O código vale por 10 minutos e só pode ser usado uma vez.</p>
   <p>Se você não pediu este código, ignore este e-mail. Ninguém do Plantões vai pedir que você o informe.</p>
   ```

4. **Limite do e-mail embutido do Supabase:** sem SMTP próprio, o Supabase
   **só entrega para endereços de membros da organização** (Team) e com
   poucos envios por hora (Authentication → Rate Limits). Serve para testar;
   para usuários reais, configure um SMTP próprio em **Authentication → SMTP
   Settings** (Resend, Brevo, Amazon SES, etc.) e suba o limite de e-mails por
   hora em Rate Limits. Endereço fora da organização sem SMTP próprio = o
   backend responde 502 ("Não foi possível enviar o código para o seu e-mail")
   e o log mostra `email_address_not_authorized`.
5. **Rate Limits:** o Supabase também limita por IP, e todas as chamadas saem
   do IP do servidor do Render: o limite por IP de verificações vale para
   todos os usuários juntos. O intervalo mínimo entre dois e-mails para o
   mesmo endereço (padrão 60 s) chega ao app como 429 "Aguarde N segundos para
   pedir outro código.".

### 3. Render: criar o serviço pelo Blueprint

1. Em <https://dashboard.render.com>: **New → Blueprint**, conecte o GitHub e
   escolha este repositório e a branch que tem o `render.yaml`.
2. O Render lê o `render.yaml` e pede os valores marcados `sync: false`:
   `DATABASE_URL`, `SUPABASE_URL` e `SUPABASE_ANON_KEY` (passo 1). O
   `JWT_SECRET_KEY` é gerado pelo Render; `ENVIRONMENT=production`,
   `ALLOWED_ORIGINS`, `TRUSTED_PROXY_IPS=*` e `PYTHON_VERSION` já vêm do
   arquivo.
3. **Apply.** O build roda `pip install -r requirements.txt`; o start roda
   `alembic upgrade head` e depois o uvicorn. Se a migração falhar, o serviço
   não sobe (o deploy anterior continua no ar) e o erro fica no log.
4. O health check é `GET /health`: 200 com banco e worker de notificações
   ativos, 503 senão.
5. Confira em `https://plantoes-backend.onrender.com/health`. Se o nome
   `plantoes-backend` estiver ocupado, o Render dá outro: atualize
   `ALLOWED_ORIGINS` no painel com a URL real e use essa URL no app
   (`--dart-define=API_BASE_URL=...`).

Variáveis do serviço, conferidas em Environment:

| Variável | Valor |
|---|---|
| `PYTHON_VERSION` | `3.13.2` (do arquivo) |
| `ENVIRONMENT` | `production` (do arquivo) |
| `JWT_SECRET_KEY` | gerado pelo Render |
| `DATABASE_URL` | Session pooler, `postgresql+psycopg://...?sslmode=require` |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | chave anon do projeto |
| `ALLOWED_ORIGINS` | `https://plantoes-backend.onrender.com` (do arquivo) |
| `TRUSTED_PROXY_IPS` | `*` (do arquivo) |

### 4. Logs

No painel do serviço: **Logs** (tempo real, com busca). O que procurar:

- `Supabase Auth`: envio e conferência do código. Só o `error_code` do
  Supabase vai ao log (ex.: `over_email_send_rate_limit`,
  `email_address_not_authorized`, `otp_expired`), com o e-mail mascarado
  (`f*****@exemplo.com`).
- `O banco não está na última migração`: o start não rodou o Alembic.
- `Configuração inválida (veja .env.example)`: falta ou sobra variável
  (ex.: só uma de `SUPABASE_URL`/`SUPABASE_ANON_KEY`).

### 5. Plano gratuito

O serviço gratuito **dorme** depois de 15 minutos sem requisições. A primeira
requisição seguinte acorda o serviço e pode levar perto de um minuto (o app
pode mostrar falha de rede nessa hora; tente de novo). Enquanto dorme, o
worker de notificações também para. O start roda `alembic upgrade head` a
cada vez que acorda; com o banco em dia, não faz nada.
