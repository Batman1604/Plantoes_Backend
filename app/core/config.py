from typing import Literal

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Configurações da aplicação lidas de variáveis de ambiente ou do arquivo .env.
# Use esta classe para centralizar valores que mudam entre ambientes (desenvolvimento, testes e produção).
class Settings(BaseSettings):
    app_name: str = "Plantões Backend"
    # AUD-012: obrigatório e restrito. Com default "development" e comparação
    # exata com "production", esquecer a variável (ou escrever "prod" ou
    # "Production") desligava TODAS as guardas de produção ao mesmo tempo, sem
    # erro visível. Agora um valor ausente ou fora destes três derruba o import.
    environment: Literal["development", "test", "production"]
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
    # AUD-012: sem default, pelo mesmo motivo do database_url. O default
    # anterior era público (estava no código) e só gerava um warning fora de
    # produção: com ENVIRONMENT errado, qualquer um forjava token de qualquer id.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    # AUD-012: o default era um e-mail pessoal real no código-fonte. Vazio, o
    # feedback falha alto (ver FeedbackService) até FEEDBACK_EMAIL_TO ser definido.
    feedback_email_to: str = ""
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
    # A-12: IPs dos proxies reversos (separados por vírgula) dos quais o
    # limitador aceita X-Forwarded-For. Vazio = nenhum: usa o IP da conexão.
    trusted_proxy_ips: str = ""
    log_level: str = "INFO"
    anthropic_api_key: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    # AUD-089: sem default público (o anterior estava no código-fonte).
    # Obrigatório quando WHATSAPP_TOKEN está definido (validador abaixo).
    whatsapp_verify_token: str = ""
    # Chave secreta do app na Meta (App Dashboard > Configurações básicas), usada para
    # validar a assinatura HMAC (header X-Hub-Signature-256) de cada webhook recebido —
    # sem isso qualquer requisição POST /webhook/whatsapp seria aceita como se viesse da Meta.
    whatsapp_app_secret: str = ""
    upgrade_webhook_secret: str = ""
    # Aceitam múltiplos valores separados por vírgula (ex.: client ID de iOS, Android e Web
    # do mesmo projeto Google, ou Service ID + Bundle ID da Apple).
    google_client_id: str = ""
    apple_client_id: str = ""
    # Supabase Auth como carteiro do código por e-mail (cadastro e redefinição
    # de senha; ver app/services/email_otp_service.py). Só a URL do projeto e a
    # chave anon, que é pública por natureza; a service_role não é usada.
    # Vazias = canal antigo (WhatsApp/SMTP; em dev, código no log).
    supabase_url: str = ""
    supabase_anon_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _whatsapp_exige_assinatura(self) -> "Settings":
        """AUD-012: com WHATSAPP_TOKEN definido o webhook está em uso, e sem o app
        secret ele aceitaria POST sem assinatura (fora de produção) — qualquer um
        falaria com o assistente como o telefone de outro usuário."""
        if self.whatsapp_token and not self.whatsapp_app_secret:
            raise ValueError("WHATSAPP_APP_SECRET é obrigatório quando WHATSAPP_TOKEN está definido.")
        if self.whatsapp_token and not self.whatsapp_verify_token:
            # AUD-089: o default público saiu; sem ele, o webhook em uso precisa de um.
            raise ValueError("WHATSAPP_VERIFY_TOKEN é obrigatório quando WHATSAPP_TOKEN está definido.")
        return self

    @field_validator("supabase_url", "supabase_anon_key", mode="before")
    @classmethod
    def _sem_espacos(cls, valor):
        # Colar no painel do Render costuma trazer espaço ou quebra de linha
        # junto; sem isto, "  " contava como definida e a chave ia inválida.
        return valor.strip() if isinstance(valor, str) else valor

    @model_validator(mode="after")
    def _supabase_completo(self) -> "Settings":
        """Só uma das duas variáveis deixaria o canal por e-mail desligado em
        silêncio, e o cadastro cairia no WhatsApp (502 em produção). A mensagem
        diz qual falta: no deploy, "precisam ser definidas juntas" não bastava
        para achar o erro."""
        if bool(self.supabase_url) != bool(self.supabase_anon_key):
            falta = "SUPABASE_ANON_KEY" if self.supabase_url else "SUPABASE_URL"
            definida = "SUPABASE_URL" if self.supabase_url else "SUPABASE_ANON_KEY"
            raise ValueError(
                f"{falta} está vazia, mas {definida} está definida. Defina as duas "
                "(URL do projeto e chave anon do Supabase) ou nenhuma."
            )
        if self.supabase_url and not self.supabase_url.startswith("https://"):
            raise ValueError("SUPABASE_URL precisa começar com https:// (ex.: https://<ref>.supabase.co).")
        return self


def _carregar_settings() -> Settings:
    """Falha alto nomeando as VARIÁVEIS DE AMBIENTE (em maiúsculas) que faltam ou
    estão inválidas — o erro cru do Pydantic cita o campo em minúsculas, que não
    é o nome que quem faz o deploy procura."""
    try:
        return Settings()
    except ValidationError as exc:
        problemas = []
        for erro in exc.errors():
            nome = ".".join(str(parte) for parte in erro["loc"]).upper() or "CONFIGURAÇÃO"
            problemas.append(f"{nome}: {erro['msg']}")
        raise RuntimeError(
            "Configuração inválida (veja .env.example): " + "; ".join(problemas)
        ) from exc


# Instância única das configurações usadas em toda a aplicação.
settings = _carregar_settings()
