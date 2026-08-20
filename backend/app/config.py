from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"

    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""
    WECHAT_LOGIN_TIMEOUT_SECONDS: float = 5.0

    RAG_ENABLED: bool = False
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_MODEL: str = ""
    EMBEDDING_DIM: int = 2048

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_CHAT_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_REASONING_MODEL: str = "deepseek-v4-pro"

    AGENT_ENABLED: bool = True
    AGENT_MODEL: str = "deepseek-v4-flash"
    AGENT_TIMEOUT_SECONDS: float = 60.0
    AGENT_INTENT_MODEL_ENABLED: bool = True
    AGENT_INTENT_MODEL: str = "deepseek-v4-flash"
    AGENT_INTENT_TIMEOUT_SECONDS: float = 15.0
    AGENT_INTENT_MAX_TOKENS: int = 1100
    AGENT_MAX_HISTORY_MESSAGES: int = 20
    AGENT_RECURSION_LIMIT: int = 8
    AGENT_ASYNC_WORKER_ENABLED: bool = True
    AGENT_WORKER_POLL_SECONDS: float = 0.5
    AGENT_RUN_LEASE_SECONDS: int = 180
    AGENT_RUN_MAX_ATTEMPTS: int = 3


settings = Settings()
