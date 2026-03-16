"""Scraper for en.cvbankas.lt."""
from __future__ import annotations

import re
from typing import AsyncGenerator

from playwright.async_api import Page

from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)

BASE_URL = "https://ru.cvbankas.lt/"


class CVBankasScraper(BaseScraper):
    """Scraper for ru.cvbankas.lt (Russian interface)."""

    source = "cvbankas"
    is_active = True

    async def scrape_all(self) -> AsyncGenerator[VacancyData, None]:
        page = await self.new_page()
        try:
            page_num = 1
            while True:
                url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
                log.info("cvbankas.fetch_page", page=page_num, url=url)

                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_selector("div#js_id_id_job_ad_list article.list_article", timeout=15_000)

                # items = await page.query_selector_all("ul#job_ad_list li.list_article")
                items = await page.query_selector_all("div#js_id_id_job_ad_list article.list_article")
                if not items:
                    log.info("cvbankas.no_more_pages", page=page_num)
                    break

                for item in items:
                    try:
                        vacancy = await self._parse_list_item(item)
                        if vacancy:
                            yield vacancy
                    except Exception as exc:
                        log.warning("cvbankas.parse_error", error=str(exc))

                # Check for next page
                next_btn = await page.query_selector("a.prev_next")
                if not next_btn:
                    log.info("cvbankas.parser", error=f"Next button for page {page_num} is inactive")
                    break
                page_num += 1
        except BaseException as exc:
            log.error("cvbankas.scrape_all_error", error=str(exc))
            raise exc
        finally:
            await page.close()

    async def _parse_list_item(self, item: object) -> VacancyData | None:
        from playwright.async_api import ElementHandle

        assert isinstance(item, ElementHandle)

        # External ID from data attribute or link
        link_el = await item.query_selector("a.list_a")
        if not link_el:
            return None

        href = await link_el.get_attribute("href") or ""
        external_id_match = re.search(r"/(\d+)/?$", href)
        if not external_id_match:
            external_id_match = re.search(r"id=(\w+)", href)
        external_id = external_id_match.group(1) if external_id_match else href

        title_el = await item.query_selector("h3.list_h3")
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        company_el = await item.query_selector("span.heading_secondary")
        company = (await company_el.inner_text()).strip() if company_el else None

        location_el = await item.query_selector("span.list_city")
        location = (await location_el.inner_text()).strip() if location_el else None

        salary_el = await item.query_selector("span.salary_amount")
        raw_salary = (await salary_el.inner_text()).strip() if salary_el else None
        salary_min, salary_max, currency, salary_period = self.parse_salary(raw_salary)

        url = href if href.startswith("http") else f"https://en.cvbankas.lt{href}"

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
            extra={"raw_salary": raw_salary},
        )
