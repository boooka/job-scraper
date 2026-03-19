"""SQLAlchemy ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    """Company information extracted from vacancy listings."""

    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_company_source_external_id"),
        Index("ix_companies_name", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    employee_count: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(100))
    office_address: Mapped[str | None] = mapped_column(Text)
    contact_person: Mapped[str | None] = mapped_column(String(255))

    extra: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    vacancies: Mapped[list[Vacancy]] = relationship("Vacancy", back_populates="company_ref")

    def __repr__(self) -> str:
        return f"<Company {self.source}:{self.name!r}>"


class Vacancy(Base):
    """A job vacancy scraped from an external source."""

    __tablename__ = "vacancies"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_vacancy_source_external_id"),
        Index("ix_vacancies_source", "source"),
        Index("ix_vacancies_is_active", "is_active"),
        Index("ix_vacancies_last_seen_at", "last_seen_at"),
        Index("ix_vacancies_company_id", "company_id"),
        Index("ix_vacancies_welcome_ukraine", "welcome_ukraine"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Company FK (replaces inline company string)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    company_name: Mapped[str | None] = mapped_column(String(255))  # denorm for quick display

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    page_html: Mapped[str | None] = mapped_column(Text)  # raw HTML of vacancy detail page

    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(10))
    salary_period: Mapped[str | None] = mapped_column(String(20))   # month / hour
    salary_type: Mapped[str | None] = mapped_column(String(20))     # gross / net

    welcome_ukraine: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    extra: Mapped[dict | None] = mapped_column(JSONB)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    company_ref: Mapped[Company | None] = relationship("Company", back_populates="vacancies")
    changes: Mapped[list[VacancyChange]] = relationship(
        "VacancyChange", back_populates="vacancy", cascade="all, delete-orphan"
    )
    translations: Mapped[list[VacancyTranslation]] = relationship(
        "VacancyTranslation", back_populates="vacancy", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Vacancy {self.source}:{self.external_id} '{self.title}'>"


class VacancyTranslation(Base):
    """Translations of vacancy title and description for search."""

    __tablename__ = "vacancy_translations"
    __table_args__ = (
        UniqueConstraint("vacancy_id", "language", name="uq_translation_vacancy_language"),
        Index("ix_vacancy_translations_vacancy_id", "vacancy_id"),
        Index("ix_vacancy_translations_language", "language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)  # "ru", "en", etc.

    title_translated: Mapped[str | None] = mapped_column(String(500))
    description_translated: Mapped[str | None] = mapped_column(Text)

    translated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    translator: Mapped[str | None] = mapped_column(String(50))  # "deepl" / "google" / "llm"

    vacancy: Mapped[Vacancy] = relationship("Vacancy", back_populates="translations")

    def __repr__(self) -> str:
        return f"<VacancyTranslation {self.vacancy_id} [{self.language}]>"


class TranslationCache(Base):
    """Canonical cache of previously translated source texts."""

    __tablename__ = "translation_cache"
    __table_args__ = (
        UniqueConstraint("language", "text_hash", name="uq_translation_cache_language_hash"),
        Index("ix_translation_cache_language", "language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    translator: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<TranslationCache {self.language}:{self.text_hash[:8]}>"


class VacancyChange(Base):
    """Audit log of field-level changes to a vacancy."""

    __tablename__ = "vacancy_changes"
    __table_args__ = (
        Index("ix_vacancy_changes_vacancy_id", "vacancy_id"),
        Index("ix_vacancy_changes_changed_at", "changed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)

    vacancy: Mapped[Vacancy] = relationship("Vacancy", back_populates="changes")

    def __repr__(self) -> str:
        return f"<VacancyChange {self.vacancy_id} {self.field_name}: {self.old_value!r} → {self.new_value!r}>"


class ScrapeRun(Base):
    """Log of each scraper execution."""

    __tablename__ = "scrape_runs"
    __table_args__ = (
        Index("ix_scrape_runs_source", "source"),
        Index("ix_scrape_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/failed
    error_message: Mapped[str | None] = mapped_column(Text)

    vacancies_found: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    deactivated_count: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self) -> str:
        return f"<ScrapeRun {self.source} {self.started_at} [{self.status}]>"


class TelegramSubscription(Base):
    """User subscription for periodic vacancy search queries."""

    __tablename__ = "telegram_subscriptions"
    __table_args__ = (
        Index("ix_telegram_subscriptions_user", "telegram_user_id"),
        Index("ix_telegram_subscriptions_chat", "chat_id"),
        Index("ix_telegram_subscriptions_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<TelegramSubscription {self.telegram_user_id} active={self.is_active}>"


class TelegramSubscriptionDelivery(Base):
    """Dedup log of delivered vacancies for each subscription."""

    __tablename__ = "telegram_subscription_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "vacancy_id",
            name="uq_telegram_delivery_subscription_vacancy",
        ),
        Index("ix_telegram_delivery_subscription_id", "subscription_id"),
        Index("ix_telegram_delivery_vacancy_id", "vacancy_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vacancies.id", ondelete="CASCADE"),
        nullable=False,
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )