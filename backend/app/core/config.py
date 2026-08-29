from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Delivery Flow"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    cors_origins: str = "http://localhost:5173"
    database_url: str = "sqlite:///./second_opinion.db"

    # Model tiers (DEC-007): personas debate on Sonnet, the judge synthesizes on
    # Opus, cheap utility calls (e.g. stance framing) run on Haiku.
    model_personas: str = "claude-sonnet-5"
    model_judge: str = "claude-opus-4-8"
    model_utility: str = "claude-haiku-4-5-20251001"

    # Cost guardrails (PRD §9): hard caps enforced before every LLM call.
    max_personas: int = 3
    max_rounds: int = 2
    max_tokens_per_debate: int = 200_000

    # Per-turn resilience: retry transient failures with exponential backoff.
    llm_max_retries: int = 2
    llm_backoff_base_seconds: float = 0.5

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
