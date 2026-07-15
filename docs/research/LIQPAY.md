# LiqPay: умови, комісії, API, оплата частинами

**LiqPay для Complex — повний розбір (станом на липень 2026)**

Джерела: офіційна документація LiqPay (`liqpay.ua/uk/doc/api/...`), сторінка тарифів LiqPay, довідник LiqPay, офіційна сторінка ПриватБанку «Оплата частинами», умови та правила LiqPay. Дані з доків витягнуто з реальних сторінок (частина — з JS-бандлів SPA-документації, бо HTML рендериться клієнтом).

---

## 1. КОМІСІЇ

### 1.1. Базовий еквайринг

| Що | Значення |
|---|---|
| Базова ставка інтернет-еквайрингу | **1,5%** від суми транзакції |
| Зарахування коштів | автоматично, **протягом 1 банківського дня**, за вирахуванням комісії. На рахунок у ПриватБанку — фактично в день продажу; на рахунок в **іншому банку** — **раз на добу** |
| Пільгові категорії | комунальні/ОСББ/ЖКГ, онлайн-кредитування — 1%; благодійність — 0% |
| Індивідуальний тариф | **від 300 000 грн обороту** — подається заявка в чат бізнес-кабінету. Також знижений тариф для категорій, де ринкова ставка нижча |
| Хто платить | **тільки магазин**. Surcharge (перекладання комісії на покупця, «+2% за оплату карткою») **прямо заборонений** умовами LiqPay. Це важливо: ціна на сайті = ціна на чекауті, ніяких надбавок |

Побутова техніка — це високочековий, низькоризиковий сегмент, тому 1,5% реально збивається до ~1,2–1,4% після виходу на обороти. Закладай у фін-модель **1,5%** і торгуйся після 2–3 місяців статистики.

Зауваження щодо цифр у медіа: гуляють числа 2,2% і 2,75%. 2,75% — це стара/загальна ставка з «Умов та Правил» для окремих каналів, 2,2% — маркетингова стаття 2021 року. Актуальний офіційний тариф на сторінці `liqpay.ua/tariffs` — **1,5%**.

### 1.2. Оплата частинами і Миттєва розстрочка — окремі гроші

Це **два різні сервіси** ПриватБанку, обидва доступні через той самий чекаут LiqPay:

**«Оплата частинами» (paypart)** — покупець платить фактично 0% (0,01%/міс, реальна річна 0,24%), **комісію за кредит платить МАГАЗИН**, і вона **додається зверху до комісії еквайрингу**.

Офіційна тарифна сітка ПриватБанку (діє з 14.11.2025):

| Платежів | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Комісія магазину, %** | 2,3 | 2,5 | 3,6 | 5,3 | 6,5 | 7,7 | 8,8 | 9,9 | 11,2 | 12,5 | 13,7 | 14,8 |

| Платежів | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Комісія магазину, %** | 16 | 17 | 18,1 | 19,1 | 20,1 | 21,1 | 22,3 | 23,2 | 24,3 | 25,3 | 26,3 | 27,3 |

**Це комісія за сервіс + комісія еквайрингу зверху** (пряма цитата довідника LiqPay: «комісія за сервісом залежно від кількості платежів + комісія еквайрингу»).

Тобто холодильник за 30 000 грн на **6 платежів** коштує магазину: 6,5% + 1,5% ≈ **8%** = 2 400 грн. Це зʼїдає всю маржу, якщо вона <10%. **Практичний висновок для Complex:** дозволяти ОЧ максимум на 2–4 платежі (2,3–3,6% — це прийнятно), а 6+ платежів — тільки на позиціях з високою маржею або як промо. Бейдж «6 платежів» на референсі galiton — це саме те, за що магазин платить 6,5%.

**«Миттєва розстрочка» (moment_part)** — **для магазину БЕЗКОШТОВНО**, платиш тільки еквайринг (1,5%). Відсотки платить покупець: **1,9%/міс** (реальна річна ~52,5%). Строк обирає сам покупець на сторінці оплати.

| | Оплата частинами | Миттєва розстрочка |
|---|---|---|
| Комісія магазину | 2,3–27,3% (за таблицею) **+ еквайринг** | **0** + еквайринг |
| Ставка для покупця | 0,01%/міс | 1,9%/міс |
| Строк | 1–24 міс (**встановлює магазин**) | 1–24 міс (**обирає покупець**) |
| Кількість платежів | 2–25 | 2–25 |
| Сума | 300 – 300 000 грн (LiqPay-довідник) / до 500 000 грн індивідуально (ПБ) | те саме |
| Гроші магазину | у день продажу (онлайн — протягом 2 годин) | у день продажу |
| Хто може купити | тільки клієнти ПриватБанку з відкритим кредитним лімітом і карткою «Універсальна» | те саме |

Підключення обох сервісів — **безкоштовне**, ~15 хвилин, меню **«Кредити»** в бізнес-кабінеті LiqPay. Підключаються обидва одразу; який саме застосується — визначає покупець, обираючи метод на чекауті.

---

## 2. ПІДКЛЮЧЕННЯ

- **ФОП або ТОВ** — обовʼязково. На картку фізособи приймати платежі **не можна**.
- **Рахунок саме в ПриватБанку — НЕ обовʼязковий** для еквайрингу. Можна вивести на IBAN ФОП/ТОВ у **будь-якому банку України**, але з обмеженнями:
  - зарахування **раз на добу** (замість «протягом дня»);
  - **повернення коштів покупцям здійснюються за рахунок майбутніх платежів** (тобто якщо оборот впав, а треба зробити рефанд — буде проблема).
- **АЛЕ: ПРРО від LiqPay доступний ТІЛЬКИ мерчантам, які приймають платежі на рахунок ПриватБанку.** Див. розділ 9. Це, а також швидші виплати й простіші рефанди — вагомий аргумент відкрити рахунок ФОП/ТОВ саме в ПБ.
- **Документи:**
  - ФОП: паспорт + РНОКПП власника (фінансовий номер телефону власника компанії).
  - ТОВ: паспорт+РНОКПП представника, статут (або код з ЦНАП), засновницький договір, документи про повноваження розпоряджатися рахунком (протокол/наказ, підпис + печатка).
- **Терміни:** реєстрація до 15 хв, **верифікація магазину — до 24 годин**. Якщо рахунку в ПБ немає — треба одноразово зайти у відділення для уточнення даних і підпису договору.
- **Вимоги до сайту (їх перевіряють при верифікації — заклади в MVP):** опис товарів з цінами і фото, контакти продавця (назва ФОП/ТОВ, телефон, e-mail, адреса), публічна оферта/угода користувача, **умови повернення коштів і доставки**. Часто просять документи від постачальників (договір або 1–2 інвойси на суму >5 000 грн) — у нас це є.
- Після реєстрації автоматично створюються дві пари ключів: **бойові** (`public_key` = `i…`, `private_key`) і **тестові** (обидва з префіксом `sandbox_`).

---

## 3. API: Checkout

### 3.1. Механіка

Все LiqPay API — це два поля:

```
data      = base64( json_utf8(params) )
signature = base64( sha1_binary( private_key + data + private_key ) )
```

**Критично:** `sha1` береться від **бінарного digest**, не від hex. І `private_key` конкатенується **з обох боків** саме `data` (base64-рядка), а не JSON.

Два ендпоінти:

| Ціль | URL | Метод |
|---|---|---|
| Чекаут (редірект покупця) | `https://www.liqpay.ua/api/3/checkout` | HTML-форма POST (`data`, `signature`) |
| Server-to-server (status, refund, hold_completion, unsubscribe…) | `https://www.liqpay.ua/api/request` | POST form-urlencoded (`data`, `signature`) |

### 3.2. Параметри Checkout

**Обовʼязкові:**

| Параметр | Тип | Опис |
|---|---|---|
| `version` | Number | `3` (в офіційному прикладі Checkout і в URL `/api/3/`). У таблиці параметрів LiqPay поле version описане як «поточне значення 7» — це стосується новіших методів/формату відповіді. **Перевір на пісочниці 3 vs 7**, у продакшн бери те, що віддає повний callback. Наш код має тримати версію в конфізі. |
| `public_key` | String | ключ магазину |
| `action` | String | `pay` \| `hold` \| `subscribe` \| `paydonate` |
| `amount` | Number | напр. `5`, `7.34` |
| `currency` | String | `UAH` \| `USD` \| `EUR` |
| `description` | String | опис платежу (видно покупцю) |
| `order_id` | String | наш ID, до 255 символів, **унікальний** |

**Опціональні, які реально треба нам:**

| Параметр | Опис |
|---|---|
| `paytypes` | Через кому: `card`, `apay`, `gpay`, `privat24`, `qr`, `cash`, `invoice`, **`paypart`** (оплата частинами), **`moment_part`** (миттєва розстрочка). Якщо не передати — застосуються налаштування магазину з вкладки Checkout. **Це наш головний важіль для тумблера розстрочки.** |
| `server_url` | Наш вебхук (server→server). До 510 символів. **Має бути публічний HTTPS.** |
| `result_url` | Куди редіректить покупця після оплати. До 510 символів. **Це НЕ підтвердження оплати** — лише UX. |
| `language` | `uk` \| `en` (`ru` теж підтримується) |
| `expired_date` | UTC, формат `2026-07-13 23:59:00` — час, до якого можна оплатити. Ставимо для «оплата протягом 24 год». |
| `sandbox` | `1` — тестовий платіж без списання |
| `rro_info` | Object — дані для фіскалізації (див. розділ 9) |
| `info` | довільний рядок (кладемо туди наш внутрішній контекст) |
| `product_name` / `product_category` / `product_description` / `product_url` | 100 / 25 / 500 / 2000 символів |
| `customer` | ID покупця в нас (для 1-click) |
| `split_rules` | розщеплення на кількох отримувачів (нам не треба) |

### 3.3. Тонкий клієнт на Python (httpx) — те, що піде в модуль

```python
# payments/liqpay/client.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx

CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"
API_URL = "https://www.liqpay.ua/api/request"
API_VERSION = 3


class LiqPaySignatureError(Exception):
    pass


class LiqPayClient:
    def __init__(self, public_key: str, private_key: str, *, sandbox: bool = False,
                 timeout: float = 15.0) -> None:
        self.public_key = public_key
        self._private_key = private_key.encode("utf-8")
        self.sandbox = sandbox
        self._timeout = timeout

    # ---------- підпис ----------
    def encode_data(self, params: dict[str, Any]) -> str:
        payload = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")

    def make_signature(self, data: str) -> str:
        raw = self._private_key + data.encode("ascii") + self._private_key
        return base64.b64encode(hashlib.sha1(raw).digest()).decode("ascii")

    def verify_signature(self, data: str, signature: str) -> bool:
        # constant-time — щоб не було timing-oracle
        return hmac.compare_digest(self.make_signature(data), signature)

    @staticmethod
    def decode_data(data: str) -> dict[str, Any]:
        return json.loads(base64.b64decode(data).decode("utf-8"))

    # ---------- checkout ----------
    def build_checkout(self, params: dict[str, Any]) -> dict[str, str]:
        """Повертає {'url', 'data', 'signature'} — фронт робить POST-форму або редірект."""
        body = {
            "version": API_VERSION,
            "public_key": self.public_key,
            **params,
        }
        if self.sandbox:
            body["sandbox"] = 1
        data = self.encode_data(body)
        return {"url": CHECKOUT_URL, "data": data, "signature": self.make_signature(data)}

    # ---------- server-to-server ----------
    def api(self, params: dict[str, Any]) -> dict[str, Any]:
        body = {"version": API_VERSION, "public_key": self.public_key, **params}
        data = self.encode_data(body)
        resp = httpx.post(
            API_URL,
            data={"data": data, "signature": self.make_signature(data)},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def status(self, order_id: str) -> dict[str, Any]:
        return self.api({"action": "status", "order_id": order_id})

    def refund(self, order_id: str, amount: Decimal) -> dict[str, Any]:
        return self.api({"action": "refund", "order_id": order_id, "amount": float(amount)})
```

Приклад формування чекауту із замовлення:

```python
# payments/liqpay/checkout.py
from decimal import Decimal

def checkout_params_for_order(order, *, allow_installments: bool) -> dict:
    paytypes = ["card", "privat24", "apay", "gpay"]
    if allow_installments:
        # paypart  — комісію за кредит платить магазин (2,3%+)
        # moment_part — комісію платить покупець, магазину безкоштовно
        paytypes += ["paypart", "moment_part"]

    return {
        "action": "pay",
        "amount": float(order.total),          # LiqPay хоче число, не рядок
        "currency": "UAH",
        "description": f"Замовлення №{order.number} — Complex",
        "order_id": str(order.payment_reference),   # UUID, НЕ order.id
        "paytypes": ",".join(paytypes),
        "language": "uk",
        "result_url": f"https://complex.ua/order/{order.number}/thanks",
        "server_url": "https://complex.ua/api/payments/liqpay/callback",
        "expired_date": order.payment_expires_at.strftime("%Y-%m-%d %H:%M:%S"),  # UTC
        "product_category": order.primary_category_slug[:25],
        "product_name": order.first_item_name[:100],
    }
```

**`order_id` — окреме поле `payment_reference` (UUID), а не PK замовлення.** Причини: LiqPay привʼязує статус/рефанд до `order_id`; якщо покупець скасував і платить вдруге, треба новий `order_id`, інакше LiqPay поверне старий платіж. Роби `Payment` як окрему модель: одне `Order` → багато `Payment` (кожна спроба зі своїм `payment_reference`).

### 3.4. Способи інтеграції

1. **Redirect (форма POST на `/api/3/checkout`)** — найпростіше, найнадійніше. Бекенд віддає `{url, data, signature}`, фронт (Next.js) будує приховану форму й сабмітить. **Рекомендую саме це для MVP.**
2. **Widget** — `<script src="//static.liqpay.ua/libjs/checkout.js">`, далі `LiqPayCheckout.init({data, signature, embedTo: "#liqpay", mode: "popup" | "embed", language: "uk"}).on("liqpay.callback", fn).on("liqpay.ready", fn).on("liqpay.close", fn)`. Покупець не йде з сайту. Мінус: `liqpay.callback` у браузері — **не є доказом оплати**, довіряти можна лише server_url-вебхуку.
3. **Server-to-server (`card_payment`, action=pay з `card`, `card_exp_month`, `card_cvv`)** — вимагає **PCI DSS**, бо номер картки проходить через наш сервер. **Категорично не робимо.**

Для Next.js: кнопка «Оплатити» → `POST /api/orders/{id}/pay` на Django → отримуємо `{url, data, signature}` → авто-сабміт форми. Widget можна додати пізніше як прогресивне покращення.

---

## 4. CALLBACK

### 4.1. Як приходить

`POST` на `server_url`, `Content-Type: application/x-www-form-urlencoded`, два поля: **`data`** (base64 JSON) і **`signature`**.

Перевірка: `base64(sha1(private_key + data + private_key)) == signature`. Порівнювати **constant-time**. Якщо не збіглось — 400 і в лог (можлива атака).

**Дуже важливо:** дані беремо **тільки з розшифрованого `data`**, ніколи з query/GET. І `amount`/`currency` з callback **звіряємо з нашим замовленням** — якщо не збігаються, не проводимо (захист від підміни суми).

### 4.2. Статуси і що з ними робити

**Фінальні:**

| Статус | Що це | Дія в Complex |
|---|---|---|
| `success` | оплата успішна | `Payment.PAID`, `Order.PAID` → резерв товару, лист покупцю, фіскальний чек |
| `failure` | неуспішна | `Payment.FAILED`, замовлення лишається `PENDING`, показуємо «спробувати ще» |
| `error` | неуспішна, некоректні дані | так само як `failure`, але ALERT у Sentry — це наш баг у параметрах |
| `reversed` | кошти повернуто | `Payment.REFUNDED`, `Order.REFUNDED`, повертаємо товар на склад |
| `subscribed` / `unsubscribed` | підписка створена/скасована | нам не треба (немає підписок) |
| `sandbox` | **успішний тестовий платіж** | у DEV/STAGING трактуємо як `success`; **у проді — ALERT і НЕ віддаємо товар** (означає, що десь витік sandbox-ключ або `sandbox:1`) |

**Проміжні — чекаємо далі, замовлення НЕ виконуємо:**

`processing` (обробляється), `prepared` (створено, чекає завершення), `hold_wait` (кошти заблоковані — при `action=hold`; треба окремо `hold_completion`), `wait_secure` (платіж на перевірці — може висіти годинами), `wait_accept` (**кошти з покупця списані, але магазин на перевірці — гроші прийдуть після верифікації**; замовлення варто прийняти в роботу, але позначити як «очікує підтвердження банку»), `wait_card` (у отримувача не вказано спосіб повернення), `wait_compensation` (успішний, зарахується в добовій виплаті — фактично можна віддавати товар), `wait_lc` (акредитив), `wait_reserve` (кошти зарезервовано під повернення), `cash_wait` (чекає оплату готівкою в терміналі), `invoice_wait` (рахунок виставлено, чекає оплати).

**Верифікаційні (покупець ще щось підтверджує) — просто чекаємо:**
`3ds_verify`, `otp_verify`, `cvv_verify`, `captcha_verify`, `ivr_verify`, `password_verify`, `phone_verify`, `pin_verify`, `sender_verify`, `receiver_verify`, `senderapp_verify`, `wait_qr`, `wait_sender`.

**Практичне правило для нашого модуля:** мапимо статуси у 4 внутрішні: `PAID` (`success`, `wait_compensation`, `sandbox`+debug), `PENDING` (усе проміжне/верифікаційне), `FAILED` (`failure`, `error`), `REFUNDED` (`reversed`). Плюс окремий `HELD` для `hold_wait` і прапорець `needs_bank_review` для `wait_accept`/`wait_secure`.

Корисні поля callback: `payment_id`, `order_id`, `liqpay_order_id`, `status`, `amount`, `currency`, `paytype`, `sender_card_mask2`, `sender_card_bank`, `receiver_commission` (**наша фактична комісія — пиши в БД, це джерело правди по юніт-економіці**), `moment_part` (bool — «ознака оплати частинами»), `err_code`, `err_description`, `create_date`, `end_date`.

### 4.3. Ідемпотентність

LiqPay **може прислати той самий callback кілька разів** (ретраї) і **не гарантує порядок**. Схема:

```python
# payments/models.py (скорочено)
class Payment(models.Model):
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    reference = models.UUIDField(default=uuid4, unique=True)      # -> liqpay order_id
    provider = models.CharField(max_length=32, default="liqpay")
    status = models.CharField(max_length=32, default="created")   # наш enum
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="UAH")
    liqpay_payment_id = models.BigIntegerField(null=True, blank=True)
    receiver_commission = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    paytype = models.CharField(max_length=32, blank=True)
    last_end_date = models.BigIntegerField(null=True)             # для захисту від out-of-order
    raw_last_callback = models.JSONField(null=True)

class PaymentCallback(models.Model):
    """Сирий лог + ключ ідемпотентності."""
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="callbacks")
    fingerprint = models.CharField(max_length=64, unique=True)    # sha256(data)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
```

```python
# payments/views.py
import hashlib
import logging
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

log = logging.getLogger(__name__)

TERMINAL = {"success", "failure", "error", "reversed", "sandbox"}


@csrf_exempt
@require_POST
def liqpay_callback(request):
    data = request.POST.get("data", "")
    signature = request.POST.get("signature", "")
    client = get_liqpay_client()

    if not data or not client.verify_signature(data, signature):
        log.warning("liqpay: bad signature", extra={"ip": request.META.get("REMOTE_ADDR")})
        return HttpResponseBadRequest("bad signature")

    payload = client.decode_data(data)
    fingerprint = hashlib.sha256(data.encode()).hexdigest()

    with transaction.atomic():
        payment = (
            Payment.objects.select_for_update()
            .filter(reference=payload["order_id"])
            .first()
        )
        if payment is None:
            log.error("liqpay: unknown order_id %s", payload.get("order_id"))
            return HttpResponse("ok")          # 200, щоб LiqPay не довбав ретраями

        # 1. дедуп: точно такий самий callback уже оброблено
        _, created = PaymentCallback.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={"payment": payment, "payload": payload},
        )
        if not created:
            return HttpResponse("ok")

        # 2. захист від підміни суми
        if Decimal(str(payload["amount"])) != payment.amount or payload["currency"] != payment.currency:
            log.critical("liqpay: amount mismatch on %s", payment.reference)
            return HttpResponse("ok")

        # 3. захист від out-of-order: старіший callback не перетирає новіший
        end_date = payload.get("end_date") or payload.get("create_date") or 0
        if payment.last_end_date and end_date < payment.last_end_date:
            return HttpResponse("ok")

        # 4. фінальний статус не відкочується назад (крім success -> reversed)
        if payment.status in {"paid"} and payload["status"] not in {"reversed"}:
            return HttpResponse("ok")

        apply_liqpay_status(payment, payload, end_date)   # мапінг + перехід стану

    return HttpResponse("ok")   # LiqPay має отримати 200, інакше ретраїтиме
```

Побічні ефекти (лист, резерв складу, фіскальний чек) — **виносимо в Celery-таск, який запускається через `transaction.on_commit`**, і сам таск теж ідемпотентний (`Order.paid_notified_at`, `Order.fiscalized_at`).

---

## 5. ЗВІРКА (Status API) — обовʼязково

Так, метод є. Це **страховка від того, що вебхук не дійшов** (наш сервер лежав, Caddy перезапускався, тайм-аут).

```
POST https://www.liqpay.ua/api/request
data      = base64({"action":"status","version":3,"public_key":"i…","order_id":"…"})
signature = base64(sha1(private + data + private))
```

Відповідь — JSON з тими самими полями, що й callback: `status`, `payment_id`, `amount`, `paytype`, `receiver_commission`, `moment_part`, `err_code`, `end_date` тощо.

```python
# payments/tasks.py
from celery import shared_task
from datetime import timedelta
from django.utils import timezone

PENDING = ("created", "pending", "processing", "held")

@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def reconcile_liqpay_payments(self):
    """Кожні 5 хв: добираємо статуси, яких не принесли вебхуки."""
    cutoff = timezone.now() - timedelta(minutes=3)      # даємо вебхуку фору
    stale = Payment.objects.filter(
        provider="liqpay",
        status__in=PENDING,
        created_at__lt=cutoff,
        created_at__gt=timezone.now() - timedelta(days=3),
    )[:200]

    client = get_liqpay_client()
    for payment in stale:
        try:
            payload = client.status(str(payment.reference))
        except httpx.HTTPError as exc:
            log.warning("liqpay status failed for %s: %s", payment.reference, exc)
            continue
        apply_liqpay_status_locked(payment, payload)     # той самий код, що й у callback
```

У beat: `reconcile_liqpay_payments` — кожні 5 хвилин. Плюс щоденний таск, що тягне **реєстр платежів** (`action=reports`, `/uk/doc/api/information/register`) і звіряє суми та `receiver_commission` з нашою БД — це закриває бухгалтерію.

**Правило модуля: єдина функція `apply_liqpay_status()` викликається і з вебхука, і зі звірки.** Ніякого дубльованого мапінгу.

---

## 6. ОПЛАТА ЧАСТИНАМИ — як вмикати і як зробити тумблер на товарі

### Як вмикається
1. У бізнес-кабінеті LiqPay, меню **«Кредити»** — підключаються обидва сервіси одразу (ОЧ + МР). Безкоштовно, ~15 хв.
2. У запиті чекауту передаємо `paytypes` з `paypart` та/або `moment_part`.

```python
paytypes = "card,privat24,apay,gpay,paypart,moment_part"   # з розстрочкою
paytypes = "card,privat24,apay,gpay"                        # без розстрочки
```

Якщо `paytypes` **не передати** — LiqPay покаже те, що ввімкнено в налаштуваннях магазину (вкладка Checkout). Тобто **щоб керувати розстрочкою по-товарно, `paytypes` треба передавати ЗАВЖДИ явно**.

### Обмеження, які треба знати перед тим, як обіцяти замовнику
- **Кількість платежів (2–25) обирає ПОКУПЕЦЬ на сторінці LiqPay.** У Checkout API **немає параметра «максимум N платежів»**. ПриватБанк це прямо підтверджує у FAQ: *«Спеціального налаштування в кабінеті „Інтернет Оплата частинами“ не передбачено. Кількість платежів визначається індивідуально під час формування кожної угоди»* (індивідуальна кількість задається лише при ручному виставленні рахунку в кабінеті або через окремий API «Інтернет Оплата частинами» з `storeId`/`password`, який працює **не через LiqPay Checkout**).
  → **Наслідок:** бейдж «6 платежів» на картці товару у нас буде **маркетинговим**, а не технічним обмеженням. Якщо покупець вибере 12 платежів — магазин заплатить 13,7%. **Обовʼязково попередити замовника.** Якщо йому критично жорстко обмежити N — це друга інтеграція (прямий API «Оплата частинами» ПБ з `merchantType: PP` / `II`), і це не MVP.
- Сума: **300 – 300 000 грн** (LiqPay), тільки UAH. Товари дешевші за 300 грн — розстрочка недоступна фізично.
- Тільки клієнти ПриватБанку з відкритим кредитним лімітом і карткою «Універсальна».
- Категорії: побутова техніка та електроніка — **дозволена, прямо в списку** («Магазини електроніки та побутової техніки»). Проблем не буде.
- Забороняється завищувати ціну для покупок в розстрочку відносно звичайної оплати.

### Модель даних під тумблер

```python
# catalog/models.py
class Product(models.Model):
    ...
    installments_enabled = models.BooleanField(
        default=False,
        verbose_name="Оплата частинами доступна",
        help_text="Комісію за «Оплату частинами» (2,3–27,3% залежно від к-сті платежів) "
                  "платить магазин. Вмикати лише для товарів з достатньою маржею.",
    )
    installments_max_payments = models.PositiveSmallIntegerField(
        default=6,
        validators=[MinValueValidator(2), MaxValueValidator(25)],
        verbose_name="Платежів (для бейджа)",
        help_text="УВАГА: LiqPay не обмежує к-сть платежів технічно — покупець може обрати більше. "
                  "Це значення тільки для бейджа на картці товару.",
    )
```

Глобальний дефолт — у `SiteSettings` (напр. `installments_default_enabled`, `installments_min_price`), щоб не клацати 3000 товарів руками; на товарі — override.

### Правило «доступно тільки якщо ВСІ товари в кошику підтримують»

```python
# orders/services.py
from decimal import Decimal

INSTALLMENTS_MIN = Decimal("300")
INSTALLMENTS_MAX = Decimal("300000")


def cart_allows_installments(cart) -> bool:
    """Розстрочка доступна лише якщо КОЖНА позиція її підтримує + сума в межах ліміту."""
    if not cart.items.exists():
        return False
    if not (INSTALLMENTS_MIN <= cart.total <= INSTALLMENTS_MAX):
        return False
    if cart.currency != "UAH":
        return False
    return all(item.product.installments_enabled for item in cart.items.all())


def cart_installments_badge(cart) -> int | None:
    """Скільки платежів показувати в кошику: мінімум по всіх товарах (найконсервативніше)."""
    if not cart_allows_installments(cart):
        return None
    return min(item.product.installments_max_payments for item in cart.items.all())
```

Це значення `cart_allows_installments()` віддаємо в API кошика (`allow_installments: bool` + `installments_payments: int | null`), фронт малює/ховає блок, а бекенд **незалежно перераховує його ще раз при створенні чекауту** — фронту не довіряємо:

```python
params = checkout_params_for_order(order, allow_installments=cart_allows_installments(order))
```

На картці товару — той самий бейдж (лого ПБ + `installments_max_payments`), у кошику — перерахований мінімум. Якщо покупець додає товар без розстрочки — бейдж у кошику зникає, і `paypart`/`moment_part` не потрапляють у `paytypes`.

---

## 7. SANDBOX

Два способи, працюють обидва:

1. **Sandbox-ключі:** окрема пара `sandbox_i…` / `sandbox_…` з бізнес-кабінету. Все, що через них проходить, — тест.
2. **Параметр `sandbox: 1`** у `data` при бойових ключах. Реального списання немає.

Усі тестові платежі приходять у callback зі **статусом `sandbox`** (а не `success`). Наш мапер має це врахувати (див. розділ 4.2 — і обовʼязково алерт, якщо `sandbox` прилетів у проді).

**Тестові картки** (CVV — будь-які 3 цифри, термін — будь-яка майбутня дата `12/30`):

| Картка | Сценарій |
|---|---|
| `4242 4242 4242 4242` | успішна оплата |
| `4000 0000 0000 3063` | успішна оплата |
| `4000 0000 0000 3089` | успішна з 3DS |
| `4000 0000 0000 3055` | успішна з OTP |
| `4000 0000 0000 0002` | успішна з CVV-перевіркою |
| `4000 0000 0000 9995` | неуспішна (помилка ліміту) |

**Проблема з локальною розробкою:** `server_url` має бути публічним HTTPS. Рішення — `cloudflared tunnel` / `ngrok` на dev, а в docker-compose staging — реальний субдомен через Caddy. Плюс менеджмент-команда `python manage.py liqpay_simulate_callback --order <ref> --status success`, яка формує валідно підписаний callback і бʼє в наш власний вʼю — щоб тестити ідемпотентність без інтернету.

Розстрочку в sandbox повноцінно **не протестуєш** (потрібен реальний кредитний ліміт ПБ) — перевіряти доведеться реальним платежем на мінімальну суму (300 грн) з бойовими ключами і потім робити refund.

---

## 8. PYTHON-БІБЛІОТЕКИ

**Офіційний SDK (`github.com/liqpay/sdk-python`) — мертвий і непридатний.** Я його прочитав: у ньому `from urlparse import urljoin`, `unicode`, `basestring`, `params.iteritems()` — це **Python 2.7**. Останній коміт — правка README. Плюс там `requests.post(..., verify=False)` (!) — вимкнена перевірка TLS-сертифіката. Використовувати не можна.

Є неофіційний `liqpay-sdk-python3` (PyPI, 1.0.6) — це форк того самого коду. Він живий, але тонкий і без типів.

**Рекомендація: пишемо свій клієнт на `httpx`** (~80 рядків, див. розділ 3.3). Аргументи:
- вся «бібліотека» — це три функції: `b64(json)`, `b64(sha1(k+d+k))`, `POST`;
- нам потрібні async (Django Ninja) і типізація;
- нам потрібен `verify_signature` з `hmac.compare_digest`, чого в SDK немає;
- нам однаково треба своя абстракція провайдера (`PaymentProvider` ABC), бо в ARCHITECTURE.md закладено абстракцію.

Структура модуля:

```
payments/
  __init__.py
  models.py            # Payment, PaymentCallback, Refund
  providers/
    base.py            # PaymentProvider (ABC): create_checkout, fetch_status, refund, parse_callback
    liqpay/
      client.py        # LiqPayClient (httpx, підпис)
      provider.py      # LiqPayProvider(PaymentProvider)
      statuses.py      # LIQPAY_STATUS_MAP -> наш enum
      checkout.py      # побудова params з Order
  services.py          # apply_status() — єдина точка переходу станів
  api.py               # Ninja: POST /orders/{id}/pay, POST /liqpay/callback
  tasks.py             # reconcile_liqpay_payments, fiscalize_order
  admin.py             # unfold: Payment з raw payload, кнопка «Перевірити статус», «Повернути»
```

---

## 9. ПРРО І ФІСКАЛІЗАЦІЯ

**Так, фіскальний чек обовʼязковий.** При оплаті карткою онлайн за товар, що передається фізично, ФОП груп 2–4 та юрособи **зобовʼязані видати фіскальний чек** — еквайринг (LiqPay/Monopay/Portmone) сам по собі це не закриває. Ігнорувати не можна: штрафи 100% суми першої непробитої операції і 150% за кожну наступну.

**Хороша новина: LiqPay має власний ПРРО, і він БЕЗКОШТОВНИЙ, і фіскалізація вбудована прямо в Checkout API.**

**Умови:**
- **Тільки для мерчантів, які приймають платежі на рахунок у ПриватБанку.** Це головний аргумент відкрити рахунок у ПБ, а не в іншому банку.
- Потрібен **SmartID** (у застосунку ПриватБанку) для підпису документів при реєстрації ПРРО.
- ПРРО і касирів треба **зареєструвати в ДПС** (подається онлайн через кабінет). Є «навчальна зміна» для тестів.
- Фіскалізуються **лише гривневі** платежі. Валютні — ні (нам не проблема, продаємо в UAH).
- Повернення робиться через фіскальний журнал у кабінеті (чек повернення).

**Як це виглядає в API** — просто додаємо `rro_info` до `data` чекауту:

```python
def build_rro_info(order) -> dict:
    """Мінімальний варіант — LiqPay сам робить фіскальний чек і шле його покупцю."""
    return {
        "items": [
            {
                "amount": float(item.qty),                # кількість
                "price": float(item.unit_price),          # ціна за одиницю
                "cost": float(item.line_total),           # сума по позиції
                "name": item.product_name[:255],
                "barcode": item.product.sku,              # артикул з прайсу
                "unitcode": "2009",                       # штука (див. довідник invoice_units)
                "taxs": [                                 # для ФОП на єдиному податку без ПДВ
                    {"name": "Без ПДВ", "letter": "А", "prc": 0, "type": 0}
                ],
            }
            for item in order.items.all()
        ],
        "delivery_emails": [order.customer_email],
    }

# і в checkout_params_for_order:
params["rro_info"] = build_rro_info(order)
```

Повний формат `rro_info` з офіційних доків:

```json
"rro_info": {
  "items": [
    {
      "amount": 2,
      "price": 100,
      "cost": 200,
      "barcode": "123123",
      "name": "Шкарпетки",
      "categoryname": "ПРРО",
      "codifier": 23193212343202,
      "vndcode": "000033322",
      "unitcode": "0101",
      "taxs": [ { "name": "Без ПДВ 0%", "letter": "А", "prc": 0, "type": 0 } ]
    }
  ],
  "delivery_emails": ["email1@email.com", "email2@email.com"]
}
```

`unitcode` — код одиниці виміру з довідника ДПС (`0000` — послуга, `0101` — метр, для штук — код з довідника `invoice_units`; уточнити при налаштуванні). Сума всіх `cost` **має дорівнювати `amount` платежу** — інакше фіскалізація впаде. Тому округлення цін (питання №5 з INPUTS) треба закрити **до** релізу платіжного модуля: якщо ми округлюємо підсумок, а не позиції, `sum(cost) != amount` і чек не проб'ється.

**Чи потрібен Checkbox / Вчасно.Каса?**

| Сценарій | Рішення |
|---|---|
| Продаємо **тільки** онлайн-оплату карткою через LiqPay, рахунок у ПриватБанку | **LiqPay ПРРО достатньо.** Checkbox не потрібен. Безкоштовно. |
| Рахунок ФОП/ТОВ **не в ПриватБанку** | LiqPay ПРРО **недоступний** → треба **Checkbox** (~300–600 грн/міс за касу) або Вчасно.Каса, або безкоштовний ПРРО від ДПС (незручний) |
| Є **накладений платіж Новою Поштою** / оплата при отриманні / готівка при самовивозі | LiqPay ПРРО ці чеки **не закриє** — це не його транзакції. Треба **окремий ПРРО (Checkbox)** з інтеграцією, який пробʼє чек за операцією НП / готівкою |

**Це критично для Complex:** у ТЗ є доставка Новою Поштою. Якщо буде **накладений платіж** (а він буде — це Україна, побутова техніка), то **LiqPay ПРРО не вирішує задачу повністю** і Checkbox однаково знадобиться. У такому разі логічніше **одразу поставити Checkbox як єдиний ПРРО на всі типи оплат** (карта через LiqPay + НП накладений + готівка), ніж тримати два фіскальні контури.

**Рекомендація:**
1. Уточнити в замовника: **чи буде накладений платіж/оплата при отриманні**.
2. Якщо **ні** (тільки передоплата карткою) → рахунок у ПриватБанку + LiqPay ПРРО через `rro_info`. Нуль додаткових витрат, нуль додаткових інтеграцій.
3. Якщо **так** → закладаємо **Checkbox** як окремий сервіс фіскалізації (у нас уже є абстракція, робимо `fiscal/` модуль з провайдером), а `rro_info` в LiqPay **не передаємо** (щоб не було подвійних чеків).

Архітектурно: `fiscal/providers/{liqpay_rro, checkbox}.py` за тим самим патерном, що й платежі. У моделі `Order` — `fiscal_receipt_id`, `fiscal_receipt_url`, `fiscalized_at`. Таск `fiscalize_order` — ідемпотентний, з ретраями.

---

## Що треба уточнити в замовника (блокери платіжного модуля)

1. **Накладений платіж / оплата при отриманні буде?** → визначає, чи потрібен Checkbox (див. розділ 9). **Найважливіше питання.**
2. **Рахунок ФОП/ТОВ — де відкриємо?** Рекомендую **ПриватБанк**: безкоштовний ПРРО, гроші в день продажу, нормальні рефанди. Інший банк = мінус ПРРО, зарахування раз на добу, рефанди «за рахунок майбутніх платежів».
3. **Оплата частинами — на які товари і на скільки платежів?** Показати замовнику таблицю комісій (2,3% за 2 платежі vs 13,7% за 12) і пояснити, що **кількість платежів обирає покупець, а не ми**. Можливо, він захоче тільки «Миттєву розстрочку» (для магазину безкоштовна, відсотки платить покупець) — тоді в `paytypes` кладемо лише `moment_part`, без `paypart`.
4. **Округлення цін після USD→UAH** — має бути по-позиційне, інакше `sum(rro_info.items[].cost) != amount` і фіскалізація впаде.

---

## Sources

- [LiqPay — Тарифи](https://www.liqpay.ua/tariffs)
- [LiqPay — Умови та Правила надання послуг](https://www.liqpay.ua/information/terms)
- [LiqPay — Checkout API](https://www.liqpay.ua/uk/doc/api/internet_acquiring/checkout)
- [LiqPay — Callback](https://www.liqpay.ua/uk/doc/api/callback)
- [LiqPay — Статус платежу](https://www.liqpay.ua/uk/doc/api/information/status_payment)
- [LiqPay — Тестування (sandbox)](https://www.liqpay.ua/uk/doc/api/testing)
- [LiqPay — Оплата частинами та Миттєва розстрочка](https://www.liqpay.ua/methods/paypart)
- [LiqPay — Довідник: кредити](https://www.liqpay.ua/information/handbook/credit)
- [LiqPay — Довідник: активація компанії](https://www.liqpay.ua/information/handbook/activation)
- [LiqPay — Довідник: ПРРО](https://www.liqpay.ua/information/handbook/ppo)
- [ПриватБанк — Оплата частинами (тарифна сітка)](https://privatbank.ua/business/oplata-chastynamy)
- [ПриватБанк — Інтернет-еквайринг LiqPay](https://privatbank.ua/business/business-connect-liqpay)
- [ПриватБанк — Програмний РРО від LiqPay](https://privatbank.ua/business/handbook/prro_liqpay)
- [GitHub — liqpay/sdk-python (Python 2, застарілий)](https://github.com/liqpay/sdk-python)
- [WayForPay — тарифи «Оплата частинами» від ПриватБанку](https://help.wayforpay.com/view/361214140)
