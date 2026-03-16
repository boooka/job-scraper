"""
Универсальный диагностический скрипт для отладки селекторов.

Использование:
    # Интерактивный режим (Playwright Inspector)
    PWDEBUG=1 poetry run python scripts/debug_selectors.py --source cvbankas --interactive

    # Дамп HTML + скриншот + проверка списка селекторов
    poetry run python scripts/debug_selectors.py --source cvbankas
    poetry run python scripts/debug_selectors.py --source newsite

    # Проверить конкретный селектор
    poetry run python scripts/debug_selectors.py --source cvbankas --selector "h3.list_h3"
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import ElementHandle, Page, async_playwright

# ── Конфигурация сайтов ────────────────────────────────────────────────────────
SITES: dict[str, dict] = {
    "cv": {
        "url": "https://cv.lt/jobs",
        "wait_until": "domcontentloaded",
        "selectors": {
            "items":    "//article[@data-component='jobad']",
            "title":    "h2.job-title",
            "company":  "div.job-company",
            "location": "span.job-location",
            "salary":   "span[data-component='badge']",
            "tax":     ".main-info",
            "link":  "a.block h2.job-title",
            "href":  "a.block h2.job-title",
            
            
        },
    },
    "cvbankas": {
        "url": "https://ru.cvbankas.lt/",
        "wait_until": "domcontentloaded",
        # Селекторы которые должны работать — проверяем все
        "selectors": {
            "items":    "div#js_id_id_job_ad_list article.list_article",
            "title":    "h3.list_h3",
            "company":  "span.heading_secondary",
            "location": "span.list_city",
            "salary":   "span.salary_amount",
            "link":     "a.list_a",
        },
    },
    "cvonline": {
        "url": "https://cvonline.lt/ru/search?limit=20&offset=0",
        "wait_until": "domcontentloaded",
        "selectors": {
            "items":    "ul.vacancies-list, .vacancy-item",
            "company":  "div.vacancy-item__column",
            "title":  "h2.vacancy-item__title",
            "location": "div.vacancy-item__locations",
            "salary":   "span.salary-label",
            "link":     "h2.vacancy-item__title",
        },
    },
    "cvmarket": {
        "url": "https://www.cvmarket.lt/darbo-skelbimai?start=50",
        "wait_until": "domcontentloaded",
        "selectors": {
            "items":    "article[data-component='jobad']",
            "title":    "div.main-info h2",
            "company":  "span.job-company",
            # "location": ".location, .city, .place",
            "salary":   "div.salary-block",
            "link":     "a[href]",
        },
    },
}

OUTPUT_DIR = Path("debug_output")


async def dump_page(page: Page, source: str) -> Path:
    """Сохранить HTML и скриншот страницы."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    html_path = OUTPUT_DIR / f"{source}.html"
    png_path = OUTPUT_DIR / f"{source}.png"

    html_path.write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(png_path), full_page=True)

    print(f"  💾 HTML  → {html_path}")
    print(f"  📸 PNG   → {png_path}")
    return html_path


async def check_selector(page: Page, name: str, selector: str, sample: int = 3) -> dict:
    """Проверить один селектор, вернуть результат."""
    elements = await page.query_selector_all(selector)
    count = len(elements)

    samples = []
    for el in elements[:sample]:
        try:
            text = (await el.inner_text()).strip().replace("\n", " ")[:100]
            samples.append(text)
        except Exception:
            samples.append("<ошибка чтения>")

    status = "✅" if count > 0 else "❌"
    print(f"  {status} [{name:10s}]  найдено={count:4d}  selector={selector!r}")
    for i, s in enumerate(samples):
        print(f"             пример[{i}]: {s!r}")

    return {"name": name, "selector": selector, "count": count, "samples": samples}


async def check_fields_inside_item(item: ElementHandle, selectors: dict[str, str]) -> None:
    """Проверить, какие поля находятся внутри первого item-элемента."""
    print("\n  🔬 Поиск полей внутри первого item:")
    for name, selector in selectors.items():
        if name == "items":
            continue
        el = await item.query_selector(selector)
        if el:
            text = (await el.inner_text()).strip().replace("\n", " ")[:80]
            print(f"     ✅ {name:10s} → {text!r}")
        else:
            print(f"     ❌ {name:10s} — не найден  (selector={selector!r})")


async def suggest_alternatives(page: Page) -> None:
    """Эвристика: найти теги которые похожи на карточки вакансий."""
    print("\n  💡 Поиск альтернативных контейнеров:")
    candidates = [
        "article", "li[class]", "div[class*='job']", "div[class*='vacancy']",
        "div[class*='advert']", "div[class*='card']", "div[class*='item']",
        "[data-id]", "[data-vacancy]", "[data-job]",
    ]
    for sel in candidates:
        els = await page.query_selector_all(sel)
        if els:
            first_class = await els[0].get_attribute("class") or ""
            print(f"     {sel:35s}  count={len(els):4d}  class={first_class[:60]!r}")


async def run_debug(source: str, extra_selector: str | None, interactive: bool) -> None:
    if source not in SITES and not interactive:
        print(f"⚠️  Источник {source!r} не в конфиге SITES — будет только дамп HTML.")

    cfg = SITES.get(source, {"url": "", "wait_until": "domcontentloaded", "selectors": {}})

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not interactive,
            slow_mo=300 if interactive else 0,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        print(f"\n🌐 Загружаю: {cfg['url']}")
        await page.goto(cfg["url"], wait_until=cfg["wait_until"])
        print(f"   Заголовок: {await page.title()!r}")
        print(f"   URL после редиректов: {page.url!r}")

        if interactive:
            print("\n⏸  Открываю Playwright Inspector — используй 'Pick locator'")
            await page.pause()
            await browser.close()
            return

        # Дамп HTML + скриншот
        print("\n📁 Сохраняю дамп:")
        await dump_page(page, source)

        # Проверяем все селекторы из конфига
        if cfg["selectors"]:
            print("\n🔍 Проверка селекторов из конфига:")
            results = {}
            for name, selector in cfg["selectors"].items():
                results[name] = await check_selector(page, name, selector)

            # Если items нашлись — проверяем поля внутри первого
            items_result = results.get("items")
            if items_result and items_result["count"] > 0:
                items = await page.query_selector_all(cfg["selectors"]["items"])
                await check_fields_inside_item(items[0], cfg["selectors"])
            else:
                await suggest_alternatives(page)

        # Проверить произвольный селектор
        if extra_selector:
            print(f"\n🎯 Проверка указанного селектора: {extra_selector!r}")
            await check_selector(page, "custom", extra_selector, sample=5)

        # Итог — сохранить JSON
        OUTPUT_DIR.mkdir(exist_ok=True)
        report = {
            "source": source,
            "url": page.url,
            "title": await page.title(),
            "selectors": cfg.get("selectors", {}),
        }
        report_path = OUTPUT_DIR / f"{source}_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n📊 Отчёт → {report_path}")

        await browser.close()


# scripts/debug_selectors.py — начало main()
def main() -> None:
    import os
    import sys
    
    parser = argparse.ArgumentParser(description="Отладка селекторов Playwright")
    parser.add_argument("--source", default=os.getenv("SOURCE"), help="Имя источника: cvbankas, cvonline, cvmarket, newsite...")
    parser.add_argument("--selector", default=os.getenv("SELECTOR"), help="Проверить конкретный CSS-селектор")
    parser.add_argument("--interactive", action="store_true", help="Открыть Playwright Inspector")
    args = parser.parse_args()
    if not args.source:
        print("❌ Укажи SOURCE= в env или --source в аргументах")
        sys.exit(1)
    
    asyncio.run(run_debug(args.source, args.selector, args.interactive))


if __name__ == "__main__":
    main()
