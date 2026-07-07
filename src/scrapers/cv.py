"""Scraper for cv.lt (Lithuanian interface)."""
from __future__ import annotations

import re
from typing import AsyncGenerator

from playwright.async_api import ElementHandle

from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)

BASE_URL = "https://www.cv.lt/jobs"
PAGE_SIZE = 25


class CVScraper(BaseScraper):
    """Scraper for cv.lt (English/Lithuanian interface)."""

    source = "cv"
    is_active = True

    async def scrape_all(self) -> AsyncGenerator[VacancyData, None]:
        page = await self.new_page()
        try:
            offset = 0
            page_num = 1
            while True:
                url = f"{BASE_URL}?start={offset}"
                log.info("cv.fetch_page", page=page_num, url=url)

                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_selector("//article", timeout=15_000)

                items = await page.query_selector_all("//article[@data-component='jobad']")
                if not items:
                    log.info("cv.no_more_pages", page=page_num)
                    break

                for item in items:
                    vacancy = await self._parse_list_item(item)
                    if vacancy:
                        yield vacancy

                # Stop when the "next page" control is absent (last page reached).
                next_btn = await page.query_selector("a:has(svg.keyboard-double-arrow-right)")
                if not next_btn:
                    log.info("cv.last_page", page=page_num)
                    break

                page_num += 1
                offset += PAGE_SIZE
        except BaseException as exc:
            log.error("cv.scrape_all_error", error=str(exc))
            raise exc
        finally:
            await page.close()

    async def _parse_list_item(self, item: object) -> VacancyData | None:
        assert isinstance(item, ElementHandle)

        # External ID from the article's data-props JSON (fallback: href)
        data_props = await item.get_attribute("data-props") or ""
        raw_job_id = self.decode_content(data_props, to_json=True) if data_props else {}
        if not isinstance(raw_job_id, dict):
            raw_job_id = {}

        # Title
        title_el = await item.query_selector("h2.job-title")
        if not title_el:
            return None
        title = (await title_el.inner_text()).strip()

        # Vacancy detail URL — the title is wrapped in <a class="block ...">, the
        # h2 itself has no href, so read it from the anchor.
        link_el = await item.query_selector("a.block[href]")
        href = await link_el.get_attribute("href") if link_el else ""
        href = href or ""

        external_id = str(raw_job_id.get("job_id") or "")
        if not external_id:
            id_match = re.search(r"-(\d+)(?:\?|$)", href) or re.search(r"/(\d+)/?$", href)
            external_id = id_match.group(1) if id_match else href
        if not external_id:
            log.warning("cv.parser", error=f"External ID for {href} is not found")
            return None

        # Company & location
        company_el = await item.query_selector("div.job-company")
        company = (await company_el.inner_text()).strip() if company_el else None

        location_el = await item.query_selector("span.job-location")
        location = (await location_el.inner_text()).strip() if location_el else None

        # Salary — prefer the structured data-salary-* attributes on .main-info,
        # falling back to the visible badge text.
        salary_min = salary_max = None
        salary_currency = salary_period = salary_type = None

        main = await item.query_selector("div.main-info")
        salary_from = await main.get_attribute("data-salary-from") if main else None
        salary_to = await main.get_attribute("data-salary-to") if main else None
        salary_type = await main.get_attribute("data-salary-type") if main else None

        if salary_from and salary_from.isdigit():
            salary_min = int(salary_from)
        if salary_to and salary_to.isdigit():
            salary_max = int(salary_to)

        salary_el = await item.query_selector("span[data-component='badge']")
        raw_salary = (await salary_el.inner_text()).strip() if salary_el else None
        parsed_min, parsed_max, parsed_cur, parsed_period = self.parse_salary(raw_salary)
        # Structured attrs win; use badge parse to fill currency/period and gaps.
        salary_min = salary_min if salary_min is not None else parsed_min
        salary_max = salary_max if salary_max is not None else parsed_max
        salary_currency = parsed_cur or ("EUR" if (salary_min or salary_max) else None)
        # cv.lt salary attributes are monthly; the badge rarely carries a period.
        salary_period = parsed_period or ("month" if (salary_min or salary_max) else None)

        url = href if href.startswith("http") else f"https://www.cv.lt{href}"

        extra = {
            "raw_salary": raw_salary,
            "salary_from": salary_from,
            "salary_to": salary_to,
        }
        extra.update(raw_job_id)

        return VacancyData(
            source=self.source,
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            url=url,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            salary_period=salary_period,
            salary_type=salary_type,
            extra=extra,
        )
