"""
Translation worker.

Triggered automatically after each scrape batch via run_pending_translations().
Finds vacancies without a Russian translation and translates title + description
via DeepL, writing results to vacancy_translations.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.engine import get_session
from src.db.repository import TranslationRepository
from src.logger import get_logger
from src.models.orm import Vacancy, VacancyTranslation
from src.services.deepl_client import get_deepl_client
from src.services.metrics import metrics_registry

log = get_logger(__name__)

TRANSLATOR_NAME = "deepl"

# run_pending_translations вызывается из разных мест:
# - scheduler catch-up job (каждую минуту)
# - фоновые tasks после каждого flushed batch
# При перекрытии это может создавать много параллельных DeepL запросов
# и заметно "размазывать" запуск остальных scheduler-джобов.
_translation_lock = asyncio.Lock()


@dataclass
class _TranslationJob:
    vacancy_id: uuid.UUID
    title: str
    description: str | None


def _chunk_by_char_quota(texts: list[str], quota: int) -> list[list[str]]:
    """
    Split text list into chunks where total chars per chunk <= quota.
    """
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for text in texts:
        text_len = len(text)
        if text_len > quota:
            if current:
                chunks.append(current)
                current = []
                current_chars = 0
            chunks.append([text])
            continue

        if current and current_chars + text_len > quota:
            chunks.append(current)
            current = [text]
            current_chars = text_len
        else:
            current.append(text)
            current_chars += text_len

    if current:
        chunks.append(current)

    return chunks


async def _translate_with_cache(
    texts: list[str],
    *,
    language: str,
) -> dict[str, str]:
    """
    Translate unique non-empty texts with cache + quota-aware batching.
    Returns mapping: source_text -> translated_text.
    """
    unique_texts = [t for t in dict.fromkeys(texts) if t.strip()]
    if not unique_texts:
        return {}

    async with get_session() as session:
        repo = TranslationRepository(session)
        cached = await repo.get_cached_many(language, unique_texts)

    missing = [t for t in unique_texts if t not in cached]
    metrics_registry.incr("translation_cache_hits", len(unique_texts) - len(missing))
    metrics_registry.incr("translation_cache_misses", len(missing))
    if not missing:
        return cached

    client = get_deepl_client()
    quota = settings.deepl_batch_char_quota
    translated_new: dict[str, str] = {}

    for chunk in _chunk_by_char_quota(missing, quota):
        results = await client.translate_batch(chunk, target_lang=language)
        for src, dst in zip(chunk, results):
            translated_new[src] = dst

        if settings.deepl_delay_ms:
            await asyncio.sleep(settings.deepl_delay_ms / 1000)

    if translated_new:
        async with get_session() as session:
            repo = TranslationRepository(session)
            await repo.cache_many(
                language,
                translated_new.items(),
                translator=TRANSLATOR_NAME,
            )

    return {**cached, **translated_new}


async def _fetch_untranslated(
    session: AsyncSession,
    language: str,
    batch_size: int,
) -> list[_TranslationJob]:
    """
    Return vacancies that have no translation for `language` yet.
    Only active vacancies with at least a title are included.
    """
    # Subquery: vacancy_ids that already have this language
    existing = (
        select(VacancyTranslation.vacancy_id)
        .where(VacancyTranslation.language == language)
        .scalar_subquery()
    )

    stmt = (
        select(Vacancy.id, Vacancy.title, Vacancy.description)
        .where(
            Vacancy.is_active.is_(True),
            Vacancy.title.isnot(None),
            Vacancy.id.not_in(existing),
        )
        .order_by(Vacancy.first_seen_at.desc())
        .limit(batch_size)
    )

    result = await session.execute(stmt)
    return [
        _TranslationJob(vacancy_id=row.id, title=row.title, description=row.description)
        for row in result
    ]


async def _translate_jobs(
    jobs: list[_TranslationJob],
    language: str,
) -> list[tuple[_TranslationJob, str, str | None]]:
    """
    Call DeepL for all titles and descriptions in as few requests as possible.

    Returns list of (job, translated_title, translated_description).
    """
    titles = [j.title for j in jobs]
    descriptions = [j.description or "" for j in jobs]

    try:
        title_map = await _translate_with_cache(titles, language=language)
        translated_titles = [title_map[t] for t in titles]
    except Exception as exc:
        log.error("deepl.titles_failed", error=str(exc))
        raise

    # Only translate non-empty descriptions
    desc_indices = [i for i, d in enumerate(descriptions) if d.strip()]
    translated_descriptions: list[str | None] = [None] * len(jobs)

    if desc_indices:
        desc_texts = [descriptions[i] for i in desc_indices]
        try:
            desc_map = await _translate_with_cache(desc_texts, language=language)
            for idx in desc_indices:
                translated_descriptions[idx] = desc_map[descriptions[idx]]
        except Exception as exc:
            log.warning("deepl.descriptions_failed", error=str(exc))
            # Continue — titles are more important, descriptions are optional

    return [
        (job, translated_titles[i], translated_descriptions[i])
        for i, job in enumerate(jobs)
    ]


async def run_pending_translations(language: str | None = None) -> dict[str, int]:
    """
    Translate all untranslated active vacancies for the given language.

    Called automatically from the scheduler after each scrape run.
    Safe to call concurrently — DB unique constraint prevents duplicates.

    Returns summary: {"translated": N, "failed": N, "skipped": N}
    """
    if not settings.deepl_api_key:
        log.warning("translation.skipped", reason="DEEPL_API_KEY not set")
        return {"translated": 0, "failed": 0, "skipped": 0}

    lang = language or settings.deepl_target_lang
    batch_size = settings.deepl_batch_size
    summary = {"translated": 0, "failed": 0, "skipped": 0}

    log.info("translation.start", language=lang, batch_size=batch_size)

    # Loop until no untranslated vacancies remain in this run
    if _translation_lock.locked():
        log.info("translation.skip_already_running", language=lang)
        return summary

    async with _translation_lock:
        while True:
            async with get_session() as session:
                jobs = await _fetch_untranslated(session, lang, batch_size)

            if not jobs:
                log.info("translation.nothing_pending", language=lang)
                break

            log.info("translation.batch", language=lang, count=len(jobs))

            try:
                results = await _translate_jobs(jobs, lang)
            except Exception as exc:
                log.error("translation.batch_failed", language=lang, error=str(exc))
                summary["failed"] += len(jobs)
                break  # Stop on DeepL error to avoid quota waste

            # Persist translations
            async with get_session() as session:
                repo = TranslationRepository(session)
                for job, title_ru, desc_ru in results:
                    try:
                        await repo.upsert(
                            vacancy_id=job.vacancy_id,
                            language=lang,
                            title_translated=title_ru,
                            description_translated=desc_ru,
                            translator=TRANSLATOR_NAME,
                        )
                        summary["translated"] += 1
                    except Exception as exc:
                        log.warning(
                            "translation.save_failed",
                            vacancy_id=str(job.vacancy_id),
                            error=str(exc),
                        )
                        summary["failed"] += 1

            log.info("translation.batch_done", **summary)

            # If we got fewer than batch_size — no more to process
            if len(jobs) < batch_size:
                break

            if settings.deepl_delay_ms:
                await asyncio.sleep(settings.deepl_delay_ms / 1000)

    log.info("translation.complete", language=lang, **summary)
    return summary
