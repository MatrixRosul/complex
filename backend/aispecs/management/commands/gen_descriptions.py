"""Пачкова генерація коротких описів із уже зібраних характеристик (дешевий Haiku).

ЛИШЕ на основі наданих характеристик — без веб-читання, без вигадок, без цін/знижок.
Пише в Product.description_uk (тільки де порожньо; ручне/наявне не чіпає).
"""

from __future__ import annotations

import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.models import Product

MODEL = "claude-haiku-4-5-20251001"
SYS = (
    "Ти пишеш короткі описи товарів побутової техніки УКРАЇНСЬКОЮ для інтернет-магазину. "
    "Для КОЖНОГО товару — 3-4 природні речення на основі наданих характеристик. "
    "Якщо характеристик мало або немає — короткий нейтральний опис за НАЗВОЮ і ТИПОМ товару, "
    "БЕЗ вигадування конкретних цифр/параметрів. "
    "НЕ вигадуй фактів, яких немає. НЕ згадуй ціни, знижки, акції. "
    'Пиши по суті, без води. Поверни СТРОГО JSON-масив об\'єктів {"sku":..., "text":...}, без пояснень.'
)


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    i, j = text.find("["), text.rfind("]")
    return json.loads(text[i : j + 1]) if i != -1 and j != -1 else []


class Command(BaseCommand):
    help = "Згенерувати описи з характеристик (Haiku, пачками)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--batch", type=int, default=12)
        parser.add_argument("--limit", type=int)
        parser.add_argument("--dry", action="store_true")

    def handle(self, *args, **o) -> None:
        key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
        if not key:
            self.stderr.write("ANTHROPIC_API_KEY не заданий")
            return
        import anthropic

        client = anthropic.Anthropic(api_key=key, max_retries=4)

        # УСІ товари без опису (характеристики де є; де нема — опис за назвою+типом)
        qs = Product.objects.all().select_related("category")
        todo = []
        for p in qs:
            if (p.description_uk or "").strip():
                continue
            specs = [
                f"{s.get('n')}: {s.get('v')}{(' ' + s.get('u')) if s.get('u') else ''}"
                for s in (p.specs_json_uk or [])
                if s.get("v") and s.get("v") != "Немає даних"
            ]
            todo.append((p, specs[:16]))
        if o["limit"]:
            todo = todo[: o["limit"]]
        self.stdout.write(f"Товарів для опису: {len(todo)}")

        done = failed = 0
        for i in range(0, len(todo), o["batch"]):
            chunk = todo[i : i + o["batch"]]
            payload = [
                {
                    "sku": p.sku,
                    "name": p.name,
                    "category": str(p.category or ""),
                    "specs": "; ".join(specs),
                }
                for p, specs in chunk
            ]
            user = "Товари (напиши опис кожному):\n" + json.dumps(payload, ensure_ascii=False)
            try:
                msg = client.messages.create(
                    model=MODEL,
                    max_tokens=4000,
                    system=SYS,
                    messages=[{"role": "user", "content": user}],
                )
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                results = {r["sku"]: r["text"] for r in _extract_json(text) if r.get("sku")}
            except Exception as exc:
                self.stderr.write(f"  батч {i}: {exc}")
                failed += len(chunk)
                continue
            for p, _ in chunk:
                desc = (results.get(p.sku) or "").strip()
                if not desc:
                    continue
                if o["dry"]:
                    self.stdout.write(f"  {p.name[:36]}: {desc[:80]}")
                else:
                    p.description_uk = desc
                    p.description = desc
                    p.save(update_fields=["description", "description_uk"])
                done += 1
            self.stdout.write(f"  оброблено {min(i + o['batch'], len(todo))}/{len(todo)}")

        self.stdout.write(self.style.SUCCESS(f"Описів: {done}, помилок: {failed}"))
