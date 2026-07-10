"""Scraper for cvmarket.lt."""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import Any

from playwright.async_api import ElementHandle

from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)

BASE_URL = "https://www.cvmarket.lt/darbo-skelbimai"
PAGE_SIZE = 25


class CVMarketScraper(BaseScraper):
    """Scraper for cvmarket.lt with start-offset pagination."""

    source = "cvmarket"
    is_active = True

    async def scrape_all(self) -> AsyncGenerator[VacancyData, None]:
        page = await self.new_page()
        try:
            start = 0
            while True:
                url = f"{BASE_URL}?start={start}"
                log.info("cvmarket.fetch_page", start=start, url=url)

                await self.safe_goto(page, url)
                await page.wait_for_selector(
                    "//section[@data-component='jobs_list']",
                    timeout=15_000,
                )

                items = await page.query_selector_all("article[data-component='jobad']")
                if not items:
                    log.info("cvmarket.no_more_items", start=start)
                    break

                for item in items:
                    try:
                        vacancy = await self._parse_item(item)
                        if vacancy:
                            yield vacancy
                    except Exception as exc:
                        log.warning("cvmarket.parse_error", error=str(exc))

                if len(items) < PAGE_SIZE:
                    log.info("cvmarket.less_items_on_page", start=start, count=len(items))
                    break
                start += PAGE_SIZE
        except BaseException as exc:
            log.error("cvmarket.scrape_all_error", error=str(exc))
            raise exc
        finally:
            await page.close()

    def _extract_event_item(self, data_event: str) -> dict[str, Any]:
        """Parse the article's ``data-event`` JSON → the first ecommerce item."""
        if not data_event:
            return {}
        try:
            data = self.decode_content(data_event, to_json=True)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        try:
            items = data.get("ecommerce", {}).get("select_items", [])
            return items[0] if items else {}
        except (AttributeError, IndexError, TypeError):
            return {}

    async def _parse_item(self, item: ElementHandle) -> VacancyData | None:
        # Link
        link_el = await item.query_selector("a[href]")
        if not link_el:
            return None
        href = await link_el.get_attribute("href") or ""

        # Rich metadata from the article's data-event JSON. NOTE: cvmarket's
        # data-event is frequently shifted to a NEIGHBOURING card — its item_id
        # then belongs to another job — so it is trusted only when item_id
        # matches this card's id (event_ok below).
        data_event = await item.get_attribute("data-event") or ""
        event_item = self._extract_event_item(data_event)

        # External ID from the explicit jobid attribute, else parsed from href.
        external_id = await item.get_attribute("data-component-jobid") or ""
        if not external_id:
            id_match = re.search(r"-(\d+)(?:\?|$)", href) or re.search(r"/(\d+)/?$", href)
            external_id = id_match.group(1) if id_match else href
        if not external_id:
            log.warning("cvmarket.parser", error=f"External ID for {href} is not found")
            return None

        # data-event describes THIS card only when its item_id matches.
        event_ok = str(event_item.get("item_id") or "") == str(external_id)

        # Title — visible heading, fallback to event item name
        title_el = await item.query_selector("div.main-info h2")
        title = (await title_el.inner_text()).strip() if title_el else None
        if not title and event_ok:
            title = event_item.get("item_name")
        if not title:
            title = "Unknown"

        # Company — visible label, fallback to event "affiliation"
        company_el = await item.query_selector("span.job-company")
        company = (await company_el.inner_text()).strip() if company_el else None
        if not company and event_ok:
            company = event_item.get("affiliation")

        # Location — read from the card DOM. The data-event location_id often
        # belongs to a neighbouring job (see event_ok), which made the city
        # flip-flop on every run; the per-card DOM value is stable.
        location = None
        loc_el = await item.query_selector("div.text-sm.lg\\:hidden")
        if loc_el:
            location = (await loc_el.inner_text()).strip() or None
        if not location and event_ok:
            location = event_item.get("location_id")

        # Salary
        salary_el = await item.query_selector("div.salary-block")
        raw_salary = (await salary_el.inner_text()).strip() if salary_el else None
        salary_min, salary_max, currency, salary_period = self.parse_salary(raw_salary)

        url = href if href.startswith("http") else f"https://www.cvmarket.lt{href}"

        extra: dict[str, Any] = {"raw_salary": raw_salary}
        if event_ok:
            extra.update(
                {
                    "job_id": event_item.get("item_id"),
                    "category": event_item.get("item_category"),
                    "employment_type": event_item.get("item_category5"),
                    "seniority": event_item.get("item_category4"),
                    "salary_variant": event_item.get("item_variant"),
                }
            )

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
