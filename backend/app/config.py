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
    AGENT_RULES_FIRST_ENABLED: bool = True
    AGENT_INTENT_TIMEOUT_SECONDS: float = 6.0
    AGENT_INTENT_TOTAL_TIMEOUT_SECONDS: float = 10.0
    AGENT_INTENT_RETRY_MIN_REMAINING_SECONDS: float = 2.0
    AGENT_INTENT_MAX_TOKENS: int = 1100
    AGENT_MAX_HISTORY_MESSAGES: int = 20
    AGENT_RECURSION_LIMIT: int = 8
    AGENT_PLANNED_EXECUTION_ENABLED: bool = True
    AGENT_PLANNER_TIMEOUT_SECONDS: float = 15.0
    AGENT_REPLANNER_TIMEOUT_SECONDS: float = 30.0
    AGENT_PLANNING_MAX_TOKENS: int = 1200
    AGENT_EXECUTOR_TIMEOUT_SECONDS: float = 20.0
    AGENT_MAX_PLAN_STEPS: int = 3
    AGENT_MAX_TOOL_CALLS: int = 4
    AGENT_MAX_REPLANS: int = 1
    AGENT_MAX_MODEL_CALLS: int = 12
    AGENT_MAX_STEP_DECISIONS: int = 4
    AGENT_DIRECT_STEP_MAX_TOOL_CALLS: int = 1
    AGENT_REACT_STEP_MAX_TOOL_CALLS: int = 2
    AGENT_ASYNC_WORKER_ENABLED: bool = True
    AGENT_WORKER_POLL_SECONDS: float = 0.5
    AGENT_RUN_LEASE_SECONDS: int = 180
    AGENT_RUN_MAX_ATTEMPTS: int = 3


settings = Settings()
