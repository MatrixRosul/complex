"""Розширення PostgreSQL, TS-конфіги, функція ціни і sequence номерів замовлень.

ADR-022: усе це створюється МІГРАЦІЄЮ, а не init-скриптом образу postgres.
`infra/postgres/init/*.sql` виконується рівно один раз при ініціалізації тому і рівно
в базі $POSTGRES_DB. Тестова база pytest-django (`test_nisa`) клонується з template1,
де розширень немає → перша ж міграція з opclasses=["gin_trgm_ops"] падає в CI з
`operator class "gin_trgm_ops" does not exist`. Так само ламається staging, restore
в чисту БД і будь-який managed-Postgres.

⚠️ Ця міграція НЕ МАЄ dependencies і НЕ створює моделей — тільки об'єкти БД.
   Міграція catalog/0001_initial МУСИТЬ мати її у своїх dependencies:
       dependencies = [("core", "0001_extensions"), ...]
   Без цього Django може застосувати catalog ПЕРЕД core (граф не має ребра між ними,
   а "catalog" < "core" за алфавітом) → GinIndex(gin_trgm_ops) впаде на чистій БД.

⚠️ PostgreSQL 14. Нічого з PG15+ (MERGE, nulls_distinct) тут немає.

⚠️ РЕБРЕНДИНГ (14.07.2026). Тут імена ВЖЕ нові: `complex_price_uah()`, `ru_complex`.
   У БД, які піднялись до перейменування, ця міграція вже позначена застосованою і
   вдруге не виконається — там досі старі `nisa_price_uah()` / `ru_nisa`. Їх приводить
   до нового стану core/0003_rename_pg_objects_to_complex (ідемпотентний DDL).
   Ім'я самої БД (`nisa`, тестова `test_nisa`) лишилось СТАРИМ — свідомо.
"""

from __future__ import annotations

from django.contrib.postgres.operations import (
    BtreeGinExtension,
    TrigramExtension,
    UnaccentExtension,
)
from django.db import migrations

# ---------------------------------------------------------------------------
# TS-конфігурації (ADR-009)
# ---------------------------------------------------------------------------
# В PostgreSQL НЕМАЄ вбудованої конфігурації для української: to_tsvector('ukrainian', …)
# кине помилку. Робимо `uk` = simple + unaccent (нормалізація без стемінгу); реальний
# стемінг дає core/text/uk_stem.py, який застосовується СИМЕТРИЧНО — і при побудові
# search_vector_uk, і при парсингу запиту.
UK_TS_CONFIG_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'uk') THEN
        CREATE TEXT SEARCH CONFIGURATION uk (COPY = simple);
        ALTER TEXT SEARCH CONFIGURATION uk
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple;
    END IF;
END $$;
"""

# Ім'я саме `ru_complex`, а не `ru`: `ru` у частині збірок PG — уже наявний алiас, і CREATE впаде.
# RU — робоча мова в MVP (INPUTS §1), тому search_vector_ru мусить мати свою конфігурацію.
RU_TS_CONFIG_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'ru_complex') THEN
        CREATE TEXT SEARCH CONFIGURATION ru_complex (COPY = russian);
        ALTER TEXT SEARCH CONFIGURATION ru_complex
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, russian_stem;
    END IF;
END $$;
"""

# ---------------------------------------------------------------------------
# ЄДИНЕ джерело формули ціни (ADR-005)
# ---------------------------------------------------------------------------
# Її викликають ОБИДВА місця, де змінюється ціна: project_offers() (синк) і
# recalc_prices() (зміна курсу/округлення/націнки). Python-двійник
# catalog/services/pricing.py::compute_uah_price() існує ЛИШЕ для preview/адмінки і
# покритий обов'язковим parity-fuzz-тестом.
#
# ⚠️ GeneratedField для ціни на PG14 неможливий: вираз мусить бути IMMUTABLE і не має
#    права брати курс із зовнішньої таблиці. Тому ціна — це UPDATE з викликом функції.
PRICE_FN_SQL = """
CREATE OR REPLACE FUNCTION complex_price_uah(
    base numeric, currency text, rate numeric, markup numeric, rule text
) RETURNS numeric
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v numeric;
BEGIN
    IF base IS NULL THEN RETURN NULL; END IF;
    v := base * (1 + COALESCE(markup, 0) / 100.0);
    IF currency = 'USD' THEN
        IF rate IS NULL OR rate <= 0 THEN RETURN NULL; END IF;
        v := v * rate;
    END IF;
    RETURN CASE rule
        WHEN 'none' THEN round(v, 2)
        WHEN 'unit' THEN round(v, 0)
        WHEN 'ten'  THEN round(v / 10, 0) * 10
        WHEN 'nine' THEN CASE WHEN v < 100 THEN ceil(v)
                              ELSE greatest(round(v / 10, 0) * 10 - 1, 9) END
        ELSE round(v, 0)
    END;
END $$;
"""

# ADR-014: номер замовлення — з sequence, а не count()+1.
# count()+1 — це гонка: два одночасні checkout'и беруть однаковий номер, і другий падає
# з IntegrityError ВЖЕ ПІСЛЯ створення платежу. Sequence не відкочується транзакцією.
ORDER_SEQ_SQL = "CREATE SEQUENCE IF NOT EXISTS order_number_seq START 1;"


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        TrigramExtension(),  # pg_trgm   — trigram-пошук, автокомпліт НП, «схожі назви»
        BtreeGinExtension(),  # btree_gin — композитні GIN
        UnaccentExtension(),  # unaccent  — потрібен обом TS-конфігам нижче
        migrations.RunSQL(
            sql=UK_TS_CONFIG_SQL,
            reverse_sql="DROP TEXT SEARCH CONFIGURATION IF EXISTS uk;",
        ),
        migrations.RunSQL(
            sql=RU_TS_CONFIG_SQL,
            reverse_sql="DROP TEXT SEARCH CONFIGURATION IF EXISTS ru_complex;",
        ),
        migrations.RunSQL(
            sql=PRICE_FN_SQL,
            reverse_sql="DROP FUNCTION IF EXISTS complex_price_uah(numeric, text, numeric, numeric, text);",
        ),
        migrations.RunSQL(
            sql=ORDER_SEQ_SQL,
            reverse_sql="DROP SEQUENCE IF EXISTS order_number_seq;",
        ),
    ]
