"use client";

import { useCallback } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { SortKey } from "@/lib/api/types";

/** Ключі, які НЕ є фасетами — усе інше в query-string вважаємо фасетом. */
const RESERVED = new Set(["page", "sort", "q", "price_min", "price_max"]);

/**
 * Стан каталогу живе В URL, а не в React-стейті (DESIGN_SYSTEM §4.6).
 *
 * Чому: ?brand=lg&kolir=chornyi має бути шеронабельним і індексованим. Якби фільтри
 * лежали в useState, покупець не міг би скинути посилання на підбірку другу,
 * а Google не побачив би жодної сторінки фільтра.
 */
export function useCatalogParams() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  /** Обрані фасети: { brand: ["bosch","lg"], obiem: ["300-399"] }. */
  const facets: Record<string, string[]> = {};
  for (const key of new Set(searchParams.keys())) {
    if (RESERVED.has(key)) continue;
    facets[key] = searchParams.getAll(key);
  }

  const page = Number(searchParams.get("page") ?? "1") || 1;
  const sort = (searchParams.get("sort") ?? "popular") as SortKey;
  const q = searchParams.get("q") ?? "";
  const priceMinRaw = searchParams.get("price_min");
  const priceMaxRaw = searchParams.get("price_max");

  const activeCount =
    Object.values(facets).reduce((sum, v) => sum + v.length, 0) +
    (priceMinRaw || priceMaxRaw ? 1 : 0);

  /**
   * ⚠️ Актуальні параметри беремо з АДРЕСНОГО РЯДКА, а не з `searchParams`-замикання.
   *
   * `searchParams` із useSearchParams оновлюється лише після ре-рендеру, а `router.push`
   * міняє URL синхронно (History API) ще до нього. Тобто одразу після вибору бренду є
   * вікно (~200–400 мс pending-переходу), де замикання ще тримає СТАРІ параметри. Клік
   * «зняти галочку» в цьому вікні рахувався від старого стану — і замість зняття фасет
   * ДОДАВАВСЯ назад: URL лишався `?brand=…`, галочка стояла, сітка не скидалась. Саме той
   * «деколи фільтр не знімається». window.location.search завжди відображає поточний URL.
   */
  const liveParams = () =>
    new URLSearchParams(
      typeof window === "undefined" ? searchParams.toString() : window.location.search,
    );

  const push = useCallback(
    (next: URLSearchParams) => {
      // Будь-яка зміна фільтрів скидає пагінацію: інакше людина, що стояла
      // на 5-й сторінці й звузила вибірку до 12 товарів, побачить порожньо.
      next.delete("page");
      const qs = next.toString();
      router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  /**
   * Виставляє фасет у ЯВНИЙ стан (`on`), а не «перемикає». Це критично:
   * перемикання за `includes()` під час pending-переходу читало застарілі
   * (часто порожні) параметри — і клік «зняти галочку» перетворювався на «додати»,
   * фасет повертався, сітка не скидалась. Чекбокс сам знає свій новий стан
   * (`onCheckedChange(checked)`), тож «зняти» = ЗАВЖДИ прибрати (з порожнього — no-op),
   * «поставити» = ЗАВЖДИ додати. Ідемпотентно й імунно до гонки читання.
   */
  const setFacet = useCallback(
    (code: string, value: string, on: boolean) => {
      const next = liveParams();
      const rest = next.getAll(code).filter((v) => v !== value);
      if (on) rest.push(value);

      next.delete(code);
      for (const v of rest) next.append(code, v);
      push(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [push],
  );

  const setPrice = useCallback(
    (min: number | null, max: number | null) => {
      const next = liveParams();
      if (min === null) next.delete("price_min");
      else next.set("price_min", String(min));
      if (max === null) next.delete("price_max");
      else next.set("price_max", String(max));
      push(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [push],
  );

  const setSort = useCallback(
    (value: SortKey) => {
      const next = liveParams();
      next.set("sort", value);
      push(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [push],
  );

  const setPage = useCallback(
    (value: number) => {
      const next = liveParams();
      if (value <= 1) next.delete("page");
      else next.set("page", String(value));
      const qs = next.toString();
      router.push(qs ? `${pathname}?${qs}` : pathname);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pathname, router],
  );

  const resetAll = useCallback(() => {
    // Живий URL (див. liveParams): скидання теж може прилетіти під час pending-переходу.
    const live = liveParams();
    const next = new URLSearchParams();
    // Пошуковий запит і сортування — не фільтри, їх скидати не треба.
    const liveQ = live.get("q");
    const liveSort = live.get("sort");
    if (liveQ) next.set("q", liveQ);
    if (liveSort && liveSort !== "popular") next.set("sort", liveSort);
    const qs = next.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, router]);

  return {
    facets,
    page,
    sort,
    q,
    priceMin: priceMinRaw ? Number(priceMinRaw) : null,
    priceMax: priceMaxRaw ? Number(priceMaxRaw) : null,
    activeCount,
    setFacet,
    setPrice,
    setSort,
    setPage,
    resetAll,
  };
}
