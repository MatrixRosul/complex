"""Імпорт результатів збирача (harvest_*.js) у чергу SpecHarvestJob.

Збирання специфікацій живе у воркфлоу (веб + Claude), а сюди кладеться його вивід — так
дороге зовнішнє збирання відокремлене від транзакційного застосування в каталог.

Приймає:
  * journal.jsonl воркфлоу (рядки {"type":"result","result":{...}}), АБО
  * JSON-масив обʼєктів товарів, АБО
  * JSON-обʼєкт {"harvested":[...]} (повний return воркфлоу).

Приклад:
  python manage.py import_harvest .../wf_.../journal.jsonl --category dishwasher --replace
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aispecs.models import SpecHarvestJob
from catalog.models import Product


def _clean_desc(text: str) -> str:
    """Прибрати артефакти структурованого виводу (буває у слабших моделей: у опис влазить
    хвіст на кшталт `<parameter name="warnings">`). Обрізаємо на маркері й чистимо хвіст."""
    if not text:
        return ""
    for marker in ("<parameter", "</parameter", "\\n<", '",\\n'):
        i = text.find(marker)
        if i != -1:
            text = text[:i]
    return text.rstrip(' \n\t\r",').strip()


def _load(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    # 1) цілий JSON (масив або {harvested:[...]})
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("harvested"), list):
        return [p for p in data["harvested"] if isinstance(p, dict) and "product_id" in p]
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict) and "product_id" in p]
    # 2) jsonl-журнал — беремо result-рядки з product_id, останній на товар виграє
    by_id: dict[int, dict] = {}
    for line in text.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "result":
            continue
        res = obj.get("result")
        if isinstance(res, dict) and "product_id" in res:
            by_id[res["product_id"]] = res
    return list(by_id.values())


class Command(BaseCommand):
    help = "Імпорт результатів harvest у чергу SpecHarvestJob (на перегляд)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("path", help="Шлях до journal.jsonl або JSON з результатами")
        parser.add_argument("--category", default="dishwasher", help="Ключ шаблону категорії")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Видалити наявні джоби «на перегляд» цих товарів перед створенням",
        )

    def handle(self, *args, **opts) -> None:
        products = _load(opts["path"])
        if not products:
            raise CommandError("У файлі не знайдено жодного товару з product_id.")

        created = skipped = 0
        for p in products:
            sku = p.get("sku")
            pid = p.get("product_id")
            # матч за SKU (стабільний ключ, однаковий на dev/prod), інакше за id
            product = None
            if sku:
                product = Product.objects.filter(sku=sku).first()
            if product is None and pid:
                product = Product.objects.filter(pk=pid).first()
            if product is None:
                self.stderr.write(f"  пропуск: немає товару sku={sku} id={pid}")
                skipped += 1
                continue
            if opts["replace"]:
                SpecHarvestJob.objects.filter(
                    product=product, status=SpecHarvestJob.Status.NEEDS_REVIEW
                ).delete()
            SpecHarvestJob.objects.create(
                product=product,
                category_key=opts["category"],
                status=SpecHarvestJob.Status.NEEDS_REVIEW,
                model_code=(p.get("model_code") or "")[:120],
                matched=bool(p.get("matched")),
                match_confidence=p.get("match_confidence") or "",
                source_type=(p.get("source_type") or "")[:20],
                primary_source_url=(p.get("primary_source_url") or "")[:1000],
                proposed_specs=p.get("specs") or [],
                proposed_programs=p.get("programs") or [],
                proposed_description=_clean_desc(p.get("description_uk") or ""),
                warnings=p.get("warnings") or [],
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Створено джоб: {created}. Пропущено: {skipped}."))
