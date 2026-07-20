"""Скрапер ek.ua (E-Katalog) — характеристики по коду моделі, кодом (без ШІ).

URL товару на ek.ua: /ua/{БРЕНД}-{МОДЕЛЬ}.htm (напр. /ua/BOSCH-SMV4HVX07E.htm). Таблиця
характеристик у статичному HTML: назва в td.op1>span.gloss, значення в td.op3 (булеве =
img.prop-y/-n), заголовки груп — рядок з colspan=2 і span.op1-title.

Назви ek.ua мапляться в ключі нашої сітки через EK_ALIASES; чого нема — лягає як
generic-характеристика за назвою. Створює SpecHarvestJob (NEEDS_REVIEW), --dry лише друкує.

⚠️ Скрапінг публічних сторінок. Затримка між запитами, щоб не грузити сайт.
"""

from __future__ import annotations

import re
import time

import requests
from django.core.management.base import BaseCommand
from lxml import html as LH

from aispecs.category_specs import ek_aliases, get_template, template_fields
from aispecs.models import SpecHarvestJob
from catalog.models import Product

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
_SKIP_ROWS = {"офіційний сайт", "дата додавання на e-katalog", "поточна ціна", "де купити"}


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _num(value: str) -> float | None:
    m = re.search(r"-?\d+(?:[.,]\d+)?", value.replace(",", "."))
    return float(m.group().replace(",", ".")) if m else None


def ek_slug(brand: str, name: str) -> str:
    """Код моделі з назви → slug у форматі ek.ua (БРЕНД-МОДЕЛЬ, великими, дефіси)."""
    model = name
    if brand and brand.lower() in name.lower():
        i = name.lower().index(brand.lower())
        model = name[i + len(brand):]
    raw = f"{brand} {model}"
    return re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").upper()


def parse_ek(doc) -> list[tuple[str, str, str]]:
    """[(група, назва, значення)] з непорожнім значенням.

    ek має ДВІ верстки таблиць характеристик:
      · назва td.op1 + значення td.op3 (посудомийки, духовки, поверхні, холодильники…);
      · назва td.prop + значення td.val (мікрохвильовки, чайники, тостери, дрібна техніка).
    Обробляємо обидві.
    """
    out: list[tuple[str, str, str]] = []
    group = ""
    for tr in doc.xpath("//tr[td]"):
        name_td = tr.xpath('./td[contains(@class,"op1") or contains(@class,"prop")]')
        if not name_td:
            continue
        if tr.xpath('.//span[contains(@class,"op1-title")]'):
            group = _norm(" ".join(tr.xpath(".//text()")))
            continue
        gloss = name_td[0].xpath('.//span[contains(@class,"gloss")]//text()')
        name = _norm(" ".join(gloss or name_td[0].xpath(".//text()")))
        val_td = tr.xpath('./td[contains(@class,"op3") or contains(@class,"val")]')
        if not name or not val_td:
            continue
        cell = val_td[0]
        if cell.xpath('.//img[contains(@class,"prop-y")]'):
            val = "Так"
        elif cell.xpath('.//img[contains(@class,"prop-n")]'):
            val = "Ні"
        else:
            val = _norm(" ".join(cell.xpath(".//text()")))
            val = {"+": "Так", "-": "Ні"}.get(val, val)
        if val and name.lower() not in _SKIP_ROWS:
            out.append((group, name, val))
    return out


def _clean_energy(val: str) -> str:
    """'A+ / 295 кВт год/рік /' → 'A+'; 'D' → 'D'."""
    m = re.match(r"\s*([A-GА-Г]\+{0,3})", val)
    return m.group(1) if m else val.split("/")[0].strip()


def to_specs(rows, category_key, source_url) -> tuple[list[dict], list[str]]:
    """ek-пари → proposed_specs у форматі сітки (+ перелік програм окремо)."""
    aliases = ek_aliases(category_key)
    numeric = {f["key"] for f in template_fields(category_key) if f["type"] == "number"}
    specs, programs = [], []
    energy_is_new = False  # пріоритет «клас (new)» над старим
    for group, name, val in rows:
        low = name.lower()
        key = aliases.get(low)
        if low in ("режими мийки", "програми", "режими роботи"):
            programs = [p for p in re.split(r"\s{2,}|,|·", val) if len(p.strip()) > 1][:20]
            continue
        if key == "energy_class":
            if energy_is_new and "(new)" not in low:
                continue  # вже маємо нову шкалу — стару пропускаємо
            val = _clean_energy(val)
            if "(new)" in low:
                energy_is_new = True
            specs = [s for s in specs if s.get("key") != "energy_class"]  # лишаємо одне
        item = {"confidence": "high", "exact_code": True, "source_url": source_url}
        if key:
            item["key"] = key
            if key in numeric and _num(val) is not None:
                item["num"] = _num(val)
            else:
                item["text"] = val
        else:
            # generic: «144 л» → num=144 + unit=«л» (щоб не було подвійних одиниць і щоб
            # «1» не читалось як булеве). Булеві ek уже прийшли як «Так»/«Ні» → лишаємо текст.
            item["name_uk"] = name
            item["group_uk"] = group or "Основні"
            m = re.match(r"^(-?\d+(?:[.,]\d+)?)\s*(.*)$", val)
            if m and val not in ("Так", "Ні"):
                item["num"] = float(m.group(1).replace(",", "."))
                unit = m.group(2).strip()
                if unit and len(unit) <= 16:
                    item["unit"] = unit
            else:
                item["text"] = val
        specs.append(item)
    return specs, programs


class Command(BaseCommand):
    help = "Скрапер ek.ua: характеристики по коду моделі в чергу SpecHarvestJob."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--category", required=True, help="ключ шаблону (dishwasher/oven/hob)")
        parser.add_argument("--filter", default="", help="icontains по назві категорії товару")
        parser.add_argument("--ids", nargs="*", type=int)
        parser.add_argument("--limit", type=int)
        parser.add_argument("--dry", action="store_true", help="лише друк, без запису в чергу")
        parser.add_argument("--out", default="", help="шлях: зберегти сирі+мапнуті дані у JSON (без запису в чергу)")
        parser.add_argument("--all", action="store_true",
                            help="усі категоризовані товари (крім службової та вже зроблених grid-категорій)")
        parser.add_argument("--sleep", type=float, default=1.5)

    def handle(self, *args, **o) -> None:
        # Сітка не обов'язкова: без неї працюємо generic (усі назви ek як name_uk).
        qs = Product.objects.select_related("brand").all()
        if o["ids"]:
            qs = qs.filter(pk__in=o["ids"])
        elif o["all"]:
            qs = qs.exclude(category__isnull=True).exclude(
                category__name_uk__icontains="Без категорії"
            )
            for term in ("посудомий", "духов", "варильн"):  # уже зроблені grid-категорії
                qs = qs.exclude(category__name_uk__icontains=term)
        elif o["filter"]:
            qs = qs.filter(category__name_uk__icontains=o["filter"])
        if o["limit"]:
            qs = qs[: o["limit"]]

        found = missed = 0
        misses = []
        collected: list[dict] = []
        for p in qs:
            brand = str(p.brand) if p.brand else ""
            slug = ek_slug(brand, p.name)
            url = f"https://ek.ua/ua/{slug}.htm"
            try:
                r = requests.get(url, headers=UA, timeout=25)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"  net {p.id}: {exc}")
                missed += 1
                continue
            if r.status_code != 200 or "op1" not in r.text:
                missed += 1
                misses.append(f"{p.name} → {url} [{r.status_code}]")
                time.sleep(o["sleep"])
                continue
            rows = parse_ek(LH.fromstring(r.text))
            specs, programs = to_specs(rows, o["category"], url)
            confirmed = sum(1 for s in specs if s.get("key"))
            found += 1
            if o["out"]:
                collected.append({
                    "sku": p.sku, "product_id": p.id, "name": p.name,
                    "category_key": o["category"], "model_code": slug,
                    "primary_source_url": url, "source_type": "ek.ua",
                    "specs": specs, "programs": programs,
                    "raw_ek": [{"group": g, "name": n, "value": v} for g, n, v in rows],
                })
            elif o["dry"]:
                self.stdout.write(f"\n✓ {p.name}  ({url})")
                self.stdout.write(f"    сітка: {confirmed} · усього: {len(specs)} · програм: {len(programs)}")
                for s in specs[:8]:
                    v = s.get("num", s.get("text", ""))
                    self.stdout.write(f"      {s.get('key') or s.get('name_uk'):28} = {v}")
            else:
                SpecHarvestJob.objects.filter(
                    product=p, status=SpecHarvestJob.Status.NEEDS_REVIEW
                ).delete()
                SpecHarvestJob.objects.create(
                    product=p, category_key=o["category"],
                    status=SpecHarvestJob.Status.NEEDS_REVIEW,
                    model_code=slug, matched=True, match_confidence="high",
                    source_type="other", primary_source_url=url,
                    proposed_specs=specs, proposed_programs=programs,
                )
            time.sleep(o["sleep"])

        if o["out"] and collected:
            import json
            with open(o["out"], "w", encoding="utf-8") as f:
                json.dump(collected, f, ensure_ascii=False, indent=1)
            self.stdout.write(self.style.SUCCESS(f"Збережено у {o['out']}: {len(collected)} товарів"))

        self.stdout.write(self.style.SUCCESS(f"\nЗнайдено на ek.ua: {found} · не знайдено: {missed}"))
        for m in misses[:30]:
            self.stdout.write(f"  ✗ {m}")
