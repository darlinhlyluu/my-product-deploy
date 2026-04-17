from typing import List
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"
    debug: bool = True
    
    app_name: str = "Production AI Agent"
    app_version: str = "1.0.0"
    
    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    
    agent_api_key: str = "dev-key-change-me"
    allowed_origins: str = "*"

    rate_limit_per_minute: int = 10
    daily_budget_usd: float = 10.0
    
    redis_url: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def get_allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

settings = Settings()
