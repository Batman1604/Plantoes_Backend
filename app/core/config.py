from pydantic_settings import BaseSettings, SettingsConfigDict


# Configurações da aplicação lidas de variáveis de ambiente ou do arquivo .env.
# Use esta classe para centralizar valores que mudam entre ambientes (desenvolvimento, testes e produção).
class Settings(BaseSettings):
    app_name: str = "Plantões Backend"
    environment: str = "development"
    # SEM valor padrão, de propósito: o default anterior trazia a senha real do
    # Postgres de desenvolvimento no código-fonte, e portanto no histórico do
    # git. Pior, a guarda de produção em app/main.py procurava o sentinela
    # "changeme-local-dev", que havia sido substituído por essa senha — então
    # a proteção nunca disparava e um deploy sem DATABASE_URL subiria tentando
    # credenciais de desenvolvimento.
    #
    # Obrigatório significa que a ausência falha no import, em qualquer
    # ambiente, nomeando o campo que falta — em vez de seguir em frente com um
    # valor que ninguém escolheu.
    database_url: str
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    feedback_email_to: str = "bps.software1@gmail.com"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    allowed_origins: str = "*"
    # Liga o limitador de requisições. Vazio = automático: ativo em produção,
    # inativo fora dela. Existe como variável para que o limite possa ser
    # exercitado em teste — antes ele era desligado por uma comparação fixa com
    # "production", então nenhuma proteção de força bruta ou de varredura era
    # verificada por nenhum teste (ver auditoria A-12/C-09).
    rate_limit_enabled: str = ""
    log_level: str = "INFO"
    anthropic_api_key: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "plantoes_whatsapp_verify"
    # Chave secreta do app na Meta (App Dashboard > Configurações básicas), usada para
    # validar a assinatura HMAC (header X-Hub-Signature-256) de cada webhook recebido —
    # sem isso qualquer requisição POST /webhook/whatsapp seria aceita como se viesse da Meta.
    whatsapp_app_secret: str = ""
    upgrade_webhook_secret: str = ""
    # Aceitam múltiplos valores separados por vírgula (ex.: client ID de iOS, Android e Web
    # do mesmo projeto Google, ou Service ID + Bundle ID da Apple).
    google_client_id: str = ""
    apple_client_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Instância única das configurações usadas em toda a aplicação.
settings = Settings()
