"""
manage.py translate — керування чергою перекладу з консолі.

    manage.py translate --dry-run                  # план + РЕАЛЬНА оцінка вартості, $0
    manage.py translate --collect                  # поставити в чергу словник + контент
    manage.py translate --collect --kind=unit      # тільки один вид
    manage.py translate --limit=50                 # прогнати 50 записів (sync API)
    manage.py translate --batch --limit=1000       # прогнати через Batch API (−50%)
    manage.py translate --retry-failed             # переклад FAILED на сильнішій моделі
    manage.py translate --apply                    # СХВАЛЕНЕ → `<field>_ru`

⚠️ --apply НЕ схвалює нічого. Він лише переносить у вітрину те, що людина вже схвалила
   в адмінці. Машинний переклад не потрапляє в каталог жодною командою.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from translation import conf
from translation.models import TranslationEntry, TranslationKind, TranslationStatus
from translation.prompts import TRANSLATOR_RULES, estimate_tokens
from translation.services import (
    apply_approved,
    collect_dictionary,
    collect_targets,
    estimate_queue,
    glossary,
    translate_pending,
)


class Command(BaseCommand):
    help = "Черга машинного перекладу UA→RU (Claude API)"

    def add_arguments(self, parser):
        parser.add_argument("--kind", default=None, choices=[k for k, _ in TranslationKind.choices])
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="нічого не викликати й не писати: показати план і оцінку вартості",
        )
        parser.add_argument("--collect", action="store_true", help="наповнити чергу")
        parser.add_argument("--dictionary", action="store_true", help="--collect тільки словник")
        parser.add_argument("--apply", action="store_true", help="APPROVED → *_ru")
        parser.add_argument("--batch", action="store_true", help="через Batch API (−50%%)")
        parser.add_argument("--retry-failed", action="store_true")

    # -- helpers ------------------------------------------------------------

    def _print_models(self):
        bulk, dictm = conf.bulk_model(), conf.dict_model()
        self.stdout.write(self.style.MIGRATE_HEADING("Моделі"))
        self.stdout.write(f"  обсяг (назви/описи/SEO) : {bulk}")
        self.stdout.write(f"  словник + ретраї        : {dictm}")

        configured = getattr(settings, "ANTHROPIC_TRANSLATION_MODEL", "")
        if configured and configured != bulk:
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠ ANTHROPIC_TRANSLATION_MODEL={configured!r} не підходить "
                    f"({conf.UNSUPPORTED.get(configured, 'невідома модель')}); "
                    f"використовую {bulk!r}. Полагодь .env"
                )
            )
        if not (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip():
            self.stdout.write(self.style.WARNING("  ⚠ ANTHROPIC_API_KEY порожній → режим DRY-RUN"))

    def _print_queue(self):
        counts: dict[tuple[str, str], int] = {}
        for row in TranslationEntry.objects.values_list("kind", "status"):
            counts[row] = counts.get(row, 0) + 1
        if not counts:
            self.stdout.write(self.style.WARNING("Черга порожня. Запусти --collect."))
            return
        self.stdout.write(self.style.MIGRATE_HEADING("Черга"))
        self.stdout.write(f"  {'вид':<22}{'статус':<12}{'к-сть':>7}")
        for (kind, status), n in sorted(counts.items()):
            self.stdout.write(f"  {kind:<22}{status:<12}{n:>7}")

    def _print_estimate(self, kind, batch: bool):
        est = estimate_queue(kind=kind, batch=batch)
        block = glossary.build_block()
        sys_tok = estimate_tokens(TRANSLATOR_RULES + block)

        self.stdout.write(self.style.MIGRATE_HEADING("System-блок і кеш"))
        self.stdout.write(f"  правила + глосарій : ~{sys_tok} токенів")
        for model in {conf.bulk_model(), conf.dict_model()}:
            need = conf.spec(model).min_cache_prefix
            ok = sys_tok >= need
            mark = (
                self.style.SUCCESS("кеш працює")
                if ok
                else self.style.WARNING("кеш НЕ спрацює (мовчки!) — префікс закороткий")
            )
            self.stdout.write(f"  {model:<18} мін. префікс {need:>5} → {mark}")

        if not est.kinds:
            self.stdout.write(self.style.WARNING("\nНічого не в черзі — нічого оцінювати."))
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING(f"\nОцінка вартості ({'Batch −50%' if batch else 'sync'})")
        )
        self.stdout.write(
            f"  {'вид':<22}{'од.':>6}{'запитів':>9}{'in tok':>10}{'out tok':>10}"
            f"{'модель':>18}{'$':>10}"
        )
        for ke in sorted(est.kinds.values(), key=lambda k: -k.cost_usd):
            self.stdout.write(
                f"  {ke.kind:<22}{ke.entries:>6}{ke.requests:>9}{ke.input_tokens:>10}"
                f"{ke.output_tokens:>10}{ke.model:>18}{ke.cost_usd:>10.4f}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"  {'РАЗОМ':<22}{est.total_entries:>6}{est.total_requests:>9}"
                f"{'':>10}{'':>10}{'':>18}{est.total_cost:>10.4f}"
            )
        )
        budget = conf.monthly_budget_usd()
        self.stdout.write(f"\n  місячний hard-cap: ${budget}")
        if est.total_cost > budget:
            self.stdout.write(
                self.style.ERROR("  ⚠ оцінка перевищує бюджет — задача не сабмітитиме батчі")
            )

    # -- main ---------------------------------------------------------------

    def handle(self, *args, **opts):
        kind = opts["kind"]
        limit = opts["limit"]
        dry = opts["dry_run"]
        batch = opts["batch"]

        self._print_models()
        self.stdout.write("")

        if opts["collect"] or opts["dictionary"]:
            if dry:
                raise CommandError("--collect і --dry-run разом не мають сенсу")
            stats = (
                collect_dictionary(limit=limit)
                if opts["dictionary"]
                else collect_targets({kind} if kind else None, limit=limit)
            )
            self.stdout.write(self.style.SUCCESS(f"Зібрано: {dict(stats)}"))
            self.stdout.write("")

        self._print_queue()
        self.stdout.write("")

        if dry:
            self._print_estimate(kind, batch)
            self.stdout.write(self.style.SUCCESS("\nDRY-RUN: жодного виклику API, витрачено $0.00"))
            return

        if opts["apply"]:
            n = apply_approved(kind=kind, limit=limit)
            self.stdout.write(self.style.SUCCESS(f"Write-back: оновлено {n} полів *_ru"))
            return

        if opts["collect"] or opts["dictionary"]:
            return  # тільки збирали

        stats = translate_pending(
            kind=kind, limit=limit, use_batch=batch, only_failed=opts["retry_failed"]
        )
        self.stdout.write(self.style.SUCCESS(f"Прогін: {stats}"))

        waiting = TranslationEntry.objects.filter(status=TranslationStatus.MACHINE).count()
        self.stdout.write(
            f"У черзі на СХВАЛЕННЯ: {waiting}. У каталог не пішло нічого — "
            f"це робить тільки «Схвалити» в адмінці (або --apply)."
        )
