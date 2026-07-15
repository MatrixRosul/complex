"use client";

import { useEffect } from "react";
import Link from "next/link";

import { RelatedProducts } from "@/components/product/related-products";
import { useBulkProducts } from "@/hooks/use-bulk-products";
import { useHydrated } from "@/hooks/use-hydrated";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { useRecentlyViewedStore } from "@/store/recently-viewed";

/**
 * «Ви переглядали» на головній.
 *
 * ⚠️ БЛОК ВЗАГАЛІ НЕ ІСНУЄ, поки історія порожня. Не «порожній стан», не заглушка,
 *    не скелет — саме `null`. Новий відвідувач нічого не переглядав, і показувати йому
 *    заголовок «Ви переглядали» над порожнечею — це діра в макеті і брехня водночас.
 *
 * ⚠️ Ціни тягне POST /products/bulk, у localStorage лежать ТІЛЬКИ id (див. store/recently-viewed).
 *
 * ⚠️ ДО ГІДРАТАЦІЇ теж `null`, а не скелет. Сервер про localStorage не знає й віддав би
 *    порожній блок; будь-який інший placeholder дав би стрибок макета рівно на висоту
 *    каруселі. Блок з'являється знизу — там, де він нікому не зсуває контент.
 */
export function RecentlyViewed() {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const ids = useRecentlyViewedStore((s) => s.ids);
  const remove = useRecentlyViewedStore((s) => s.remove);

  // Порядок відповіді = порядок ids (хук це гарантує) → найновіші лишаються спереду.
  const { products, missingIds } = useBulkProducts(ids);

  /**
   * Товар зняли з продажу → bulk його не повернув. Прибираємо з історії МОВЧКИ:
   * на відміну від бажань, історію переглядів людина не збирала руками, і тост
   * «щось зникло» тут був би шумом про те, чого вона не просила.
   */
  useEffect(() => {
    if (missingIds.length === 0) return;
    missingIds.forEach(remove);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missingIds.join(",")]);

  // Порожня історія / ще не гідратувались / бекенд нічого не віддав → блоку немає.
  // Помилку API теж ковтаємо: «Ви переглядали» — допоміжний блок, і червоний банер
  // помилки замість нього на головній коштував би більше, ніж сам блок.
  if (!hydrated || ids.length === 0 || products.length === 0) return null;

  return (
    <RelatedProducts
      title={t("home.recentlyViewed")}
      products={products}
      action={
        <Link
          href={localePath(locale, "/catalog")}
          className="text-sm text-primary hover:underline"
        >
          {t("home.showAll")}
        </Link>
      }
    />
  );
}
