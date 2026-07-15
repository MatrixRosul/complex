"""
Замовлення — моделі.

Зона відповідальності (ARCHITECTURE.md §1):
    Order, OrderItem, OrderStatusHistory, checkout-сервіс

Джерело правди: DATA_MODEL.md §4.

⚠️ ОСОБИСТОГО КАБІНЕТУ НЕМАЄ. Замовлення оформлюється БЕЗ реєстрації, тому:
  * сторінка замовлення адресується `public_token` (UUID4), а НЕ номером.
    Було б /order/CMPX-260711-0042 — і перебір 0001..0100 віддавав би ПІБ, телефон і адресу
    будь-якого покупця. Це IDOR, і він тут коштує персональних даних, а не «зручності».
  * `number` лишається людським ідентифікатором для менеджера й телефонної розмови,
    але НІКОЛИ не є ключем публічного роуту.

⚠️ ЦІЛЬОВА БАЗА — PostgreSQL 14 (не 15/16). Міграції МУСЯТЬ застосуватись на PG14.
Що з Django 5 тут НЕ ПРАЦЮЄ і чим це замінювати:

  1. UniqueConstraint(..., nulls_distinct=False)  →  потребує PG15. На PG14 NULL-и завжди
     розрізняються, тому «унікальність з урахуванням NULL» робиться ДВОМА частковими
     обмеженнями або унікальним індексом по COALESCE. Тут не знадобилось.

  2. GeneratedField  →  НЕ використовуємо (див. OrderItem.line_total: там докладно, чим
     замінено і чому заміна дає ту саму гарантію).

  3. MERGE (SQL) → PG15. Використовуємо INSERT ... ON CONFLICT.

⚠️ SEQUENCE `order_number_seq` (ADR-014, DATA_MODEL §0.1) — створюється МІГРАЦІЄЮ:
       CREATE SEQUENCE IF NOT EXISTS order_number_seq START 1;
   Без неї Order.generate_number() впаде. Номер БЕРЕТЬСЯ З SEQUENCE, а не з count()+1:
   count()+1 — це гонка, при якій два одночасні checkout'и беруть один номер, і другий
   падає з IntegrityError ВЖЕ ПІСЛЯ створення платежу (гроші є, замовлення немає).
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.db.models import F, Q
from django.utils import timezone

from core.models import TimeStampedModel


class Order(TimeStampedModel):
    """Замовлення БЕЗ реєстрації покупця."""

    class Status(models.TextChoices):
        NEW = "new", "Новий"
        CONFIRMED = "confirmed", "Підтверджений"
        PACKED = "packed", "Скомплектований"
        SHIPPED = "shipped", "Відправлений"
        DELIVERED = "delivered", "Доставлений"
        DONE = "done", "Завершений"
        CANCELLED = "cancelled", "Скасований"
        RETURNED = "returned", "Повернення"

    class DeliveryMethod(models.TextChoices):
        NP_WAREHOUSE = "np_warehouse", "Відділення Нової Пошти"
        NP_POSTOMAT = "np_postomat", "Поштомат Нової Пошти"
        NP_COURIER = "np_courier", "Кур'єр Нової Пошти"
        PICKUP = "pickup", "Самовивіз (Ужгород)"
        LOCAL_COURIER = "local_courier", "Кур'єр по Ужгороду"

    class PaymentMethod(models.TextChoices):
        COD = "cod", "Накладений платіж"
        PREPAY = "prepay", "Повна передоплата (реквізити)"
        ONLINE = "online", "Онлайн-оплата картою"
        INSTALLMENT = "installment", "Оплата частинами"

    class PaymentStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Не потрібна"
        PENDING = "pending", "Очікує"
        PAID = "paid", "Оплачено"
        FAILED = "failed", "Помилка"
        REFUNDED = "refunded", "Повернуто"

    number = models.CharField("Номер", max_length=24, unique=True)  # "CMPX-260711-0042"
    # Генерується з SEQUENCE: nextval('order_number_seq') → f"CMPX-{d:%y%m%d}-{n:04d}"
    # (див. generate_number() нижче і коментар у шапці модуля).

    public_token = models.UUIDField(
        "Публічний токен", default=uuid.uuid4, unique=True, editable=False
    )
    # ⚡ ПУБЛІЧНИЙ РОУТ — /order/{public_token}, НЕ /order/{number}.
    #   Номер передбачуваний (дата + лічильник) → перебір CMPX-260711-0001..0100 віддав би
    #   ПІБ / телефон / адресу будь-кого. Токен — 122 біти ентропії, перебір неможливий.
    #   unique=True вже створює індекс — db_index=True поруч НЕ додавати.

    status = models.CharField(
        "Статус", max_length=12, choices=Status.choices, default=Status.NEW, db_index=True
    )
    idempotency_key = models.UUIDField("Ключ ідемпотентності", unique=True)
    # Клієнт генерує його один раз на checkout. Подвійний сабміт форми / ретрай мережі
    # не створює друге замовлення і другий платіж.

    # --- ПОКУПЕЦЬ ---
    last_name = models.CharField("Прізвище", max_length=80)
    first_name = models.CharField("Ім'я", max_length=80)
    phone = models.CharField("Телефон", max_length=20, db_index=True)
    # нормалізований +380XXXXXXXXX — нормалізацію робить checkout-сервіс, не модель
    email = models.EmailField("Email", blank=True)
    # ⚠️ blank=True — свідомий дефолт (OPEN_QUESTIONS Q-ORD-1). Але для payment_method
    #   online/installment email ОБОВ'ЯЗКОВИЙ: без нього немає куди слати квитанцію і
    #   немає ідентифікатора платника. Перевірка — і в clean(), і в CheckConstraint нижче.
    comment = models.TextField("Коментар покупця", blank=True)

    # --- ДОСТАВКА ---
    delivery_method = models.CharField(
        "Спосіб доставки", max_length=14, choices=DeliveryMethod.choices
    )
    np_area_ref = models.CharField("НП: область (ref)", max_length=36, blank=True)
    np_area_name = models.CharField("НП: область", max_length=120, blank=True)
    np_city_ref = models.CharField("НП: місто (ref)", max_length=36, blank=True)
    # канонічний DeliveryCity ref — саме він іде в API Нової Пошти
    np_settlement_ref = models.CharField("НП: населений пункт (ref)", max_length=36, blank=True)
    # ⚠️ SettlementRef ≠ CityRef. Це різні довідники НП, плутанина = ТТН не вибивається.
    np_city_name = models.CharField("НП: місто", max_length=200, blank=True)  # готовий Present
    np_warehouse_ref = models.CharField("НП: відділення (ref)", max_length=36, blank=True)
    np_warehouse_name = models.CharField("НП: відділення", max_length=255, blank=True)
    np_service_type = models.CharField("НП: тип послуги", max_length=24, blank=True)
    # WarehouseWarehouse | WarehousePostomat | WarehouseDoors
    delivery_address = models.CharField("Адреса доставки", max_length=255, blank=True)
    pickup_point = models.ForeignKey(
        "cms.PickupPoint",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
        verbose_name="Пункт самовивозу",
    )
    delivery_cost_estimate = models.DecimalField(
        "Орієнтовна вартість доставки", max_digits=10, decimal_places=2, null=True, blank=True
    )
    # NULL — ЛЕГАЛЬНИЙ стан: circuit breaker НП відкритий → «вартість повідомить менеджер».
    # Замовлення все одно має оформитись: недоступність чужого API не має коштувати продажу.
    ttn = models.CharField("ТТН", max_length=20, blank=True, db_index=True)

    # --- ОПЛАТА ---
    payment_method = models.CharField("Спосіб оплати", max_length=12, choices=PaymentMethod.choices)
    payment_status = models.CharField(
        "Статус оплати",
        max_length=12,
        choices=PaymentStatus.choices,
        default=PaymentStatus.NOT_REQUIRED,
        db_index=True,
    )
    paid_at = models.DateTimeField("Оплачено о", null=True, blank=True)

    # --- СУМИ (ЗАВЖДИ перераховуються на сервері з актуальних Product.price) ---
    subtotal = models.DecimalField("Сума товарів", max_digits=12, decimal_places=2)
    discount = models.DecimalField("Знижка", max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField("До сплати", max_digits=12, decimal_places=2)

    manager_note = models.TextField("Примітка менеджера", blank=True)
    utm = models.JSONField("UTM", default=dict, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.CharField("User-Agent", max_length=400, blank=True)

    class Meta:
        verbose_name = "Замовлення"
        verbose_name_plural = "Замовлення"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "-created_at"], name="order_status_idx"),
            models.Index(fields=["payment_status", "-created_at"], name="order_payment_idx"),
        ]
        constraints = [
            # Доставка у відділення/поштомат без ref-ів = замовлення, на яке фізично
            # неможливо вибити ТТН. Ловимо на рівні БД, а не «менеджер потім передзвонить».
            models.CheckConstraint(
                check=~Q(delivery_method__in=["np_warehouse", "np_postomat"])
                | (Q(np_city_ref__gt="") & Q(np_warehouse_ref__gt="")),
                name="order_np_refs_required",
            ),
            models.CheckConstraint(
                check=~Q(payment_method__in=["online", "installment"]) | ~Q(email=""),
                name="order_online_needs_email",
            ),
            models.CheckConstraint(check=Q(total__gte=0), name="order_total_nonneg"),
            models.CheckConstraint(check=Q(subtotal__gte=0), name="order_subtotal_nonneg"),
        ]

    def __str__(self) -> str:
        return f"{self.number} — {self.last_name} {self.first_name}"

    # -- номер ---------------------------------------------------------------

    @staticmethod
    def generate_number(now=None) -> str:
        """
        CMPX-YYMMDD-NNNN з PostgreSQL-послідовності.

        Чому sequence, а не count()+1: nextval() атомарний і НЕ відкочується разом із
        транзакцією, тому два паралельні checkout'и фізично не можуть отримати один номер.
        count()+1 давав гонку, яка вилазила IntegrityError'ом уже ПІСЛЯ створення платежу.
        Дірки в нумерації (від відкочених транзакцій) — прийнятна ціна; дубль номера — ні.

        Потребує послідовності з міграції:  CREATE SEQUENCE IF NOT EXISTS order_number_seq;
        """
        now = now or timezone.localtime()
        with connection.cursor() as cur:
            cur.execute("SELECT nextval('order_number_seq')")
            n = cur.fetchone()[0]
        return f"CMPX-{now:%y%m%d}-{n:04d}"

    # -- інваріанти ----------------------------------------------------------

    def clean(self) -> None:
        """
        Ті самі інваріанти, що й у CheckConstraint — але з людською помилкою у формі.
        CheckConstraint — ДРУГА лінія оборони, не перша.
        """
        if (
            self.payment_method in (self.PaymentMethod.ONLINE, self.PaymentMethod.INSTALLMENT)
            and not self.email
        ):
            raise ValidationError(
                {"email": "Email обов'язковий для онлайн-оплати та оплати частинами"}
            )

        if self.delivery_method in (
            self.DeliveryMethod.NP_WAREHOUSE,
            self.DeliveryMethod.NP_POSTOMAT,
        ) and not (self.np_city_ref and self.np_warehouse_ref):
            raise ValidationError(
                {"np_warehouse_ref": "Для доставки НП потрібні місто і відділення (ref)"}
            )

        # Інваріант «оплата частинами»: доступна, ЛИШЕ якщо ВСІ товари її підтримують.
        # Перевіряється з трьох місць (API-checkout, адмінка, створення Payment) —
        # тут адмінка. Для нового (ще не збереженого) замовлення items недоступні.
        if (
            self.pk
            and self.payment_method == self.PaymentMethod.INSTALLMENT
            and self.items.filter(installment_available=False).exists()
        ):
            raise ValidationError("Оплата частинами доступна лише якщо ВСІ товари її підтримують")


class OrderItem(models.Model):
    """
    Позиція замовлення — СНАПШОТ товару на момент оформлення.

    product — це лише посилання «звідки взялось» (SET_NULL: товар можуть видалити).
    Чек НЕ МАЄ «пливти»: sku / name / price / quantity зберігаються тут копією, тому
    зміна ціни в каталозі завтра не переписує вчорашню історію продажів.
    """

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items", verbose_name="Замовлення"
    )
    product = models.ForeignKey(
        "catalog.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_items",
        verbose_name="Товар",
    )

    # --- СНАПШОТ ---
    sku = models.CharField("Артикул", max_length=64)
    name = models.CharField("Назва", max_length=255)
    price = models.DecimalField("Ціна за одиницю", max_digits=12, decimal_places=2)
    quantity = models.PositiveSmallIntegerField("Кількість", default=1)

    line_total = models.DecimalField("Сума позиції", max_digits=12, decimal_places=2)
    # ⚡ ВІДСТУП ВІД DATA_MODEL §4 (там GeneratedField(expression=F("price") * F("quantity"))).
    #   Рішення проєкту: на PG14 ЗГЕНЕРОВАНИХ КОЛОНОК НЕ ВИКОРИСТОВУЄМО (див. core/models.py).
    #   Заміна дає ТУ САМУ гарантію, тільки іншим механізмом:
    #     * значення рахує save() (нижче) і checkout-сервіс для bulk_create;
    #     * CheckConstraint `oi_line_total_matches` НЕ ДАЄ записати рядок, де
    #       line_total <> price * quantity — тобто «розсинхрон» неможливий і при bulk_create,
    #       і при .update(), і при ручній правці в адмінці. Різниця з GeneratedField лише в
    #       тому, що БД не обчислює значення, а ВІДХИЛЯЄ неправильне — голосно, а не тихо.

    image_url = models.URLField("Фото", max_length=500, blank=True)
    installment_available = models.BooleanField("Доступна оплата частинами", default=False)
    # Снапшот тумблера товару: інваріант «оплата частинами лише якщо ВСІ товари її
    # підтримують» має перевірятись по стану НА МОМЕНТ замовлення, а не по сьогоднішньому.

    # --- ЕФЕКТИВНІ ГАБАРИТИ (те, що РЕАЛЬНО пішло в Нову Пошту) ---
    weight_kg = models.DecimalField(
        "Вага (ефективна)", max_digits=7, decimal_places=3, null=True, blank=True
    )
    volume_m3 = models.DecimalField(
        "Об'єм (ефективний)", max_digits=10, decimal_places=5, null=True, blank=True
    )
    dims_source = models.CharField("Джерело габаритів", max_length=10, blank=True)
    # product | category | default — результат delivery/services/dims.py::effective_dims()
    # (ADR-021). Зберігаємо саме ЕФЕКТИВНІ значення, а не сирі з Product: інакше неможливо
    # звірити рахунок НП із тим, що ми відправили, коли габаритів у товару не було.

    class Meta:
        verbose_name = "Позиція замовлення"
        verbose_name_plural = "Позиції замовлення"
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(check=Q(quantity__gte=1), name="oi_qty_positive"),
            models.CheckConstraint(check=Q(price__gte=0), name="oi_price_nonneg"),
            # Заміна GeneratedField (див. коментар до line_total).
            models.CheckConstraint(
                check=Q(line_total=F("price") * F("quantity")),
                name="oi_line_total_matches",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sku} × {self.quantity}"

    def save(self, *args, **kwargs):
        # Тримає line_total узгодженим при звичайному save(). bulk_create у checkout-сервісі
        # save() не викликає — там значення рахується явно, а БД його перевіряє constraint'ом.
        self.line_total = self.price * self.quantity
        super().save(*args, **kwargs)


class OrderStatusHistory(models.Model):
    """Хто, коли і з якого статусу перевів замовлення. Append-only."""

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="history", verbose_name="Замовлення"
    )
    from_status = models.CharField("Зі статусу", max_length=12, blank=True)
    to_status = models.CharField("У статус", max_length=12)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="order_status_changes",
        verbose_name="Змінив",
    )
    # null=True — статус може міняти не людина, а вебхук платіжки або Celery-задача.
    comment = models.TextField("Коментар", blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Історія статусів"
        verbose_name_plural = "Історія статусів"
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["order", "-created_at"], name="osh_order_idx")]

    def __str__(self) -> str:
        return f"{self.order_id}: {self.from_status} → {self.to_status}"
