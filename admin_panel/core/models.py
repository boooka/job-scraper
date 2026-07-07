"""Unmanaged Django models mirroring the Alembic-managed schema.

The source of truth for these tables' schema is src/models/orm.py and the
Alembic migrations in migrations/versions/. These models never generate or
apply migrations of their own (managed = False) — they only give the Django
admin a way to read and write the existing data.
"""
from __future__ import annotations

from django.db import models


class Company(models.Model):
    id = models.UUIDField(primary_key=True)
    source = models.CharField(max_length=50)
    external_id = models.CharField(max_length=255)

    name = models.CharField(max_length=255)
    employee_count = models.IntegerField(null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    office_address = models.TextField(null=True, blank=True)
    contact_person = models.CharField(max_length=255, null=True, blank=True)

    extra = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "companies"
        verbose_name = "Company"
        verbose_name_plural = "Companies"

    def __str__(self) -> str:
        return f"{self.source}:{self.name}"


class City(models.Model):
    id = models.AutoField(primary_key=True)
    name_en = models.CharField(max_length=255, unique=True)
    name_translated = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "cities"
        verbose_name = "City"
        verbose_name_plural = "Cities"

    def __str__(self) -> str:
        return self.name_translated or self.name_en


class Vacancy(models.Model):
    id = models.UUIDField(primary_key=True)
    source = models.CharField(max_length=50)
    external_id = models.CharField(max_length=255)

    company = models.ForeignKey(
        Company,
        related_name="vacancies",
        db_column="company_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
    )
    company_name = models.CharField(max_length=255, null=True, blank=True)

    city = models.ForeignKey(
        City,
        related_name="vacancies",
        db_column="city_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=500)
    location = models.CharField(max_length=255, null=True, blank=True)
    url = models.TextField()
    description = models.TextField(null=True, blank=True)
    page_html = models.TextField(null=True, blank=True)

    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    salary_currency = models.CharField(max_length=10, null=True, blank=True)
    salary_period = models.CharField(max_length=20, null=True, blank=True)
    salary_type = models.CharField(max_length=20, null=True, blank=True)

    welcome_ukraine = models.BooleanField(default=False)

    extra = models.JSONField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "vacancies"
        verbose_name = "Vacancy"
        verbose_name_plural = "Vacancies"

    def __str__(self) -> str:
        return f"{self.source}:{self.external_id} {self.title}"


class VacancyTranslation(models.Model):
    id = models.BigAutoField(primary_key=True)
    vacancy = models.ForeignKey(
        Vacancy,
        related_name="translations",
        db_column="vacancy_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
    )
    language = models.CharField(max_length=10)

    title_translated = models.CharField(max_length=500, null=True, blank=True)
    description_translated = models.TextField(null=True, blank=True)

    translated_at = models.DateTimeField()
    translator = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "vacancy_translations"
        verbose_name = "Vacancy translation"
        verbose_name_plural = "Vacancy translations"

    def __str__(self) -> str:
        return f"{self.vacancy_id} [{self.language}]"


class TranslationCache(models.Model):
    id = models.BigAutoField(primary_key=True)
    language = models.CharField(max_length=10)
    text_hash = models.CharField(max_length=64)
    source_text = models.TextField()
    translated_text = models.TextField()
    translator = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "translation_cache"
        verbose_name = "Translation cache entry"
        verbose_name_plural = "Translation cache"

    def __str__(self) -> str:
        return f"{self.language}:{self.text_hash[:8]}"


class VacancyChange(models.Model):
    id = models.BigAutoField(primary_key=True)
    vacancy = models.ForeignKey(
        Vacancy,
        related_name="changes",
        db_column="vacancy_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
    )
    changed_at = models.DateTimeField()
    field_name = models.CharField(max_length=100)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "vacancy_changes"
        verbose_name = "Vacancy change"
        verbose_name_plural = "Vacancy changes"

    def __str__(self) -> str:
        return f"{self.vacancy_id} {self.field_name}"


class ScrapeRun(models.Model):
    id = models.BigAutoField(primary_key=True)
    source = models.CharField(max_length=50)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="running")
    error_message = models.TextField(null=True, blank=True)

    vacancies_found = models.IntegerField(default=0)
    new_count = models.IntegerField(default=0)
    changed_count = models.IntegerField(default=0)
    deactivated_count = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = "scrape_runs"
        verbose_name = "Scrape run"
        verbose_name_plural = "Scrape runs"

    def __str__(self) -> str:
        return f"{self.source} {self.started_at} [{self.status}]"


class TelegramSubscription(models.Model):
    id = models.BigAutoField(primary_key=True)
    telegram_user_id = models.BigIntegerField()
    username = models.CharField(max_length=255, null=True, blank=True)
    chat_id = models.BigIntegerField()
    query = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField()
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "telegram_subscriptions"
        verbose_name = "Telegram subscription"
        verbose_name_plural = "Telegram subscriptions"

    def __str__(self) -> str:
        return f"{self.telegram_user_id} ({'active' if self.is_active else 'inactive'})"


class TelegramUser(models.Model):
    id = models.BigAutoField(primary_key=True)
    telegram_user_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=255, null=True, blank=True)
    first_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(max_length=255, null=True, blank=True)
    language_code = models.CharField(max_length=32, null=True, blank=True)
    is_bot = models.BooleanField(default=False)
    is_premium = models.BooleanField(null=True, blank=True)
    last_chat_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "telegram_users"
        verbose_name = "Telegram user"
        verbose_name_plural = "Telegram users"

    def __str__(self) -> str:
        return f"{self.telegram_user_id} @{self.username or '-'}"


class TelegramSubscriptionDelivery(models.Model):
    id = models.BigAutoField(primary_key=True)
    subscription = models.ForeignKey(
        TelegramSubscription,
        related_name="deliveries",
        db_column="subscription_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
    )
    vacancy = models.ForeignKey(
        Vacancy,
        related_name="deliveries",
        db_column="vacancy_id",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
    )
    sent_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "telegram_subscription_deliveries"
        verbose_name = "Telegram delivery"
        verbose_name_plural = "Telegram deliveries"

    def __str__(self) -> str:
        return f"sub={self.subscription_id} vacancy={self.vacancy_id}"
