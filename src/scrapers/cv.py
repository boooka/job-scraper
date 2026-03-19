"""Scraper for en.cvbankas.lt."""
from __future__ import annotations

import re
import traceback
from typing import AsyncGenerator

from playwright.async_api import Page

from src.logger import get_logger
from src.models.schemas import VacancyData
from src.scrapers.base import BaseScraper

log = get_logger(__name__)

BASE_URL = "https://www.cv.lt/jobs"


class CVScraper(BaseScraper):
    """Scraper for cv.lt (English interface)."""

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

                # items = await page.query_selector_all("ul#job_ad_list li.list_article")
                items = await page.query_selector_all("//article[@data-component='jobad']")
                if not items:
                    log.info("cv.no_more_pages", page=page_num)
                    break

                for item in items:
                        vacancy = await self._parse_list_item(item)
                        if vacancy:
                            yield vacancy
        
                # Check for next page
                next_btn = await page.query_selector("a:has(svg.keyboard-double-arrow-right)")
                if not next_btn:
                    log.warning("cv.parser", error=f"Next button for page {page_num} is inactive")
                    break
                
                page_num += 1
                offset = page_num * 25
        except BaseException as exc: 
            log.error("cv.scrape_all_error", error=str(exc))
            raise exc   
        finally:
            await page.close()

    async def _parse_list_item(self, item: object) -> VacancyData | None:
        from playwright.async_api import ElementHandle

        assert isinstance(item, ElementHandle)

        # External ID from data attribute or link
        # link_el = await item.query_selector("article[@data-props]")
        link_el = await item.query_selector("a.block h2.job-title")
        if not link_el:
            return None
    
        href = await link_el.get_attribute("href") or ""
        data_props = await item.get_attribute("data-props") or ""
        raw_job_id = self.decode_content(data_props, to_json=True)

        if not raw_job_id:
            external_id_match = re.search(r"/(\d+)/?$", href)
            external_id = external_id_match.group(1) if external_id_match else href
            raw_job_id = {}            
        else:
            external_id = raw_job_id.get("job_id")
            

        title_el = await item.query_selector("h2.job-title")
        title = (await title_el.inner_text()).strip() if title_el else "Unknown"

        company_el = await item.query_selector("div.job-company")
        company = (await company_el.inner_text()).strip() if company_el else None

        location_el = await item.query_selector("span.job-location")
        location = (await location_el.inner_text()).strip() if location_el else None

        salary_el = await item.query_selector("span[data-component='badge']")
        raw_salary = (await salary_el.inner_text()).strip() if salary_el else None
        salary_min, salary_max, currency, salary_period = self.parse_salary(raw_salary)

        main = await item.query_selector("div.main-info")
        salary_from    = await main.get_attribute("data-salary-from")   # "2200"
        salary_to      = await main.get_attribute("data-salary-to")     # "3000"
        salary_type    = await main.get_attribute("data-salary-type")   # "gross"
        
        
        url = href if href.startswith("http") else f"https://www.cv.lt/{href}"
        extra = {
            "raw_salary": raw_salary,
            "salary_from": salary_from,
            "salary_to": salary_to,
            "salary_type": salary_type,
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
            salary_currency=currency,
            salary_period=salary_period,
            extra=extra,
        )
