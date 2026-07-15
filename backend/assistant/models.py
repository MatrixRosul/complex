"""
ШІ-асистент «Лисичка» — моделі: сесія чату і повідомлення.

╔══════════════════════════════════════════════════════════════════════════════════════╗
║ ЧОМУ ТУТ САМЕ ТАКІ ПОЛЯ                                                              ║
║                                                                                      ║
║ 1. public_token (UUID), а не pk. Ендпоінт чату публічний і анонімний — сесію          ║
║    доводиться називати назовні. Автоінкрементний pk назовні — це і перелічуваність    ║
║    («скільки в них взагалі розмов?»), і спокуса підставити чужий id. UUID у запиті,   ║
║    pk — усередині.                                                                   ║
║                                                                                      ║
║ 2. ip_hash, а не сирий IP. IP потрібен рівно для одного: «це той самий відвідувач?»   ║
║    (антизловживання). Для цього достатньо sha256(ip + SECRET_KEY)[:32] — порівнювати  ║
║    можна, деанонімізувати ні. Сирі IP у БД публічного чат-бота — персональні дані     ║
║    без жодної потреби в них.                                                          ║
║                                                                                      ║
║ 3. cost_usd = Decimal(8,6), НІКОЛИ float. Один виклик коштує ~$0.002–0.05; шість      ║
║    знаків після коми — це і є одиниця обліку. float тут накопичував би похибку рівно  ║
║    на тому місці, де ми впираємось у місячний бюджет (ASSISTANT_MONTHLY_BUDGET_USD).  ║
║    Формат збігається з translation.conf.compute_cost() — там quantize("0.000001").    ║
║                                                                                      ║
║ 4. message_count денормалізований на сесії. Гард «≤ 30 повідомлень у сесії» ставиться ║
║    на КОЖНОМУ запиті ще до виклику моделі; COUNT(*) по ChatMessage заради цього —     ║
║    зайвий запит на гарячому шляху.                                                    ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

Бюджет рахується як Sum(ChatMessage.cost_usd) за поточний місяць — тому created_at у
ChatMessage індексований (він приходить з TimeStampedModel).

⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14: жодних GeneratedField і nulls_distinct (див. core/models.py).
   Тут вони й не потрібні: усі колонки NOT NULL, лічильники рахує Python.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

log = logging.getLogger(__name__)

__all__ = [
    "ChatMessage",
    "ChatRole",
    "ChatSession",
    "hash_ip",
]


def hash_ip(ip: str | None) -> str:
    """sha256(ip + SECRET_KEY)[:32] — стабільний ідентифікатор відвідувача без самого IP.

    SECRET_KEY у солі не косметика: без нього хеш від IPv4 підбирається повним перебором
    4 млрд варіантів за хвилини, тобто «хеш» не приховує нічого.
    """
    ip = (ip or "").strip()
    if not ip:
        return ""
    digest = hashlib.sha256(f"{ip}{settings.SECRET_KEY}".encode()).hexdigest()
    return digest[:32]


class ChatRole(models.TextChoices):
    """Ролі в тому вигляді, в якому їх розуміє Messages API."""

    USER = "user", "Користувач"
    ASSISTANT = "assistant", "Асистент"


class ChatSession(TimeStampedModel):
    """Одна розмова з лисичкою. Живе рівно стільки, скільки відкрита вкладка."""

    public_token = models.UUIDField(
        "Публічний токен",
        default=uuid4,
        unique=True,
        editable=False,
        help_text="Ідентифікатор сесії назовні. У API ніколи не віддаємо pk.",
    )
    locale = models.CharField("Мова", max_length=5, default="uk")
    ip_hash = models.CharField(
        "Відбиток IP",
        max_length=32,
        blank=True,
        help_text="sha256(IP + SECRET_KEY)[:32]. Сирий IP не зберігаємо.",
    )
    message_count = models.PositiveSmallIntegerField(
        "Повідомлень",
        default=0,
        help_text="Денормалізований лічильник: по ньому працює ліміт ASSISTANT_MAX_MESSAGES.",
    )
    cost_usd = models.DecimalField(
        "Вартість сесії, $", max_digits=8, decimal_places=6, default=Decimal("0")
    )

    class Meta:
        verbose_name = "Сесія чату"
        verbose_name_plural = "Сесії чату"
        ordering = ("-created_at", "-id")
        indexes = [
            # «Скільки сесій з цього IP за останню добу» — антизловживання.
            models.Index(fields=["ip_hash", "created_at"], name="asst_ip_created_idx"),
        ]

    def __str__(self) -> str:
        return f"Сесія {self.public_token} ({self.locale}, {self.message_count} повідомл.)"


class ChatMessage(TimeStampedModel):
    """Одне повідомлення. Це і аудит витрат, і матеріал «що люди насправді питають».

    tool_calls — компактний слід виклику інструментів ([{name, input, ...}]). Він потрібен
    не моделі (та отримує повну історію в запиті), а нам: без нього неможливо відповісти на
    питання «чому асистент відповів саме це» через тиждень після розмови.
    """

    session = models.ForeignKey(
        ChatSession,
        verbose_name="Сесія",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField("Роль", max_length=10, choices=ChatRole.choices)
    content = models.TextField("Текст", blank=True)
    tool_calls = models.JSONField("Виклики інструментів", default=list, blank=True)

    input_tokens = models.PositiveIntegerField("Вхідні токени", default=0)
    output_tokens = models.PositiveIntegerField("Вихідні токени", default=0)
    cache_read_tokens = models.PositiveIntegerField("Токени з кешу", default=0)
    cache_write_tokens = models.PositiveIntegerField("Токени в кеш", default=0)
    cost_usd = models.DecimalField(
        "Вартість, $", max_digits=8, decimal_places=6, default=Decimal("0")
    )

    class Meta:
        verbose_name = "Повідомлення чату"
        verbose_name_plural = "Повідомлення чату"
        ordering = ("created_at", "id")
        indexes = [
            # Історія однієї розмови (адмінка, інлайн).
            models.Index(fields=["session", "created_at"], name="asst_msg_session_idx"),
            # Місячний бюджет: Sum(cost_usd) WHERE created_at >= початок місяця.
            models.Index(fields=["created_at", "cost_usd"], name="asst_msg_spend_idx"),
        ]

    def __str__(self) -> str:
        preview = self.content[:60] + ("…" if len(self.content) > 60 else "")
        return f"{self.get_role_display()}: {preview}"
