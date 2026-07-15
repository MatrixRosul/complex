"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ApiLang, ProductDetail, ProductListItem } from "@/lib/api/types";
import { useLocale } from "@/i18n/provider";
import { localeToApiLang } from "@/i18n/config";

/**
 * Довантаження товарів за id — для /wishlist і /compare.
 *
 * ⚠️ Той самий принцип, що й у кошику: у localStorage лежать ГОЛІ id, а все, що
 * показується (ціна, наявність, характеристики), тягнеться з бекенда при кожному відкритті.
 * Список бажань живе місяцями — ціна в ньому протухає гарантовано.
 *
 * Обидва ендпоінти толерантні до зниклих id: вони не 404, а просто не повертаються.
 */

/** /wishlist → POST /products/bulk. Одна відповідь = повна картка: ціна, свотчі, бейджі. */
export function useBulkProducts(ids: number[]) {
  return useIdList<ProductListItem>(ids, (list, lang) => api.getProductsBulk(list, lang));
}

/**
 * /compare → повні картки з ХАРАКТЕРИСТИКАМИ.
 * bulk їх не віддає (у сітці й кошику вони не потрібні, а це +40 рядків на товар),
 * тому тут запит на товар. Порівняння обмежене 8 позиціями — це й стеля кількості запитів.
 */
export function useProductDetails(ids: number[]) {
  return useIdList<ProductDetail>(ids, (list, lang) => api.getProductDetails(list, lang));
}

function useIdList<T extends ProductListItem>(
  ids: number[],
  fetcher: (ids: number[], lang: ApiLang) => Promise<T[]>,
) {
  const locale = useLocale();
  const [products, setProducts] = useState<T[]>([]);
  const [isLoading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  /**
   * ⚠️ id, які ми запитали, а бекенд їх НЕ ПОВЕРНУВ (товар деактивовано або видалено).
   *
   * Раніше вони просто відфільтровувались і зникали — мовчки. У порівнянні це давало
   * найгірший наслідок: мертвий id лишався в localStorage НАЗАВЖДИ і далі займав слот
   * у ліміті 4 (canAdd/toggle рахують увесь масив). Людина бачила «Можна порівняти не
   * більше 4 товарів» при трьох видимих товарах — і не могла нічого зробити, крім
   * «Очистити». Тепер сторінка знає про такі id і прибирає їх зі свого стора.
   */
  const [missingIds, setMissingIds] = useState<number[]>([]);

  const key = ids.join(",");

  useEffect(() => {
    let cancelled = false;

    if (ids.length === 0) {
      // Скидання/прапорець мусять спрацювати СИНХРОННО, до await: інакше між
      // зміною входу й відповіддю буде кадр зі старими даними.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setProducts([]);
      setLoading(false);
      setError(null);
      setMissingIds([]);
      return;
    }

    setLoading(true);
    setError(null);

    fetcher(ids, localeToApiLang[locale])
      .then((res) => {
        if (cancelled) return;
        // Зберігаємо порядок додавання користувачем, а не порядок відповіді.
        const byId = new Map(res.map((p) => [p.id, p]));
        setProducts(ids.map((id) => byId.get(id)).filter((p): p is T => Boolean(p)));
        // ⚠️ Тільки в УСПІШНІЙ відповіді. Мережева помилка — це не «товару немає»,
        // і викидати через неї весь список порівняння було б катастрофою.
        setMissingIds(ids.filter((id) => !byId.has(id)));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        setMissingIds([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, locale]);

  return { products, isLoading, error, missingIds };
}
