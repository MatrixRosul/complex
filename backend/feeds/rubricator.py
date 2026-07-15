"""Імпорт рубрикатора Hotline (`hotline_tree_uk.csv`).

Без нього фід НЕ ПРАЦЮЄ ВЗАГАЛІ: `Category.hotline_category` — FK на `HotlineCategory`, а товар
без прив'язки до рубрикатора у фід не потрапляє (skip: no_hotline_category). Порожня таблиця
`HotlineCategory` = порожній фід.

🔴 ПАСТКИ ФАЙЛА (перевірено live, HTTP 200, ~28 КБ, 1223 рядки):
  • кодування — **windows-1251**, не UTF-8 → `body.decode("cp1251")`;
  • це НЕ CSV: дерево з відступами, і **кількість провідних `;` = глибина**;
  • ID категорій у файлі НЕМАЄ — тільки назви. Ключ upsert'у — `path` (повний шлях).

Зникла з рубрикатора категорія → `is_active=False`, а НЕ DELETE: на неї посилаються наші
категорії (FK з catalog), і мовчазне видалення вибило б їхні товари з фіда.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx
from django.db import transaction

from feeds.models import HotlineCategory

log = logging.getLogger(__name__)

__all__ = ["TREE_URL", "TreeNode", "fetch_tree", "import_tree", "parse_tree"]

TREE_URL = "https://hotline.ua/download/hotline/hotline_tree_uk.csv"
ENCODING = "cp1251"  # ⚠️ НЕ UTF-8. Перевірено: інакше кирилиця розсиплеться на мохібаке.


@dataclass(frozen=True)
class TreeNode:
    depth: int
    name: str
    is_leaf: bool


def parse_tree(text: str) -> list[TreeNode]:
    """`;;Холодильники` → TreeNode(depth=2, name="Холодильники", is_leaf=…).

    is_leaf рахуємо з ФАЙЛА: вузол листовий, якщо наступний непорожній рядок має глибину
    НЕ БІЛЬШУ за його власну. Тільки листові можна ставити в Category.hotline_category —
    фід з нелистовою категорією Hotline не приймає.
    """
    raw: list[tuple[int, str]] = []
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.strip(" ;"):
            continue
        stripped = line.lstrip(";")
        raw.append((len(line) - len(stripped), stripped.strip()))

    nodes: list[TreeNode] = []
    for i, (depth, name) in enumerate(raw):
        next_depth = raw[i + 1][0] if i + 1 < len(raw) else -1
        nodes.append(TreeNode(depth=depth, name=name, is_leaf=next_depth <= depth))
    return nodes


def fetch_tree(*, timeout: float = 30.0) -> str:
    response = httpx.get(TREE_URL, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content.decode(ENCODING)


@transaction.atomic
def import_tree(nodes: list[TreeNode]) -> dict[str, int]:
    """Upsert по `path`. Повертає лічильники."""
    run_id = uuid.uuid4()
    stack: dict[int, HotlineCategory] = {}
    created = updated = 0

    for node in nodes:
        parent = stack.get(node.depth - 1) if node.depth else None
        path = f"{parent.path}/{node.name}" if parent else node.name

        obj, was_created = HotlineCategory.objects.update_or_create(
            path=path[:500],
            defaults={
                "name": node.name[:200],
                "parent": parent,
                "depth": node.depth,
                "is_leaf": node.is_leaf,
                "is_active": True,
                "last_seen_run": run_id,
            },
        )
        created += was_created
        updated += not was_created
        stack[node.depth] = obj
        # Глибші рівні попереднього піддерева більше не батьки — інакше «Холодильники» з одного
        # розділу стали б батьком категорії з наступного.
        for deeper in [d for d in stack if d > node.depth]:
            del stack[deeper]

    deactivated = (
        HotlineCategory.objects.filter(is_active=True)
        .exclude(last_seen_run=run_id)
        .update(is_active=False)
    )

    log.info(
        "hotline_tree_imported created=%s updated=%s deactivated=%s", created, updated, deactivated
    )
    return {"created": created, "updated": updated, "deactivated": deactivated, "total": len(nodes)}
