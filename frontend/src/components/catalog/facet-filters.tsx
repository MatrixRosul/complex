"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { formatPrice } from "@/lib/format";
import type { FacetGroup, PriceRange } from "@/lib/api/types";
import { useCatalogParams } from "./use-catalog-params";

/**
 * Фасетні фільтри (DESIGN_SYSTEM §4.6).
 *
 * ⚠️ Джерело фасетів — ТІЛЬКИ блок «Характеристики» (INPUTS §2). «Опис» не фільтрується
 * ніколи: це вільний rich-HTML, з нього не побудувати надійний токен.
 * Фронт тут нічого не вирішує — він показує те, що прислав бекенд у facets[].
 *
 * ⚠️ Лічильник 0 → пункт disabled, але НЕ схований. Якщо ховати, список фасетів
 * скаче при кожному кліку, і людина втрачає орієнтир («а де був Bosch?»).
 */
export function FacetFilters({
  facets,
  priceRange,
  className,
  onApplied,
}: {
  facets: FacetGroup[];
  priceRange: PriceRange;
  className?: string;
  /** Викликається після зміни — мобільний Sheet себе закриває. */
  onApplied?: () => void;
}) {
  const t = useT();
  const { facets: selected, priceMin, priceMax, activeCount, setFacet, setPrice, resetAll } =
    useCatalogParams();

  return (
    <div className={cn("flex flex-col gap-5", className)}>
      {activeCount > 0 && (
        <button
          type="button"
          onClick={() => {
            resetAll();
            onApplied?.();
          }}
          className="w-fit text-sm text-muted-foreground underline hover:text-foreground"
        >
          {t("catalog.resetAll")}
        </button>
      )}

      <PriceFacet
        range={priceRange}
        min={priceMin}
        max={priceMax}
        onChange={(lo, hi) => {
          setPrice(lo, hi);
          onApplied?.();
        }}
      />

      {facets.map((group, index) => (
        <FacetBlock
          key={group.code}
          group={group}
          selected={selected[group.code] ?? []}
          // Перші 3 групи розгорнуті за замовчуванням.
          defaultOpen={index < 3}
          onToggle={(value, on) => {
            setFacet(group.code, value, on);
            onApplied?.();
          }}
        />
      ))}
    </div>
  );
}

/** Ціна: двоповзунковий Range + два інпути «від»/«до», дебаунс 400 мс. */
function PriceFacet({
  range,
  min,
  max,
  onChange,
}: {
  range: PriceRange;
  min: number | null;
  max: number | null;
  onChange: (min: number | null, max: number | null) => void;
}) {
  const t = useT();
  const locale = useLocale();

  const lo = min ?? range.min;
  const hi = max ?? range.max;

  const [local, setLocal] = useState<[number, number]>([lo, hi]);

  // Синхронізація з URL (наприклад, після «Скинути все») — ПІД ЧАС РЕНДЕРУ,
  // а не в useEffect: ефект дав би зайвий рендер зі старими значеннями повзунка,
  // і той на мить «стрибав» би назад. Це задокументований React-патерн
  // «adjust state when a prop changes».
  const [synced, setSynced] = useState<[number, number]>([lo, hi]);
  if (synced[0] !== lo || synced[1] !== hi) {
    setSynced([lo, hi]);
    setLocal([lo, hi]);
  }

  const commit = (next: [number, number]) => {
    onChange(
      next[0] <= range.min ? null : next[0],
      next[1] >= range.max ? null : next[1],
    );
  };

  if (range.max <= range.min) return null;

  return (
    <section className="border-b border-border pb-5">
      <h3 className="text-sm font-semibold text-foreground">{t("catalog.price")}</h3>

      <Slider
        className="mt-4"
        min={range.min}
        max={range.max}
        value={local}
        onValueChange={(value) =>
          setLocal(Array.isArray(value) ? [value[0], value[1]] : [range.min, value])
        }
        onValueCommitted={(value) =>
          commit(Array.isArray(value) ? [value[0], value[1]] : [range.min, value])
        }
      />

      <div className="mt-3 flex items-center gap-2">
        <PriceInput
          label={t("catalog.priceFrom")}
          value={local[0]}
          onCommit={(v) => {
            const next: [number, number] = [Math.max(range.min, v), local[1]];
            setLocal(next);
            commit(next);
          }}
        />
        <span className="text-muted-foreground">—</span>
        <PriceInput
          label={t("catalog.priceTo")}
          value={local[1]}
          onCommit={(v) => {
            const next: [number, number] = [local[0], Math.min(range.max, v)];
            setLocal(next);
            commit(next);
          }}
        />
      </div>

      <p className="mt-2 text-xs text-muted-foreground tnum">
        {formatPrice(range.min, locale)} — {formatPrice(range.max, locale)} ₴
      </p>
    </section>
  );
}

function PriceInput({
  label,
  value,
  onCommit,
}: {
  label: string;
  value: number;
  onCommit: (value: number) => void;
}) {
  // Той самий патерн: підтягуємо значення з URL під час рендеру, не в ефекті.
  const [text, setText] = useState(String(value));
  const [synced, setSynced] = useState(value);
  if (synced !== value) {
    setSynced(value);
    setText(String(value));
  }

  return (
    <label className="flex flex-1 items-center gap-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          const n = Number.parseInt(text, 10);
          if (Number.isFinite(n)) onCommit(n);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        // border-input (3:1), не border-border — це контрол.
        className="h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm text-foreground tnum focus-visible:border-ring focus-visible:outline-none"
      />
    </label>
  );
}

/** Група фасета: Collapsible + пошук усередині, якщо значень > 8. */
function FacetBlock({
  group,
  selected,
  defaultOpen,
  onToggle,
}: {
  group: FacetGroup;
  selected: string[];
  defaultOpen: boolean;
  onToggle: (value: string, on: boolean) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen);
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);

  const needsSearch = group.values.length > 8;

  const filtered = query
    ? group.values.filter((v) => v.label.toLowerCase().includes(query.toLowerCase()))
    : group.values;

  const visible = showAll ? filtered : filtered.slice(0, 8);
  const rest = filtered.length - visible.length;

  // Switch-фасет («No Frost», «Оплата частинами») — один тумблер, без списку.
  if (group.widget === "switch" && group.values.length === 1) {
    const only = group.values[0];
    return (
      <section className="flex items-center justify-between gap-3 border-b border-border pb-5">
        <label
          htmlFor={`facet-${group.code}`}
          className="text-sm font-semibold text-foreground"
        >
          {group.label}
        </label>
        <Switch
          id={`facet-${group.code}`}
          checked={selected.includes(only.value)}
          onCheckedChange={(checked) => onToggle(only.value, checked === true)}
        />
      </section>
    );
  }

  return (
    <section className="border-b border-border pb-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <h3 className="text-sm font-semibold text-foreground">{group.label}</h3>
        <ChevronDown
          aria-hidden
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="mt-3 flex flex-col gap-2">
          {needsSearch && (
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("catalog.searchInFacet")}
              aria-label={`${group.label}: ${t("catalog.searchInFacet")}`}
              className="h-8 rounded-md border border-input bg-background px-2 text-sm focus-visible:border-ring focus-visible:outline-none"
            />
          )}

          {visible.map((value) => {
            const isChecked = selected.includes(value.value);
            // Лічильник 0 → disabled, але видимий: список не має скакати.
            const isEmpty = value.count === 0 && !isChecked;

            return (
              <label
                key={value.value}
                className={cn(
                  "flex cursor-pointer items-center gap-2.5",
                  isEmpty && "cursor-not-allowed opacity-50",
                )}
              >
                <Checkbox
                  checked={isChecked}
                  disabled={isEmpty}
                  onCheckedChange={(checked) => onToggle(value.value, checked === true)}
                />
                <span className="flex-1 text-sm text-foreground">{value.label}</span>
                <span className="text-xs text-muted-foreground tnum">{value.count}</span>
              </label>
            );
          })}

          {rest > 0 && (
            <Button
              variant="link"
              size="sm"
              className="w-fit px-0"
              onClick={() => setShowAll(true)}
            >
              {t("catalog.showMore", { n: rest })}
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
