"""
Платежі — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    Payment, PaymentWebhookEvent, провайдери (liqpay — активний), звірка

Джерела: DATA_MODEL.md §5, INTEGRATIONS.md §3, docs/research/LIQPAY.md, ADR-015 / ADR-025.

────────────────────────────────────────────────────────────────────────────
ПРО «PaymentProvider» — це НЕ таблиця (ADR-015)
────────────────────────────────────────────────────────────────────────────
Абстракція провайдера — `typing.Protocol` у `payments/providers/base.py` з 5 методами
(create_checkout / verify_callback / parse_callback / fetch_status / refund), а активний
провайдер обирається значенням `SiteSettings.payment_provider` (зміна = поле в адмінці,
не деплой). Окрема модель `PaymentProvider` у БД не заводиться свідомо: рядок у таблиці
не додає жодного інваріанта (ключі живуть у docker secrets, реалізація — у коді), зате
створює другий, розсинхронізований з кодом, «список провайдерів». Тут провайдер — enum
`Payment.Provider`; на старті реалізація рівно одна — LiqPay.

────────────────────────────────────────────────────────────────────────────
ЧОТИРИ ЗАПОБІЖНИКИ ВЕБХУКА (INTEGRATIONS §3.4) — усі чотири обов'язкові
────────────────────────────────────────────────────────────────────────────
LiqPay може прислати той самий callback кілька разів і НЕ гарантує порядок.
  (1) ідемпотентність     → PaymentWebhookEvent, ДВА unique-ключі (див. нижче);
  (2) звірка суми         → payment.amount == payload.amount AND currency; інакше гроші
                            НЕ проводимо + алерт (без цього будь-хто, підібравши order_id,
                            провів би замовлення на 1 грн);
  (3) захист out-of-order → Payment.last_end_date: старіший callback не перетирає новіший;
  (4) фінальність стану   → PAID/REFUNDED незворотні; єдиний дозволений перехід paid → reversed.
      Реалізація — умовний UPDATE, а не read-modify-write:
          Payment.objects.filter(pk=..., status__in=["created", "pending"]).update(status="paid")
      0 оновлених рядків = подія прийшла не по порядку → ігноруємо (лог, не помилка).

🔴 Дедуп ТІЛЬКИ по sha256(body) недостатній: у тілі є `end_date`, тому кожен ретрай має інше
тіло і перший ключ не спрацює взагалі. Саме тому другий unique — (provider, invoice_id, status),
який від timestamp у тілі НЕ залежить.

🔴 ЗВІРКА (reconcile) — не «додатковий комфорт», а умова існування магазину: вебхук може не
дійти (бекенд лежав на деплої, Caddy віддав 502, LiqPay вичерпав ретраї). Без
`payments.tasks.reconcile_pending_payments` (beat, кожні 5 хв) буде: гроші зняті, а замовлення
ВІЧНО «очікує оплати». Індекс під неї — `pay_reconcile_idx (status, created_at)`.


⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.
  1. UniqueConstraint(..., nulls_distinct=False) → PG15. Тут скрізь використані ЧАСТКОВІ
     unique-обмеження з `condition=~Q(field="")` — вони дають ту саму семантику на PG14
     (порожній рядок замість NULL, ADR-016).
  2. GeneratedField → на PG14 лише STORED з IMMUTABLE-виразу. Жодного обчисленого поля тут
     немає: `paid_at` ставить `apply_payment_status()`, а не БД.
  3. MERGE (SQL) → PG15. Живемо на INSERT ... ON CONFLICT / get_or_create.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from core.models import TimeStampedModel


class Payment(TimeStampedModel):
    """Одна СПРОБА оплати. Одне `Order` → БАГАТО `Payment`.

    🔴 Чому багато (INTEGRATIONS §3.3): LiqPay прив'язує статус і рефанд до `order_id`. Якщо
    покупець скасував оплату і платить удруге з тим самим `order_id`, LiqPay ПОВЕРНЕ СТАРИЙ
    ПЛАТІЖ. Тому `order_id`, який ми шлемо в LiqPay, — це `Payment.reference` (UUID кожної
    спроби), а НЕ `Order.pk` і не `Order.number`.
    """

    class Provider(models.TextChoices):
        LIQPAY = "liqpay", "LiqPay (ПриватБанк)"
        MANUAL = "manual", "Ручне підтвердження"
        MONO = "mono", "monobank (не реалізовано)"
        # ⚡ ADR-025: провайдер обрано — LiqPay. `mono` лишається в enum лише як заявлена
        #   точка розширення; реалізації немає і не пишемо, доки не знадобиться
        #   («мертвий код гниє швидше за живий», ADR-015).

    class Status(models.TextChoices):
        CREATED = "created", "Створено"
        PENDING = "pending", "Очікує"
        HELD = "held", "Кошти заблоковано"  # action=hold → потрібен hold_completion
        PAID = "paid", "Оплачено"  # ФІНАЛЬНИЙ
        FAILED = "failed", "Помилка"
        REVERSED = "reversed", "Скасовано (сторновано)"
        REFUNDED = "refunded", "Повернуто"  # ФІНАЛЬНИЙ
        EXPIRED = "expired", "Протерміновано"  # >48 год у PENDING (reconcile)

    #: Статуси, які реконсайл добирає через `action=status` (INTEGRATIONS §3.6).
    RECONCILABLE_STATUSES = (Status.CREATED, Status.PENDING, Status.HELD)
    #: Незворотні стани: назад із них не відкочуємось ніколи.
    FINAL_STATUSES = (Status.PAID, Status.REFUNDED)

    class PayType(models.TextChoices):
        """`paytype` з callback LiqPay — чим саме заплатили.

        🔴 Юніт-економіка: `paypart` (оплата частинами) коштує МАГАЗИНУ 2,3–27,3% залежно від
        кількості платежів + еквайринг зверху; `moment_part` (миттєва розстрочка) — магазину
        безкоштовно, відсотки платить покупець. Фактичну ціну показує `receiver_commission`.
        """

        CARD = "card", "Картка"
        PRIVAT24 = "privat24", "Приват24"
        APAY = "apay", "Apple Pay"
        GPAY = "gpay", "Google Pay"
        PAYPART = "paypart", "Оплата частинами (комісію платить магазин)"
        MOMENT_PART = "moment_part", "Миттєва розстрочка (відсотки платить покупець)"
        CASH = "cash", "Готівка (термінал)"
        INVOICE = "invoice", "Рахунок"
        QR = "qr", "QR"

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    # PROTECT: замовлення з платежем не видаляється в принципі — це фінансовий слід.

    provider = models.CharField(
        "Провайдер", max_length=10, choices=Provider.choices, default=Provider.LIQPAY
    )

    reference = models.UUIDField("Reference (order_id для LiqPay)", default=uuid.uuid4, unique=True)
    # 🔴 ЦЕ і є `order_id`, який іде в LiqPay. Нова спроба оплати = новий Payment = новий
    #    reference. По ньому ж шукається платіж у callback і в reconcile (`action=status`).

    provider_invoice_id = models.CharField(
        "ID платежу у провайдера", max_length=120, blank=True, db_index=True
    )
    # LiqPay `payment_id` / `liqpay_order_id`. Порожній до першої відповіді провайдера.

    amount = models.DecimalField("Сума", max_digits=12, decimal_places=2)
    currency = models.CharField("Валюта", max_length=3, default="UAH")
    # ⚡ Звірка (2): amount МУСИТЬ дорівнювати order.total, а currency — "UAH".

    status = models.CharField(
        "Статус", max_length=10, choices=Status.choices, default=Status.CREATED, db_index=True
    )

    # --- як саме заплатили (з callback) ---
    paytype = models.CharField(
        "Спосіб оплати", max_length=12, choices=PayType.choices, blank=True, db_index=True
    )
    installment_count = models.PositiveSmallIntegerField(
        "Кількість платежів", null=True, blank=True
    )
    # ⚠️ ЗАПОВНЮЄТЬСЯ НЕ ЗАВЖДИ. У Checkout API немає параметра «максимум N платежів»:
    #    кількість (2–25) обирає ПОКУПЕЦЬ на сторінці LiqPay, і callback її не гарантує
    #    (INTEGRATIONS §3.7, LIQPAY.md §6). Бейдж «6 платежів» на картці товару —
    #    МАРКЕТИНГОВА ОБІЦЯНКА, а не технічне обмеження.
    #    Фактичну вартість розстрочки для магазину показує ТІЛЬКИ receiver_commission.
    #    Тому installment_count = довідкове поле (звіт/аналітика), NULL — легальний стан.
    is_moment_part = models.BooleanField("Миттєва розстрочка", null=True, blank=True)
    # Прапорець `moment_part` з callback. NULL = провайдер не сказав.

    receiver_commission = models.DecimalField(
        "Фактична комісія", max_digits=12, decimal_places=2, null=True, blank=True
    )
    # ⚡ ДЖЕРЕЛО ПРАВДИ ПО ЮНІТ-ЕКОНОМІЦІ (INTEGRATIONS §3.5). Тільки тут видно, скільки
    #   реально коштувала розстрочка на 12 платежів (13,7% + 1,5% еквайрингу).

    payment_url = models.URLField("Посилання на оплату", max_length=500, blank=True)

    # --- сирі payload'и провайдера ---
    raw_request = models.JSONField("Запит (редаговано)", default=dict, blank=True)
    raw_response = models.JSONField("Відповідь (редаговано)", default=dict, blank=True)
    # 🔴 РЕДАКЦІЯ ЗА ALLOWLIST перед збереженням (`providers/base.py::redact`, INTEGRATIONS §3.10).
    #   Ніяких ключів, підписів і PAN у БД — вони назавжди осідають ще й у бекапах.
    #   `sender_card_mask2` ("41****1234") — можна, повний PAN ми і не побачимо.

    sender_card_mask2 = models.CharField("Маска картки", max_length=32, blank=True)
    sender_card_bank = models.CharField("Банк-емітент", max_length=64, blank=True)

    err_code = models.CharField("Код помилки", max_length=64, blank=True)
    error_message = models.CharField("Помилка", max_length=500, blank=True)

    # --- захист від out-of-order (запобіжник 3) ---
    last_end_date = models.BigIntegerField("end_date останньої події", null=True, blank=True)
    # LiqPay кладе в тіло `end_date`/`create_date` (unix ms). Подія зі старішим end_date
    # НЕ перетирає новішу. Без цього ретрай `pending` після `success` відкочував би PAID.

    needs_bank_review = models.BooleanField("На перевірці банку", default=False, db_index=True)
    # ⚠️ Статуси `wait_accept` / `wait_secure`: кошти з покупця ВЖЕ СПИСАНІ, але магазин на
    #   верифікації. Замовлення беремо в роботу, але позначаємо — може висіти годинами.

    expires_at = models.DateTimeField("Діє до", null=True, blank=True)
    # `expired_date` у чекауті (UTC, «оплатити протягом 24 год»).
    paid_at = models.DateTimeField("Оплачено о", null=True, blank=True)
    last_polled_at = models.DateTimeField(
        "Останнє опитування", null=True, blank=True, db_index=True
    )
    # ⚡ Ставить reconcile. Дозволяє побачити платіж, який ми взагалі перестали опитувати.

    class Meta:
        verbose_name = "Платіж"
        verbose_name_plural = "Платежі"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_invoice_id"],
                condition=~Q(provider_invoice_id=""),
                name="uniq_provider_invoice",
            ),
            # ⚡ ЧАСТКОВИЙ unique замість nulls_distinct=False (PG14, ADR-016): порожній
            #   provider_invoice_id (платіж ще не дійшов до провайдера) — легальний і не
            #   унікальний стан.
            models.CheckConstraint(check=Q(amount__gt=0), name="pay_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="pay_reconcile_idx"),
            # ⚡ Під reconcile_pending_payments: status IN (created,pending,held)
            #    AND created_at BETWEEN now()-3d AND now()-3min.
            models.Index(fields=["order", "-created_at"], name="pay_order_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} · {self.amount} {self.currency} · {self.status}"

    @property
    def is_final(self) -> bool:
        return self.status in self.FINAL_STATUSES


class PaymentWebhookEvent(models.Model):
    """Сирий вебхук — ключ ідемпотентності + матеріал для розборів.

    НЕ TimeStampedModel: це журнал, `updated_at` тут не має сенсу (рядок незмінний після
    обробки). Ретеншн — 90 днів (`payments.tasks.purge_webhook_events`, beat 04:30):
    `raw_body` потрібен рівно для перевірки підпису і для форензики.
    """

    class Result(models.TextChoices):
        APPLIED = "applied", "Застосовано"
        DUPLICATE = "duplicate", "Дубль (вже оброблено)"
        STALE = "stale", "Застаріла подія (out-of-order)"
        FINAL_STATE = "final_state", "Стан уже фінальний"
        AMOUNT_MISMATCH = "amount_mismatch", "Сума не збігається"  # 🔴 алерт, гроші НЕ проводимо
        BAD_SIGNATURE = "bad_signature", "Невалідний підпис"  # 🔴 можлива атака
        UNKNOWN_PAYMENT = "unknown_payment", "Платіж не знайдено"
        SANDBOX_IN_PROD = "sandbox_in_prod", "Sandbox-платіж у проді"  # 🔴 витік ключа
        ERROR = "error", "Помилка обробки"

    id = models.BigAutoField(primary_key=True)

    provider = models.CharField("Провайдер", max_length=10, choices=Payment.Provider.choices)
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="webhook_events",
    )
    # null: вебхук міг прийти на невідомий order_id (чужий/підроблений) — його однаково треба
    # зберегти й повернути 200, інакше LiqPay довбатиме ретраями.

    invoice_id = models.CharField("order_id з тіла", max_length=120, blank=True, db_index=True)
    # = Payment.reference, як його прислав провайдер.
    status = models.CharField("Статус з тіла", max_length=20, blank=True)
    # 🔴 СИРИЙ статус провайдера ("success", "wait_accept", "sandbox"…), НЕ наш enum.
    #   Мапінг у наш — рівно один словник: `providers/liqpay/statuses.py`. Усе, чого немає
    #   в мапі → PENDING + WARN: новий статус не має мовчки ставати «оплачено» чи «провалено».

    body_hash = models.CharField("sha256(raw_body)", max_length=64)
    raw_body = models.TextField("Сире тіло (поле data)")
    signature = models.CharField("Підпис", max_length=128, blank=True)
    headers = models.JSONField("Заголовки", default=dict, blank=True)
    payload = models.JSONField("Розшифроване тіло", default=dict, blank=True)
    # 🔴 Дані беремо ТІЛЬКИ з розшифрованого `data`, НІКОЛИ з query/GET.

    signature_valid = models.BooleanField("Підпис валідний", default=False)
    # Перевірка — hmac.compare_digest (constant-time), інакше timing-oracle.

    processed = models.BooleanField("Оброблено", default=False, db_index=True)
    processed_at = models.DateTimeField("Оброблено о", null=True, blank=True)
    result = models.CharField("Результат", max_length=16, choices=Result.choices, blank=True)
    error = models.TextField("Помилка", blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Вебхук платежу"
        verbose_name_plural = "Вебхуки платежів"
        ordering = ("-created_at",)
        constraints = [
            # КЛЮЧ 1 — точний дубль тіла.
            models.UniqueConstraint(fields=["provider", "body_hash"], name="uniq_webhook_body"),
            # КЛЮЧ 2 — 🔴 CRITICAL. НЕ залежить від timestamp у тілі.
            # Провайдер кладе в тіло `end_date` → у кожного ретраю ІНШЕ тіло → інший body_hash
            # → перший ключ не спрацює ВЗАГАЛІ. Другий ключ ловить «той самий статус того ж
            # платежу» незалежно від часу в тілі.
            # ⚡ Частковий (PG14, ADR-016): invoice_id="" (нерозпізнаний вебхук) не унікальний.
            models.UniqueConstraint(
                fields=["provider", "invoice_id", "status"],
                condition=~Q(invoice_id=""),
                name="uniq_webhook_invoice_status",
            ),
        ]
        indexes = [
            models.Index(fields=["processed", "-created_at"], name="webhook_unprocessed_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.provider} · {self.invoice_id or '—'} · {self.status or '—'}"
