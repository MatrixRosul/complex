"""
Прогін черги: PENDING/STALE/REJECTED → MACHINE (у чергу схвалення) або FAILED.

Три шляхи, бо три різні гарантії (TRANSLATION.md §3, §4, §5.3):

  1. ПРОСТІ види (attribute_name, attribute_value, unit, category_name, seo_*, short_desc)
     N записів → 1 запит (50 або 25 сегментів). segment.id = "te-<entry.id>".

  2. PRODUCT_NAME — те саме, АЛЕ спершу split_name(): бренд і код моделі відрізаються
     детерміністично і в Claude не їдуть узагалі. Назад: f"{ru_head} {tail}".

  3. HTML (product_description, page_html, news_html) — 1 запис = 1 запит.
     Опис розбирається в DOM, у модель їде ЛИШЕ текст із плейсхолдерами <0>…</0>.
     Розмітка, <img src>, R2-URL через модель НЕ ПРОХОДЯТЬ.

Жоден невалідований переклад не потрапляє навіть у чергу на схвалення: помилка
валідації → FAILED (+ ретрай на сильнішій моделі), а не MACHINE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dc_field
from decimal import Decimal

from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from translation import conf
from translation.client import BaseClient, BudgetExceeded, TranslationRequest, get_client
from translation.models import (
    KIND_REVIEW_PRIORITY,
    TranslationEntry,
    TranslationKind,
    TranslationStatus,
)
from translation.prompts import PROMPT_VERSION
from translation.segmentation import (
    HtmlDocument,
    Segment,
    apply_html,
    join_name,
    parse_html,
    split_name,
    validate_html_translation,
    validate_plain_translation,
)
from translation.services import glossary

logger = logging.getLogger(__name__)

RETRYABLE = (TranslationStatus.PENDING, TranslationStatus.STALE, TranslationStatus.REJECTED)


@dataclass
class _Job:
    request: TranslationRequest
    # segment_id → entry  (прості види і product_name)
    entries: dict[str, TranslationEntry] = dc_field(default_factory=dict)
    # хвости назв, які ми відрізали ДО відправки: segment_id → "Bosch PNK6B2P40R"
    tails: dict[str, str] = dc_field(default_factory=dict)
    # HTML: один запис на запит
    html_entry: TranslationEntry | None = None
    html_doc: HtmlDocument | None = None
    html_segments: list[Segment] = dc_field(default_factory=list)


def _pick_model(entry: TranslationEntry) -> str:
    """Словник і ретраї — на Opus 4.8. Обсяг — на Sonnet 5 (TRANSLATION.md §7.2).

    Словник: обсяг мізерний (~4 300 коротких рядків, $0,65), а вплив максимальний — цей
    текст реплікується в 300 000 місць каталогу і потрапляє в глосарій. Економити на
    моделі тут безглуздо.
    """
    if entry.kind in conf.DICTIONARY_KINDS:
        return conf.dict_model()
    if entry.status == TranslationStatus.REJECTED or entry.validation_errors:
        return conf.dict_model()  # людина відхилила / валідатор упав → сильніша модель
    return conf.bulk_model()


def _pending_qs(kind: str | None, statuses=RETRYABLE):
    order = Case(
        *[When(kind=k, then=Value(p)) for k, p in KIND_REVIEW_PRIORITY.items()],
        default=Value(99),
        output_field=IntegerField(),
    )
    qs = (
        TranslationEntry.objects.filter(status__in=statuses, target_lang="ru")
        .annotate(_prio=order)
        .order_by("_prio", "pk")
    )
    if kind:
        qs = qs.filter(kind=kind)
    return qs


def _build_jobs(entries: list[TranslationEntry]) -> list[_Job]:
    jobs: list[_Job] = []
    simple: dict[str, list[TranslationEntry]] = {}

    for entry in entries:
        if entry.kind in conf.HTML_KINDS:
            doc = parse_html(entry.source_text)
            if not doc.segments:
                continue  # у HTML немає перекладного тексту — платити нема за що
            jobs.append(
                _Job(
                    request=TranslationRequest(
                        custom_id=f"te-{entry.pk}",
                        kind=entry.kind,
                        segments=[{"id": s.id, "uk": s.uk} for s in doc.segments],
                        model=_pick_model(entry),
                        context=_html_context(entry),
                    ),
                    html_entry=entry,
                    html_doc=doc,
                    html_segments=doc.segments,
                )
            )
        else:
            simple.setdefault(entry.kind, []).append(entry)

    for kind, group in simple.items():
        size = conf.BATCH_SIZES.get(kind, conf.DEFAULT_BATCH_SIZE)
        for i in range(0, len(group), size):
            chunk = group[i : i + size]
            job = _Job(
                request=TranslationRequest(
                    custom_id=f"{kind}-{chunk[0].pk}",
                    kind=kind,
                    segments=[],
                    model=_pick_model(chunk[0]),
                )
            )
            for entry in chunk:
                sid = f"te-{entry.pk}"
                uk = entry.source_text

                if entry.kind == TranslationKind.PRODUCT_NAME:
                    # РІВЕНЬ 1: бренд і код моделі фізично не потрапляють у запит.
                    brand = _brand_of(entry)
                    head, tail = split_name(uk, brand)
                    if not head.strip():
                        # Назва — суцільний бренд+код («TEKA 83340602»). Перекладати нічого.
                        entry.target_text = uk
                        entry.status = TranslationStatus.DO_NOT_TRANSLATE
                        entry.save(update_fields=["target_text", "status", "updated_at"])
                        continue
                    uk = head
                    job.tails[sid] = tail

                job.request.segments.append({"id": sid, "uk": uk})
                job.entries[sid] = entry

            if job.request.segments:
                jobs.append(job)

    return jobs


def _brand_of(entry: TranslationEntry) -> str:
    obj = entry.target
    brand = getattr(obj, "brand", None) if obj is not None else None
    return getattr(brand, "name", "") or ""


def _html_context(entry: TranslationEntry) -> str | None:
    """Контекст для узгодження роду й термінології: «Робот-пилосос Xiaomi …»."""
    obj = entry.target
    name = getattr(obj, "name_uk", None) or getattr(obj, "title_uk", None)
    return str(name) if name else None


def _month_spend() -> Decimal:
    from django.db.models import Sum

    start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = TranslationEntry.objects.filter(updated_at__gte=start).aggregate(s=Sum("cost_usd"))["s"]
    return Decimal(total or 0)


def translate_pending(
    *,
    kind: str | None = None,
    limit: int | None = None,
    client: BaseClient | None = None,
    dry_run: bool = False,
    use_batch: bool = False,
    only_failed: bool = False,
) -> dict[str, int]:
    """Прогнати чергу. Повертає лічильники."""
    statuses = (TranslationStatus.FAILED,) if only_failed else RETRYABLE
    qs = _pending_qs(kind, statuses)
    entries = list(qs[:limit] if limit else qs)
    stats = {"requests": 0, "machine": 0, "failed": 0, "skipped": 0, "entries": len(entries)}
    if not entries:
        return stats

    client = client or get_client(dry_run=dry_run)

    # Hard-cap: при перевищенні місячного бюджету не сабмітимо, а кричимо (§7.5).
    budget = conf.monthly_budget_usd()
    spent = _month_spend()
    if not getattr(client, "is_dry_run", False) and spent >= budget:
        raise BudgetExceeded(
            f"Витрачено ${spent} за місяць при ліміті ${budget}. "
            "Батчі не надсилаються. Підніми TRANSLATION_MONTHLY_BUDGET_USD або розберись."
        )

    block = glossary.build_block()
    gversion = glossary.current_version(block)

    jobs = _build_jobs(entries)
    if not jobs:
        return stats
    stats["requests"] = len(jobs)

    if use_batch and hasattr(client, "translate_many"):
        responses = client.translate_many([j.request for j in jobs], block)
    else:
        responses = [client.translate(j.request, block) for j in jobs]

    # ⚠️ Результати батчу приходять У ДОВІЛЬНОМУ ПОРЯДКУ — тільки по custom_id.
    by_id = {r.custom_id: r for r in responses}

    for job in jobs:
        resp = by_id.get(job.request.custom_id)
        if resp is None:
            continue
        if job.html_entry is not None:
            _ingest_html(job, resp, gversion, stats)
        else:
            _ingest_simple(job, resp, gversion, stats)

    logger.info("TRANSLATION translate_pending(%s): %s", kind or "all", stats)
    return stats


def _split_usage(resp, n: int) -> tuple[int, int, Decimal]:
    """Вартість запиту ділиться між його записами. Рівно — бо сегменти однорідні."""
    n = max(n, 1)
    return (
        resp.usage.input_tokens // n,
        resp.usage.output_tokens // n,
        (resp.usage.cost_usd / n).quantize(Decimal("0.000001")),
    )


def _stamp(entry: TranslationEntry, resp, model: str, gversion: str) -> None:
    entry.engine_model = model
    entry.prompt_version = PROMPT_VERSION
    entry.glossary_version = gversion


def _finish(entry: TranslationEntry, errors: list[str], stats: dict[str, int]) -> None:
    entry.validation_errors = errors
    if errors:
        entry.status = TranslationStatus.FAILED
        stats["failed"] += 1
    else:
        entry.status = TranslationStatus.MACHINE  # у чергу на схвалення, НЕ у вітрину
        stats["machine"] += 1
        if conf.auto_approve(entry.kind) and not entry.model_note:
            entry.save()
            entry.approve(None)
            return
    entry.save()


def _ingest_simple(job: _Job, resp, gversion: str, stats: dict[str, int]) -> None:
    if resp.error:
        for entry in job.entries.values():
            entry.validation_errors = [f"api:{resp.error}"]
            entry.status = TranslationStatus.FAILED
            entry.save(update_fields=["validation_errors", "status", "updated_at"])
            stats["failed"] += 1
        return

    tin, tout, cost = _split_usage(resp, len(job.entries))

    for sid, entry in job.entries.items():
        ru = resp.translations.get(sid, "")
        entry.model_note = resp.notes.get(sid, "")
        entry.input_tokens = tin
        entry.output_tokens = tout
        entry.cost_usd = cost
        _stamp(entry, resp, job.request.model, gversion)

        if not ru:
            _finish(entry, ["missing"], stats)
            continue

        # Валідуємо ПРОТИ ТОГО, ЩО РЕАЛЬНО ЇХАЛО В МОДЕЛЬ (для назв — тільки head).
        sent = next((s["uk"] for s in job.request.segments if s["id"] == sid), entry.source_text)
        errors = validate_plain_translation(sent, ru)

        if entry.kind == TranslationKind.PRODUCT_NAME:
            # Збірка назад: бренд і код повертаються з нашого боку, а не з відповіді моделі.
            entry.target_text = join_name(ru, job.tails.get(sid, ""))
        else:
            entry.target_text = ru

        _finish(entry, errors, stats)


def _ingest_html(job: _Job, resp, gversion: str, stats: dict[str, int]) -> None:
    entry = job.html_entry
    assert entry is not None
    _stamp(entry, resp, job.request.model, gversion)
    entry.input_tokens = resp.usage.input_tokens
    entry.output_tokens = resp.usage.output_tokens
    entry.cost_usd = resp.usage.cost_usd
    entry.model_note = "; ".join(resp.notes.values())[:2000]

    if resp.error:
        _finish(entry, [f"api:{resp.error}"], stats)
        return

    assert job.html_doc is not None
    dst_html = apply_html(job.html_doc, resp.translations)
    errors = validate_html_translation(
        entry.source_text, dst_html, job.html_segments, resp.translations
    )
    entry.target_text = dst_html
    _finish(entry, errors, stats)
