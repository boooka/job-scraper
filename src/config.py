"""Application configuration via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
    schedule_cv: str = Field(default="0 1 * * *")
    schedule_translations: str = Field(default="*/15 * * * *")  # every 15 min
    schedule_subscription_notifications: str = Field(default="*/10 * * * *")
    schedule_daily_report: str = Field(default="0 9 * * *")  # daily admin health report

    # Admin notifications
    admin_notify_new_users: bool = Field(default=True)
    admin_notify_new_subscriptions: bool = Field(default=True)
    # Hours without any new vacancy that trigger a "stale" alert in the report
    daily_report_stale_hours: int = Field(default=24)

    # Retry
    retry_max_attempts: int = Field(default=3)
    retry_wait_seconds: int = Field(default=10)

    # DeepL
    deepl_api_key: str = Field(default="")
    deepl_target_lang: str = Field(default="RU")
    deepl_batch_size: int = Field(default=50)
    deepl_delay_ms: int = Field(default=200)
    deepl_batch_char_quota: int = Field(default=10_000)

    # Telegram bot
    telegram_bot_token: str = Field(default="")
    telegram_admin_usernames: str = Field(default="")
    telegram_poll_timeout_seconds: int = Field(default=20)
    telegram_search_limit: int = Field(default=20)
    telegram_api_max_retries: int = Field(default=5)
    telegram_api_backoff_base_seconds: float = Field(default=0.5)
    telegram_api_backoff_jitter_seconds: float = Field(default=0.25)
    telegram_regex_max_length: int = Field(default=120)
    telegram_regex_max_groups: int = Field(default=10)
    telegram_regex_max_alternations: int = Field(default=12)
    telegram_search_show_all_threshold: int = Field(default=3)
    telegram_search_next_batch_size: int = Field(default=5)
    metrics_top_search_queries: int = Field(default=10)
    metrics_dump_path: str = Field(default="logs/metrics/metrics.jsonl")
    metrics_dump_interval_seconds: int = Field(default=300)

    # Telegram debug/audit logging
    telegram_debug_logging: bool = Field(default=True)
    telegram_debug_log_path: str = Field(default="logs/telegram/debug.log")

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    app_log_path: str = Field(default="logs/app/app.log")
    scraper_log_path: str = Field(default="logs/scraper/scraper.log")


settings = Settings()
