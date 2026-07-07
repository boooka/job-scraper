"""Abstract base scraper using Playwright."""
from __future__ import annotations

import abc
import asyncio
import html
import json
import traceback
from collections.abc import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from tenacity import retry, stop_after_attempt, wait_fixed

from src.config import settings
from src.logger import get_logger
from src.models.schemas import VacancyData

log = get_logger(__name__)


class BaseScraper(abc.ABC):
    """
    Abstract Playwright-based scraper.

    Subclasses implement `scrape_all` which yields VacancyData items.
    The base class handles browser lifecycle, retries, logging and
    optional detail-page HTML fetching.

    Detail page fetching
    --------------------
    Set ``fetch_detail_html = True`` in a subclass to automatically open
    each vacancy URL and store the raw HTML in ``VacancyData.page_html``.
    A dedicated second page is reused for all detail requests so list
    pagination is never interrupted.
    """

    source: str  # must be set in subclass

    # ── Detail-page options (override in subclass) ─────────────────────
    fetch_detail_html: bool = False
    detail_fetch_delay_ms: int = 800  # polite delay between detail requests

    def __init__(self) -> None:
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._detail_page: Page | None = None

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
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        # Pre-open a dedicated page for detail requests
        if self.fetch_detail_html:
            self._detail_page = await self._context.new_page()
            self._detail_page.set_default_timeout(settings.scraper_timeout_ms)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._detail_page:
            await self._detail_page.close()
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
        """Scrape all vacancies (with optional detail HTML) from the source."""
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
        """Yield VacancyData items from all list pages."""
        ...

    # ------------------------------------------------------------------
    # Detail-page HTML fetching
    # ------------------------------------------------------------------

    async def get_detail_html(self, url: str) -> str | None:
        """
        Navigate to a vacancy detail page and return its full HTML.

        Uses the shared ``_detail_page`` so the list page is untouched.
        Returns None on any error (timeout, 403, etc.) without raising.
        """
        if not self._detail_page:
            log.warning("detail_page.not_initialised", url=url)
            return None

        try:
            if self.detail_fetch_delay_ms:
                await asyncio.sleep(self.detail_fetch_delay_ms / 1000)

            await self._detail_page.goto(url, wait_until="domcontentloaded")
            page_html = await self._detail_page.content()
            log.debug("detail_page.fetched", url=url, size=len(page_html))
            return page_html

        except Exception as exc:
            log.warning("detail_page.error", url=url, error=str(exc))
            return None

    async def enrich_with_detail(self, vacancy: VacancyData) -> VacancyData:
        """
        Fetch detail page HTML and attach it to the vacancy.

        Example usage in subclass::

            async def scrape_all(self):
                async for item in self._scrape_list():
                    item = await self.enrich_with_detail(item)
                    yield item
        """
        page_html = await self.get_detail_html(vacancy.url)
        if page_html:
            vacancy = vacancy.model_copy(update={"page_html": page_html})
        return vacancy

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_salary(raw: str | None) -> tuple[int | None, int | None, str | None, str | None]:
        """
        Parse a raw salary string.

        Returns (salary_min, salary_max, currency, salary_period).
        """
        salary_min = None
        salary_max = None
        currency = None
        salary_period = None

        if not raw:
            return salary_min, salary_max, currency, salary_period

        import re

        raw = raw.replace("\xa0", "").replace(" ", "")

        if "€" in raw or "EUR" in raw:
            currency = "EUR"
        elif "$" in raw:
            currency = "USD"

        nums = re.findall(r"\d+", raw)
        if not nums:
            return salary_min, salary_max, currency, salary_period

        values = [int(n) for n in nums if int(n) > 0]
        low = raw.lower()
        if len(values) == 1:
            # "До / Iki / up to N" is an upper bound; otherwise treat as lower.
            if any(w in low for w in ("до", "iki", "upto")):
                salary_max = values[0]
            else:
                salary_min = values[0]
        if len(values) >= 2:
            salary_min, salary_max = min(values[:2]), max(values[:2])

        # Period detection across LT ("/val") and RU ("/час") interfaces.
        # Default to month; only flip to hour on an explicit hourly marker.
        hour_markers = ("/val", "val.", "/h", "/hr", "hour", "час", "/ч")
        if any(marker in low for marker in hour_markers):
            salary_period = "hour"
        else:
            salary_period = "month"

        return salary_min, salary_max, currency, salary_period

    @staticmethod
    def decode_content(content: str, to_json: bool = False) -> str | dict:
        """Decode HTML entities and optionally parse as JSON."""
        encoded = "utf-8"
        content = html.unescape(content)
        content.replace("\\/", "/").encode(encoded).decode(encoded)
        if to_json:
            return json.loads(content)
        return content