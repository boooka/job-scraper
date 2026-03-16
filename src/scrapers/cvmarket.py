"""Scraper for cvmarket.lt."""
from __future__ import annotations

from itertools import count
import re
import traceback
from typing import AsyncGenerator

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

                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_selector(
                    "//section[@data-component='jobs_list']",
                    timeout=15_000,
                )

                items = await page.query_selector_all(
                    "article[data-component='jobad']"
                )
                if not items:
                    log.info("cvmarket.no_more_items", start=start)
                    break

                for item in items:
                    vacancy = await self._parse_item(item)
                    if vacancy:
                        yield vacancy
                

                if len(items) < PAGE_SIZE:
                    log.info("cvmarket.less_items_on_page", start=start, count=len(items))
                    break
                start += PAGE_SIZE
        except BaseException as exc:
            log.error("cvmarket.scrape_all_error", error=str(exc))
            raise exc
        finally:
            await page.close()

    async def _parse_item(self, item: ElementHandle) -> VacancyData | None:
        # Link & ID
        link_el = await item.query_selector("a[href]")
        if not link_el:
            return None

        href = await link_el.get_attribute("href") or ""
        raw_data = await item.query_selector("//article[@data-event]")
        if not raw_data:
            external_id_match = re.search(r"/(\d+)/?$", href)
            external_id = external_id_match.group(1) if external_id_match else href
            data = {}
        else:
            data_event = await item.get_attribute("data-event") or ""
            data = self.decode_content(data_event, to_json=True)
            external_id = data.get("job_id")
            

        id_match = re.search(r"/(\d+)/?", href) or re.search(r"id=(\w+)", href)
        external_id = id_match.group(1) if id_match else href

        # Title — multiple possible selectors
        title_el = await item.query_selector("div.main-info h2") 
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        # Company
        company_el = await item.query_selector("span.job-company")
        company = (await company_el.inner_text()).strip() if company_el else None

        # Location
        # location_el = await item.query_selector(".location, .city, .place")
        # location = (await location_el.inner_text()).strip() if location_el else None
        location = None

        # Salary
        salary_el = await item.query_selector("div.salary-block")
        raw_salary = (await salary_el.inner_text()).strip() if salary_el else None
        salary_min, salary_max, currency, salary_period = self.parse_salary(raw_salary)

        url = href if href.startswith("http") else f"https://www.cvmarket.lt{href}"

        extra = {
            "raw_salary": raw_salary,
        }
        extra.update(data)

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
