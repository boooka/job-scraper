"""One-off backfill: create company_groups and link existing companies.

Idempotent — safe to re-run. New scrapes link the group automatically via
:class:`~src.db.repository.CompanyGroupRepository`.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select, update

from src.db.engine import get_session
from src.db.repository import CompanyGroupRepository
from src.logger import get_logger
from src.models.orm import Company
from src.services.company_normalizer import normalize_company_name

log = get_logger(__name__)


async def backfill_company_groups() -> dict[str, int]:
    """Resolve every company to a canonical group and set companies.group_id."""
    async with get_session() as session:
        result = await session.execute(select(Company.id, Company.name))
        rows = list(result.all())

    # Group company ids by normalized key (skip names that yield an empty key)
    by_key: dict[str, list] = defaultdict(list)
    rep_name: dict[str, str] = {}
    for cid, name in rows:
        key = normalize_company_name(name)
        if not key:
            continue
        by_key[key].append(cid)
        # Representative display name: the longest raw name in the group
        if key not in rep_name or len(name or "") > len(rep_name[key]):
            rep_name[key] = (name or "").strip()

    log.info("company_backfill.start", companies=len(rows), groups=len(by_key))

    updated = 0
    for key, ids in by_key.items():
        async with get_session() as session:
            group_repo = CompanyGroupRepository(session)
            group = await group_repo.get_or_create(rep_name[key])
            if group is None:
                continue
            res = await session.execute(
                update(Company)
                .where(Company.id.in_(ids), Company.group_id.is_distinct_from(group.id))
                .values(group_id=group.id)
            )
            updated += res.rowcount or 0

    summary = {"companies": len(rows), "groups": len(by_key), "companies_linked": updated}
    log.info("company_backfill.complete", **summary)
    return summary
