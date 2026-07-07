"""Normalise raw location strings to a canonical (English) city name + RU translation.

Job boards spell the same place differently — cvbankas/cvonline use Russian
("Вильнюс"), cv.lt/cvmarket use Lithuanian ("Vilnius"). This module maps every
known spelling to one canonical ``name_en`` (always returned) and, when known,
a Russian ``name_translated``.

Matching is diacritic- and case-insensitive, so "Klaipėda" / "Klaipeda" /
"KLAIPEDA" all resolve to the same entry.
"""

from __future__ import annotations

import unicodedata

# Canonical entries: (name_en, name_translated_ru, extra_aliases)
# name_en and name_translated are added as aliases automatically; `extra_aliases`
# covers spellings that differ from both (rare — most variance is diacritics,
# handled by normalisation).
_CITY_DATA: list[tuple[str, str | None, tuple[str, ...]]] = [
    # ── Lithuanian cities / towns ─────────────────────────────────────────
    ("Vilnius", "Вильнюс", ()),
    ("Kaunas", "Каунас", ()),
    ("Klaipėda", "Клайпеда", ()),
    ("Šiauliai", "Шяуляй", ()),
    ("Panevėžys", "Паневежис", ()),
    ("Alytus", "Алитус", ()),
    ("Marijampolė", "Мариямполе", ()),
    ("Palanga", "Паланга", ()),
    ("Jonava", "Йонава", ()),
    ("Kėdainiai", "Кедайняй", ()),
    ("Mažeikiai", "Мажейкяй", ()),
    ("Utena", "Утена", ()),
    ("Plungė", "Плунге", ()),
    ("Kaišiadorys", "Кайшиадорис", ()),
    ("Telšiai", "Тельшяй", ()),
    ("Gargždai", "Гаргждай", ()),
    ("Elektrėnai", "Электренай", ()),
    ("Ukmergė", "Укмерге", ()),
    ("Tauragė", "Таураге", ()),
    ("Šilutė", "Шилуте", ()),
    ("Radviliškis", "Радвилишкис", ()),
    ("Kretinga", "Кретинга", ()),
    ("Druskininkai", "Друскининкай", ()),
    ("Lentvaris", "Лентварис", ()),
    ("Prienai", "Приенай", ()),
    ("Trakai", "Тракай", ()),
    ("Rokiškis", "Рокишкис", ()),
    ("Visaginas", "Висагинас", ()),
    ("Molėtai", "Молетай", ()),
    ("Birštonas", "Бирштонас", ()),
    ("Vievis", "Виевис", ()),
    ("Raseiniai", "Расейняй", ()),
    ("Širvintos", "Ширвинтос", ()),
    ("Anykščiai", "Аникшчяй", ()),
    ("Jurbarkas", "Юрбаркас", ()),
    ("Pasvalys", "Пасвалис", ()),
    ("Šakiai", "Шакяй", ()),
    ("Kelmė", "Кельме", ()),
    ("Varėna", "Варена", ()),
    ("Joniškis", "Йонишкис", ()),
    ("Vilkaviškis", "Вилкавишкис", ()),
    ("Zarasai", "Зарасай", ()),
    ("Naujoji Akmenė", "Науджойи Акмене", ()),
    ("Kuršėnai", "Куршенай", ()),
    ("Pabradė", "Пабраде", ()),
    ("Kazlų Rūda", "Казлу Руда", ()),
    ("Šilalė", "Шилале", ()),
    ("Skuodas", "Скуодас", ()),
    ("Ignalina", "Игналина", ()),
    ("Lazdijai", "Лаздияй", ()),
    ("Kalvarija", "Калвария", ()),
    ("Šalčininkai", "Шальчининкай", ()),
    ("Kupiškis", "Купишкис", ()),
    ("Pakruojis", "Пакруойи", ()),
    ("Biržai", "Биржай", ()),
    ("Rietavas", "Риетавас", ()),
    ("Garliava", "Гарлява", ()),
    ("Akmenė", "Акмене", ()),
    ("Karmėlava", "Кармелава", ()),
    ("Vilkyškiai", "Вилкишкяй", ()),
    ("Nemenčinė", "Неменчине", ("Nemencine",)),
    ("Neringa", "Неринга", ()),
    ("Švenčionys", "Швенчёнис", ("Швенчионис",)),
    ("Pagėgiai", "Пагегиай", ()),
    ("Maišiagala", "Майшягала", ()),
    ("Vilkija", "Вилькия", ()),
    ("Baisogala", "Байсогала", ()),
    ("Ariogala", "Ариогала", ()),
    ("Kretingalė", "Кретингале", ()),
    ("Švėkšna", "Швекшна", ()),
    ("Rusnė", "Русне", ()),
    ("Raudondvaris", "Раудондварис", ()),
    ("Šeduva", "Шедува", ()),
    ("Kernavė", "Кернаве", ()),
    ("Kruonis", "Круонис", ()),
    ("Kazlų rūda", "Казлу Руда", ()),  # lowercase variant seen in data
    # ── Lithuanian counties (apskritis) — kept as-is, no RU form in data ──
    ("Kauno apskritis", None, ()),
    ("Vilniaus apskritis", None, ()),
    ("Panevėžio apskritis", None, ()),
    ("Šiaulių apskritis", None, ()),
    ("Klaipėdos apskritis", None, ()),
    ("Telšių apskritis", None, ()),
    ("Utenos apskritis", None, ()),
    ("Marijampolės apskritis", None, ()),
    ("Alytaus apskritis", None, ()),
    ("Tauragės apskritis", None, ()),
    # ── Foreign cities ───────────────────────────────────────────────────
    ("Tallinn", "Таллин", ()),
    # ── Countries ────────────────────────────────────────────────────────
    ("Lithuania", "Литва", ("Lietuva", "Visa Lietuva", "Kitur Lietuvoje")),
    ("Estonia", "Эстония", ("Estija",)),
    ("Latvia", "Латвия", ("Latvija",)),
    ("Germany", "Германия", ("Vokietija",)),
    ("Netherlands", "Нидерланды", ("Olandija", "Nyderlandai")),
    ("Sweden", "Швеция", ("Švedija",)),
    ("Norway", "Норвегия", ("Norvegija",)),
    ("Iceland", "Исландия", ("Islandija",)),
    ("Belgium", "Бельгия", ("Belgija",)),
    ("Finland", "Финляндия", ("Suomija",)),
    ("France", "Франция", ("Prancūzija",)),
    ("Denmark", "Дания", ("Danija",)),
    ("Spain", "Испания", ("Ispanija",)),
    ("Ireland", "Ирландия", ("Airija",)),
    ("United Kingdom", "Великобритания", ("Jungtinė Karalystė",)),
    ("USA", "США", ("JAV",)),
    ("Italy", "Италия", ("Italija",)),
    ("Cyprus", "Кипр", ("Kipras",)),
    ("Ukraine", "Украина", ("Ukraina",)),
    ("Poland", "Польша", ("Lenkija",)),
    ("Nigeria", "Нигерия", ()),
    ("Malta", "Мальта", ()),
    ("Kenya", "Кения", ()),
    ("Israel", "Израиль", ("Izraelis",)),
    ("Morocco", "Марокко", ()),
    ("Romania", "Румыния", ("Rumunija",)),
    ("Turkey", "Турция", ("Turkija",)),
    ("Hungary", "Венгрия", ("Vengrija",)),
    ("Tanzania", "Танзания", ()),
    # ── Special / remote ─────────────────────────────────────────────────
    ("Remote", "Удалённо", ("Работа из дома", "Iš namų", "Work from home", "Nuotolinis")),
    ("Abroad", "За рубежом", ("Užsienyje",)),
]


def _normalize_key(text: str) -> str:
    """Case- and diacritic-insensitive lookup key (collapses inner whitespace)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def _build_index() -> dict[str, tuple[str, str | None]]:
    index: dict[str, tuple[str, str | None]] = {}
    for name_en, name_ru, aliases in _CITY_DATA:
        target = (name_en, name_ru)
        keys = [name_en, *aliases]
        if name_ru:
            keys.append(name_ru)
        for key in keys:
            index.setdefault(_normalize_key(key), target)
    return index


_ALIAS_INDEX = _build_index()


def normalize_city(raw: str | None) -> tuple[str, str | None] | None:
    """Resolve a raw location string.

    Returns ``(name_en, name_translated)`` or ``None`` for empty input.
    Unknown locations fall back to ``(raw_stripped, None)`` so ``name_en`` is
    always populated.
    """
    if not raw or not raw.strip():
        return None
    key = _normalize_key(raw)
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    return raw.strip(), None
