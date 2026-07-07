"""Scraper for en.cvbankas.lt."""
from __future__ import annotations

import re
from collections.abc import AsyncGenerator

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
                next_el = await page.query_selector("ul.pages_ul a.prev_next:last-child")
                quote = await next_el.inner_text() if next_el else None
                # First page has no prev button
                if quote == "»" and page_num > 1:
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
        external_id_match = re.search(r"/([-\w\d]+/\d*-\d+)/?$", href)
        if not external_id_match:
            external_id_match = re.search(f"{BASE_URL}(.*)/?$", href)
        external_id = external_id_match.group(1) if external_id_match else href

        if not external_id:
            log.warning("cvbankas.parser", error=f"External ID for {href} is not found")
            return None

        title_el = await item.query_selector("h3.list_h3")
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        company_el = await item.query_selector("span.heading_secondary")
        company = (await company_el.inner_text()).strip() if company_el else None

        location_el = await item.query_selector("span.list_city")
        location = (await location_el.inner_text()).strip() if location_el else None

        # Salary: amount lives in .salary_amount, currency+period in .salary_period
        # (e.g. "€/час"). Read the whole .salary_text so parse_salary sees both.
        salary_text_el = await item.query_selector("span.salary_text")
        if salary_text_el is None:
            salary_text_el = await item.query_selector("span.salary_amount")
        raw_salary = (await salary_text_el.inner_text()).strip() if salary_text_el else None
        salary_min, salary_max, currency, salary_period = self.parse_salary(raw_salary)
        # cvbankas always pays in EUR; the € may sit in a not-yet-loaded node.
        if (salary_min or salary_max) and not currency:
            currency = "EUR"

        # Gross/net marker from the salary block class / calculation label
        salary_type: str | None = None
        if await item.query_selector("span.salary_bl_net"):
            salary_type = "net"
        elif await item.query_selector("span.salary_bl_gross"):
            salary_type = "gross"

        url = href if href.startswith("http") else f"https://ru.cvbankas.lt{href}"

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
            salary_type=salary_type,
            extra={"raw_salary": raw_salary},
        )
