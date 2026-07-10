"""Delete bogus location-change rows from vacancy_changes.

Before the cvmarket parser fix, every scrape assigned a neighbouring job's city
to a vacancy (the data-event payload was shifted), so vacancy_changes filled up
with spurious `location` flip-flops (Kaunas↔Vilnius, ...). This command removes
those rows for a given source.

Dry-run by default — pass --apply to actually delete. Run on prod via:
    ./deploy/deploy-remote.sh manage clean_location_changes --apply
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import VacancyChange


class Command(BaseCommand):
    help = "Delete bogus location-change rows from vacancy_changes for a source."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source",
            default="cvmarket",
            help="Vacancy source to clean (default: cvmarket).",
        )
        parser.add_argument(
            "--field",
            default="location",
            help="Tracked field whose changes to delete (default: location).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete. Without it the command only reports (dry-run).",
        )

    def handle(self, *args, **opts) -> None:
        source = opts["source"]
        field = opts["field"]

        qs = VacancyChange.objects.filter(field_name=field, vacancy__source=source)
        count = qs.count()

        if count == 0:
            self.stdout.write(f"No '{field}' changes found for source '{source}'. Nothing to do.")
            return

        if not opts["apply"]:
            self.stdout.write(
                f"[dry-run] Would delete {count} '{field}' change row(s) for source "
                f"'{source}'. Re-run with --apply to delete."
            )
            return

        deleted, _ = qs.delete()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {deleted} '{field}' change row(s) for source '{source}'.")
        )
