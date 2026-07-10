"""Admin site registrations for the job-scraper data model."""

from __future__ import annotations

import re

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    City,
    Company,
    CompanyGroup,
    Schedule,
    ScrapeRun,
    TelegramSubscription,
    TelegramSubscriptionDelivery,
    TelegramUser,
    TranslationCache,
    Vacancy,
    VacancyChange,
    VacancyTranslation,
)

_CRON_FIELD = re.compile(r"[A-Za-z0-9*/,\-]+")


class ReadOnlyAdminMixin:
    """Marks a ModelAdmin as view-only — no add/change/delete through the UI.

    Used for audit-log / technical tables (scrape runs, change history,
    translation cache, delivery dedup log) that should never be hand-edited.
    """

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class VacancyTranslationInline(admin.TabularInline):
    model = VacancyTranslation
    extra = 0
    fields = ("language", "title_translated", "translator", "translated_at")
    readonly_fields = ("translated_at",)


class VacancyChangeInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = VacancyChange
    extra = 0
    fields = ("changed_at", "field_name", "old_value", "new_value")
    ordering = ("-changed_at",)
    can_delete = False


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name_en", "name_translated", "vacancy_count", "updated_at")
    search_fields = ("name_en", "name_translated")
    ordering = ("name_en",)
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_vacancy_count=Count("vacancies", filter=Q(vacancies__is_active=True)))
        )

    @admin.display(description="Active vacancies", ordering="_vacancy_count")
    def vacancy_count(self, obj):
        return getattr(obj, "_vacancy_count", 0)


@admin.register(CompanyGroup)
class CompanyGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "company_count", "vacancy_count", "normalized_key", "updated_at")
    search_fields = ("name", "normalized_key")
    ordering = ("name",)
    readonly_fields = ("normalized_key", "created_at", "updated_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _company_count=Count("companies", distinct=True),
                _vacancy_count=Count(
                    "companies__vacancies",
                    filter=Q(companies__vacancies__is_active=True),
                    distinct=True,
                ),
            )
        )

    @admin.display(description="Companies", ordering="_company_count")
    def company_count(self, obj):
        return getattr(obj, "_company_count", 0)

    @admin.display(description="Active vacancies", ordering="_vacancy_count")
    def vacancy_count(self, obj):
        return getattr(obj, "_vacancy_count", 0)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "source",
        "group",
        "vacancy_count",
        "country",
        "employee_count",
        "updated_at",
    )
    list_filter = ("source", "country")
    search_fields = ("name", "external_id", "office_address", "group__name")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("group",)
    list_select_related = ("group",)
    actions = ("merge_into_one_group",)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_vacancy_count=Count("vacancies", filter=Q(vacancies__is_active=True)))
        )

    @admin.display(description="Active vacancies", ordering="_vacancy_count")
    def vacancy_count(self, obj):
        return getattr(obj, "_vacancy_count", 0)

    @admin.action(description="Объединить: перенести в группу первой выбранной компании")
    def merge_into_one_group(self, request, queryset):
        """Manual merge (option C): reassign all selected companies to the group
        of the first selected company that already has one."""
        target_group_id = (
            queryset.exclude(group__isnull=True).values_list("group_id", flat=True).first()
        )
        if target_group_id is None:
            self.message_user(
                request, "Ни у одной выбранной компании нет группы.", level=messages.WARNING
            )
            return
        updated = queryset.exclude(group_id=target_group_id).update(group_id=target_group_id)
        self.message_user(request, f"Перенесено компаний в одну группу: {updated}.")


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source",
        "company_name",
        "location",
        "city",
        "salary_min",
        "salary_max",
        "salary_currency",
        "is_active",
        "welcome_ukraine",
        "last_seen_at",
    )
    list_filter = ("source", "is_active", "welcome_ukraine", "salary_currency")
    search_fields = ("title", "company_name", "external_id", "location")
    raw_id_fields = ("company", "city")
    list_select_related = ("company", "city")
    readonly_fields = ("id", "first_seen_at", "last_seen_at")
    inlines = (VacancyTranslationInline, VacancyChangeInline)


@admin.register(VacancyTranslation)
class VacancyTranslationAdmin(admin.ModelAdmin):
    list_display = ("vacancy", "language", "translator", "translated_at")
    list_filter = ("language", "translator")
    search_fields = ("title_translated", "description_translated")
    raw_id_fields = ("vacancy",)
    readonly_fields = ("translated_at",)


@admin.register(TelegramSubscription)
class TelegramSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("telegram_user_id", "username", "query", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("telegram_user_id", "username", "query")
    readonly_fields = ("created_at",)


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = (
        "telegram_user_id",
        "username",
        "first_name",
        "last_name",
        "language_code",
        "last_seen_at",
    )
    search_fields = ("telegram_user_id", "username", "first_name", "last_name")
    list_filter = ("is_bot", "is_premium", "language_code")
    readonly_fields = ("created_at", "last_seen_at")


@admin.register(ScrapeRun)
class ScrapeRunAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "source",
        "started_at",
        "finished_at",
        "status",
        "vacancies_found",
        "new_count",
        "changed_count",
        "deactivated_count",
    )
    list_filter = ("source", "status")
    ordering = ("-started_at",)


@admin.register(VacancyChange)
class VacancyChangeAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("vacancy", "changed_at", "field_name", "old_value", "new_value")
    list_filter = ("field_name",)
    search_fields = ("vacancy__title", "old_value", "new_value")
    ordering = ("-changed_at",)


@admin.register(TranslationCache)
class TranslationCacheAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("language", "text_hash", "translator", "created_at")
    list_filter = ("language", "translator")
    search_fields = ("source_text", "translated_text", "text_hash")
    ordering = ("-created_at",)


@admin.register(TelegramSubscriptionDelivery)
class TelegramSubscriptionDeliveryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("subscription", "vacancy", "sent_at")
    search_fields = ("subscription__telegram_user_id", "vacancy__title")
    ordering = ("-sent_at",)


class ScheduleForm(forms.ModelForm):
    class Meta:
        model = Schedule
        fields = ("cron", "enabled")

    def clean_cron(self) -> str:
        expr = (self.cleaned_data.get("cron") or "").strip()
        parts = expr.split()
        if len(parts) != 5:
            raise forms.ValidationError(
                "Cron должен содержать ровно 5 полей: минута час день месяц день_недели."
            )
        if not all(_CRON_FIELD.fullmatch(p) for p in parts):
            raise forms.ValidationError(
                "Недопустимые символы. Разрешены цифры и символы * / , - "
                "(и имена дней/месяцев, напр. mon-fri)."
            )
        return expr


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    form = ScheduleForm
    list_display = ("job_id", "name", "cron", "enabled", "run_now_requested_at", "updated_at")
    list_display_links = ("job_id",)
    list_editable = ("cron", "enabled")
    ordering = ("job_id",)
    readonly_fields = ("job_id", "name", "run_now_requested_at", "created_at", "updated_at")
    actions = ("run_now",)

    # Rows are owned by the scheduler's JOB_REGISTRY — seeded on startup. Adding
    # or deleting rows by hand would desync from the code, so both are disabled.
    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    @admin.action(description="▶ Запустить сейчас")
    def run_now(self, request, queryset) -> None:
        updated = queryset.update(run_now_requested_at=timezone.now())
        self.message_user(
            request,
            f"Запрошен внеплановый запуск задач: {updated}. "
            "Scheduler подхватит в течение минуты.",
            messages.SUCCESS,
        )
