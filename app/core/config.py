from pydantic_settings import BaseSettings, SettingsConfigDict


# Configurações da aplicação lidas de variáveis de ambiente ou do arquivo .env.
# Use esta classe para centralizar valores que mudam entre ambientes (desenvolvimento, testes e produção).
class Settings(BaseSettings):
    app_name: str = "Plantões Backend"
    environment: str = "development"
    # database_url: str = "postgresql+psycopg://axelpalmeira:Adaj1982@localhost:5432/postgres"
    database_url: str = "postgresql+psycopg://postgres:DeusAbutre@localhost:5432/plantoes"
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
    log_level: str = "INFO"
    anthropic_api_key: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "plantoes_whatsapp_verify"
    upgrade_webhook_secret: str = ""
    # Aceitam múltiplos valores separados por vírgula (ex.: client ID de iOS, Android e Web
    # do mesmo projeto Google, ou Service ID + Bundle ID da Apple).
    google_client_id: str = ""
    apple_client_id: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Instância única das configurações usadas em toda a aplicação.
settings = Settings()
