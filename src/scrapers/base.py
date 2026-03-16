"""Abstract base scraper using Playwright."""
from __future__ import annotations

import abc
import html
import json
import traceback
from typing import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from tenacity import retry, stop_after_attempt, wait_fixed

from src.config import settings
from src.logger import get_logger
from src.models.schemas import VacancyData

log = get_logger(__name__)


class BaseScraper(abc.ABC):
    """
    Abstract Playwright-based scraper.

    Subclasses implement `scrape_page` which yields VacancyData items.
    The base class handles browser lifecycle, retries and logging.
    """

    source: str  # must be set in subclass

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> BaseScraper:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.scraper_headless,
            slow_mo=settings.scraper_slow_mo_ms,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Europe/Vilnius",
        )
        # Hide webdriver flag
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        await self._playwright.stop()

    async def new_page(self) -> Page:
        assert self._context, "Scraper not initialised — use as async context manager"
        page = await self._context.new_page()
        page.set_default_timeout(settings.scraper_timeout_ms)
        return page

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(10),
        reraise=True,
    )
    async def run(self) -> list[VacancyData]:
        """Scrape all vacancies from the source."""
        log.info("scraper.start", source=self.source)
        results: list[VacancyData] = []
        try:
            async for item in self.scrape_all():
                results.append(item)
        except BaseException as exc:
            log.error("scraper.error", source=self.source, error=str(traceback.format_exc()))
            raise exc
        log.info("scraper.done", source=self.source, count=len(results))
        return results

    # ------------------------------------------------------------------
    # Abstract methods
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def scrape_all(self) -> AsyncGenerator[VacancyData, None]:
        """Yield VacancyData items from all pages."""
        ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_salary(raw: str | None) -> tuple[int | None, int | None, str | None, str | None]:
        """
        Parse a raw salary string.

        Returns (salary_min, salary_max, currency).
        """
        salary_min = None
        salary_max = None
        currency = None
        salary_period = None
        if not raw:
            return salary_min, salary_max, currency, salary_period

        import re

        raw = raw.replace("\xa0", "").replace(" ", "")
        currency = None
        if "€" in raw or "EUR" in raw:
            currency = "EUR"
        elif "$" in raw:
            currency = "USD"

        nums = re.findall(r"\d+", raw)
        if not nums:
            return salary_min, salary_max, currency, salary_period

        values = [int(n) for n in nums if int(n) > 0]
        if len(values) == 1:
            salary_min=values[0]
        if len(values) >= 2:
            salary_min, salary_max = min(values[:2]), max(values[:2])
        if "ч" in raw.lower() or "h" in raw.lower():
            salary_period = "hour"
        else:
            salary_period = "month"
        return salary_min, salary_max, currency, salary_period

    @staticmethod
    def decode_content(content: str, to_json: bool = False) -> str | dict:
        encoded = 'utf-8'  # 'cp1252' get by Content-Type header
        content = html.unescape(content)  # decode HTML entities
        content.replace('\\/', '/').encode(encoded).decode(encoded)
        if to_json:
            content = json.loads(content)
        return content