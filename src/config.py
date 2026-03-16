"""Application configuration via pydantic-settings."""
from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://scraper:scraper_pass@localhost:5432/job_scraper"
    )
    database_pool_size: int = Field(default=10)
    database_max_overflow: int = Field(default=20)

    # Playwright
    scraper_headless: bool = Field(default=True)
    scraper_timeout_ms: int = Field(default=30_000)
    scraper_slow_mo_ms: int = Field(default=500)
    scraper_concurrency: int = Field(default=2)

    # Scheduler (cron)
    schedule_cvbankas: str = Field(default="0 */4 * * *")
    schedule_cvonline: str = Field(default="30 */4 * * *")
    schedule_cvmarket: str = Field(default="0 1 * * *")

    # Retry
    retry_max_attempts: int = Field(default=3)
    retry_wait_seconds: int = Field(default=10)

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")


settings = Settings()
