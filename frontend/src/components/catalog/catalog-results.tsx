import { ActiveFilters, CatalogToolbar } from "@/components/catalog/catalog-toolbar";
import { FacetFilters } from "@/components/catalog/facet-filters";
import { Pagination } from "@/components/catalog/pagination";
import { ProductGrid } from "@/components/product/product-grid";
import { getT } from "@/i18n/dictionary";
import type { Locale } from "@/i18n/config";
import type { CatalogQuery, CatalogResponse, SortKey } from "@/lib/api/types";

/**
 * Результати лістингу: фасети + тулбар + сітка + пагінація.
 *
 * Один компонент на ТРИ сторінки — /catalog, /catalog/{шлях категорії}, /search.
 * Раніше цей блок був скопійований у каталог і пошук; будь-яка зміна фасетів мусила
 * робитись двічі, і рано чи пізно розійшлася б.
 *
 * ⚠️ Серверний компонент: фасети з лічильниками рахує бекенд одним запитом (ADR-008),
 * фронт їх не перераховує й не фільтрує повторно.
 */
export function CatalogResults({
  data,
  locale,
}: {
  data: CatalogResponse;
  locale: Locale;
}) {
  const t = getT(locale);

  if (data.items.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-12 text-center">
        <p className="text-h3 text-foreground">{t("catalog.empty")}</p>
        <p className="mt-2 text-sm text-muted-foreground">{t("catalog.emptyHint")}</p>
      </div>
    );
  }

  return (
    <div className="flex gap-8">
      {/* ── Сайдбар фасетів: 280px, sticky, власний скрол ─────────────── */}
      <aside
        // ⚠️ `overflow-x-hidden` + `pr-3` — не косметика. Вміст був на 11 px ширший за
        // колонку (scrollWidth 291 проти 280), тож унизу вилазила ГОРИЗОНТАЛЬНА смуга
        // прокрутки, а вертикальна лягала просто на лічильники товарів біля брендів.
        // Тепер по горизонталі не скролиться взагалі, а відступ справа лишає смузі
        // власне місце — цифри більше не перекриваються.
        className="sticky top-32 hidden h-fit max-h-[calc(100vh-9rem)] w-[280px] shrink-0 overflow-y-auto overflow-x-hidden pr-3 lg:block"
      >
        <FacetFilters facets={data.facets} priceRange={data.price_range} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <CatalogToolbar total={data.total} facets={data.facets} priceRange={data.price_range} />
        <ActiveFilters facets={data.facets} />
        <ProductGrid products={data.items} />
        <Pagination page={data.page} pages={data.pages} />
      </div>
    </div>
  );
}

export type SearchParams = Record<string, string | string[] | undefined>;

/**
 * Зарезервовані ключі query-string. Усе інше — ФАСЕТ.
 *
 * ⚠️ Саме тому фасети не треба «оголошувати» на фронті: бекенд віддає код групи
 * (`brand`, `no-frost`, `obiem`), фронт кладе його в URL як є і повертає назад.
 * Нова характеристика в адмінці з'являється у фільтрах без жодного рядка коду тут.
 */
const RESERVED = new Set(["page", "sort", "q", "price_min", "price_max"]);

export function parseCatalogParams(sp: SearchParams, category?: string): CatalogQuery {
  const facets: Record<string, string[]> = {};

  for (const [key, value] of Object.entries(sp)) {
    if (RESERVED.has(key) || value === undefined) continue;
    facets[key] = Array.isArray(value) ? value : [value];
  }

  const one = (key: string) => {
    const v = sp[key];
    return Array.isArray(v) ? v[0] : v;
  };

  const q = (one("q") ?? "").trim();

  return {
    category,
    q: q || undefined,
    page: Number(one("page") ?? "1") || 1,
    page_size: 24,
    sort: (one("sort") ?? "popular") as SortKey,
    price_min: one("price_min") ? Number(one("price_min")) : undefined,
    price_max: one("price_max") ? Number(one("price_max")) : undefined,
    facets,
  };
}
