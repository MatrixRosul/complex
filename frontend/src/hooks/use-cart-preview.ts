"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CartPreviewResponse } from "@/lib/api/types";
import { useLocale } from "@/i18n/provider";
import { localeToApiLang } from "@/i18n/config";
import { useCartStore } from "@/store/cart";

/** Порожній кошик — відповідь відома без жодного запиту. */
const EMPTY: CartPreviewResponse = {
  items: [],
  subtotal: "0.00",
  installment_allowed: false,
  changed_items: [],
  unavailable_items: [],
};

/**
 * ⚠️ ЄДИНЕ джерело цін і наявності для кошика.
 *
 * localStorage тримає {id, qty}. Усе інше — назва, ціна, стара ціна, наявність,
 * чи доступна оплата частинами, підсумок — приходить сюди з бекенда bulk-запитом.
 * Ніякого кешу: інакше сенс втрачається і ми показуємо протухлу ціну.
 *
 * Побічний ефект, який ми робимо навмисно: позиції з preview.unavailable_items
 * викидаємо з localStorage — товар зник з каталогу, тримати його в кошику нема сенсу.
 */
export function useCartPreview() {
  const lines = useCartStore((s) => s.lines);
  const pruneUnavailable = useCartStore((s) => s.pruneUnavailable);
  const locale = useLocale();

  const [data, setData] = useState<CartPreviewResponse | null>(null);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  /**
   * ⚠️ ЛИПКИЙ список викинутих позицій — і саме тому окремий стан, а не `data.unavailable_items`.
   *
   * Ланцюжок такий: відповідь каже «id 999999 більше немає» → pruneUnavailable() чистить
   * localStorage → змінюється `key` → злітає НОВИЙ запит → він повертає ЧИСТУ відповідь
   * (unavailable_items вже порожній) → `data` перезаписується. Банер «товар більше
   * недоступний» жив рівно один цикл рендера: товар зникав, а попередження людина не
   * встигала прочитати. Тому факт видалення тримаємо ТУТ — він переживає перезапит.
   */
  const [removed, setRemoved] = useState<number[]>([]);

  // Стабільний ключ: перезапит лише коли реально змінився склад кошика.
  const key = lines.map((l) => `${l.id}:${l.qty}`).join(",");
  const [reloadToken, setReloadToken] = useState(0);

  const reload = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    // Порожній кошик обробляється в рендері (нижче) — запит не потрібен.
    if (lines.length === 0) return;

    let cancelled = false;

    // Прапорець мусить піднятись СИНХРОННО, до await: інакше між зміною складу
    // кошика й відповіддю сервера буде кадр зі старими цінами — рівно те,
    // від чого ми тут і захищаємось.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);

    api
      .previewCart(lines, localeToApiLang[locale])
      .then((res) => {
        if (cancelled) return;
        setData(res);
        if (res.unavailable_items.length > 0) {
          setRemoved((prev) => [...new Set([...prev, ...res.unavailable_items])]);
          pruneUnavailable(res.unavailable_items);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // `key` замінює `lines` навмисно: масив — новий об'єкт на кожен рендер стора,
    // тож пряма залежність від нього дала б нескінченний цикл запитів.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, reloadToken, locale, pruneUnavailable]);

  // Порожній стан — похідний, а не збережений: жодного зайвого рендера.
  const isEmpty = lines.length === 0;

  const dismissRemoved = useCallback(() => setRemoved([]), []);

  return {
    data: isEmpty ? EMPTY : data,
    isLoading: isEmpty ? false : isLoading,
    error: isEmpty ? null : error,
    /** Позиції, які бекенд визнав недоступними і які ми вже прибрали з кошика. */
    removedItems: removed,
    dismissRemoved,
    reload,
  };
}
