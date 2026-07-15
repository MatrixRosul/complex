"use client";

import { useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";
import type { FacetGroup, PriceRange, SortKey } from "@/lib/api/types";

import { FacetFilters } from "./facet-filters";
import { useCatalogParams } from "./use-catalog-params";

const SORT_KEYS: SortKey[] = ["popular", "price_asc", "price_desc", "new", "name"];

/** Сортування + кнопка фільтрів (мобільний Sheet) + лічильник знайденого. */
export function CatalogToolbar({
  total,
  facets,
  priceRange,
}: {
  total: number;
  facets: FacetGroup[];
  priceRange: PriceRange;
}) {
  const t = useT();
  const { sort, setSort, activeCount } = useCatalogParams();
  const [sheetOpen, setSheetOpen] = useState(false);

  return (
    /**
     * ⚠️ `flex-wrap` — фікс горизонтального скролу на 320px (iPhone SE).
     * Лічильник + кнопка «Фільтри» + селект сортування (190px) не влазять у 288px
     * контенту: тулбар роздувався до 373px і тягнув за собою весь документ (виміряно —
     * scrollWidth 389 при viewport 320). Тепер контроли переносяться на власний рядок.
     */
    <div className="flex flex-wrap items-center justify-between gap-3">
      <span className="text-sm text-muted-foreground tnum">
        {t("catalog.productsCount", { n: total })}
      </span>

      <div className="flex items-center gap-2">
        {/* Фільтри — на мобільному в Sheet на весь екран. */}
        <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
          <SheetTrigger
            render={
              <Button variant="outline" size="xl" className="lg:hidden">
                <SlidersHorizontal className="size-4" />
                {activeCount > 0
                  ? t("catalog.filtersWithCount", { n: activeCount })
                  : t("catalog.filters")}
              </Button>
            }
          />

          <SheetContent side="left" className="flex w-full flex-col gap-0 sm:max-w-md">
            <SheetTitle className="border-b border-border p-4 text-h3">
              {t("catalog.filters")}
            </SheetTitle>

            <div className="flex-1 overflow-y-auto p-4">
              <FacetFilters facets={facets} priceRange={priceRange} />
            </div>

            <div className="border-t border-border p-4">
              <Button size="xl" className="w-full" onClick={() => setSheetOpen(false)}>
                {t("catalog.showProducts", { n: total })}
              </Button>
            </div>
          </SheetContent>
        </Sheet>

        <Select value={sort} onValueChange={(value) => setSort(value as SortKey)}>
          {/* На 320px 190px селекта + кнопка «Фільтри» не лишають місця навіть після
              переносу рядка — тому на вузьких екранах селект вужчий. */}
          <SelectTrigger className="h-10 w-[150px] sm:w-[190px]" aria-label={t("catalog.sort")}>
            {/* Передаємо готовий підпис дитиною: не залежимо від того,
                як base-ui резолвить value → label. */}
            <SelectValue>{t(`catalog.sortBy.${sort}` as "catalog.sortBy.popular")}</SelectValue>
          </SelectTrigger>

          <SelectContent>
            {SORT_KEYS.map((key) => (
              <SelectItem key={key} value={key}>
                {t(`catalog.sortBy.${key}` as "catalog.sortBy.popular")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}

/**
 * Активні фільтри — чіпи над сіткою (DESIGN_SYSTEM §4.6).
 * Показують, ЩО саме звузило видачу: без них покупець бачить 3 товари
 * замість 200 і не розуміє, чому.
 */
export function ActiveFilters({
  facets,
  className,
}: {
  facets: FacetGroup[];
  className?: string;
}) {
  const t = useT();
  const { facets: selected, activeCount, setFacet, resetAll } = useCatalogParams();

  if (activeCount === 0) return null;

  const chips: { code: string; value: string; label: string }[] = [];
  for (const group of facets) {
    for (const value of selected[group.code] ?? []) {
      const match = group.values.find((v) => v.value === value);
      chips.push({
        code: group.code,
        value,
        label: match ? `${group.label}: ${match.label}` : value,
      });
    }
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {chips.map((chip) => (
        <button
          key={`${chip.code}:${chip.value}`}
          type="button"
          onClick={() => setFacet(chip.code, chip.value, false)}
          className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-brand-subtle px-3 py-1 text-xs text-brand-subtle-foreground transition-colors hover:border-primary"
        >
          {chip.label}
          <X aria-hidden className="size-3" />
        </button>
      ))}

      <button
        type="button"
        onClick={resetAll}
        className="text-sm text-muted-foreground underline hover:text-foreground"
      >
        {t("catalog.resetAll")}
      </button>
    </div>
  );
}
