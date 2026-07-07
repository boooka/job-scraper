"""Scraper for cvonline.lt (Russian interface)."""
from __future__ import annotations

import re
from typing import AsyncGenerator

from playwright.async_api import ElementHandle
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)

BASE_URL = "https://www.cvonline.lt/ru/search"
PAGE_SIZE = 100


class CVOnlineScraper(BaseScraper):
    """Scraper for cvonline.lt with offset-based pagination."""

    source = "cvonline"
    is_active = True

    async def scrape_all(self) -> AsyncGenerator[VacancyData, None]:
        page = await self.new_page()
        try:
            offset = 0
            while True:
                url = f"{BASE_URL}?limit={PAGE_SIZE}&offset={offset}"
                log.info("cvonline.fetch_page", offset=offset, url=url)

                await page.goto(url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector("ul.vacancies-list", timeout=15_000)
                except PlaywrightTimeoutError:
                    # No results container → we've paged past the last vacancy.
                    log.info("cvonline.no_more_pages", offset=offset)
                    break

                # Each vacancy card is a `.vacancy-item` div. Do NOT include the
                # `ul.vacancies-list` container itself in the selector.
                items = await page.query_selector_all("li.vacancies-list__item div.vacancy-item")
                if not items:
                    log.info("cvonline.no_more_items", offset=offset)
                    break

                for item in items:
                    try:
                        vacancy = await self._parse_item(item)
                        if vacancy:
                            yield vacancy
                    except Exception as exc:
                        log.warning("cvonline.parse_error", error=str(exc))

                # The site may cap page size below `limit`; advance by the actual
                # number of cards returned so no vacancies are skipped.
                offset += len(items)
        except BaseException as exc:
            log.error("cvonline.scrape_all_error", error=str(exc))
            raise exc
        finally:
            await page.close()

    async def _parse_item(self, item: ElementHandle) -> VacancyData | None:
        # Title link — the <a> lives *inside* h2.vacancy-item__title
        link_el = await item.query_selector("h2.vacancy-item__title a")
        if not link_el:
            return None

        href = await link_el.get_attribute("href") or ""
        id_match = re.search(r"/vacancy/(\d+)", href)
        if not id_match:
            # Fallback: id is embedded in the card's data-testid ("vacancy-item-123")
            testid = await item.get_attribute("data-testid") or ""
            id_match = re.search(r"(\d+)", testid)
        external_id = id_match.group(1) if id_match else href
        if not external_id:
            log.warning("cvonline.parser", error=f"External ID for {href} is not found")
            return None

        # Title
        title = (await link_el.inner_text()).strip()

        # Company — the employer link (distinct from the locations column)
        company_el = await item.query_selector(
            "a[data-testid^='vacancy-item-link-employer']"
        )
        company = (await company_el.inner_text()).strip() if company_el else None

        # Location — "Литва / Удаленный" → location + kind
        location_el = await item.query_selector("div.vacancy-item__locations")
        location_raw = (await location_el.inner_text()).strip() if location_el else None
        location: str | None = None
        kind: str | None = None
        if location_raw:
            parts = [p.strip() for p in location_raw.split("/") if p.strip()]
            if len(parts) >= 2:
                location, kind = parts[0], parts[1]
            elif parts:
                location = parts[0]

        # Salary
        salary_el = await item.query_selector("span.salary-label")
        raw_salary = (await salary_el.inner_text()).strip() if salary_el else None
        salary_min, salary_max, currency, salary_period = self.parse_salary(raw_salary)

        url = href if href.startswith("http") else f"https://www.cvonline.lt{href}"

        extra = {
            "raw_salary": raw_salary,
            "kind": kind,
        }

        return VacancyData(
            source=self.source,
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            url=url,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            salary_period=salary_period,
            extra=extra,
        )
