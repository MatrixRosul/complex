"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ImageOff, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatPriceWithCurrency } from "@/lib/format";
import { useLocale, useT } from "@/i18n/provider";
import { localePath, localeToApiLang } from "@/i18n/config";

/** Тонка форма підказки — рівно те, що віддає /api/suggest. */
type Suggestion = {
  id: number;
  name: string;
  slug: string;
  price: string;
  old_price: string | null;
  main_image_url: string | null;
};

const MIN_CHARS = 2;
const DEBOUNCE_MS = 220;

export function SearchBar({
  className,
  initialQuery = "",
}: {
  className?: string;
  initialQuery?: string;
}) {
  const t = useT();
  const locale = useLocale();
  const router = useRouter();
  const listboxId = useId();

  const [value, setValue] = useState(initialQuery);
  const [items, setItems] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  // Індекс підсвіченого рядка для навігації клавіатурою; -1 — нічого не обрано.
  const [active, setActive] = useState(-1);

  const rootRef = useRef<HTMLDivElement>(null);

  const query = value.trim();

  // ── Дебаунс + запит підказок ────────────────────────────────────────────
  useEffect(() => {
    if (query.length < MIN_CHARS) {
      setItems([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/suggest?q=${encodeURIComponent(query)}&locale=${locale}`,
          { signal: ctrl.signal },
        );
        if (!res.ok) throw new Error(String(res.status));
        const data: { items: Suggestion[] } = await res.json();
        setItems(data.items ?? []);
        setActive(-1);
      } catch (err) {
        if ((err as Error).name !== "AbortError") setItems([]);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [query, locale]);

  // ── Закриття по кліку поза компонентом ──────────────────────────────────
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function goToSearch(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    setOpen(false);
    router.push(localePath(locale, `/search?q=${encodeURIComponent(trimmed)}`));
  }

  function goToProduct(item: Suggestion) {
    setOpen(false);
    setValue(item.name);
    router.push(localePath(locale, `/p/${item.id}/${item.slug}`));
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open || items.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? items.length - 1 : i - 1));
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      goToProduct(items[active]);
    }
  }

  const showDropdown =
    open && query.length >= MIN_CHARS && (loading || items.length > 0);

  return (
    <div ref={rootRef} className={cn("relative w-full", className)}>
      <form
        role="search"
        className="flex w-full items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          goToSearch(value);
        }}
      >
        <div className="relative flex-1">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            name="q"
            value={value}
            autoComplete="off"
            role="combobox"
            aria-expanded={showDropdown}
            aria-controls={listboxId}
            aria-autocomplete="list"
            aria-activedescendant={
              active >= 0 ? `${listboxId}-opt-${active}` : undefined
            }
            onChange={(e) => {
              setValue(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
            placeholder={t("search.placeholder")}
            aria-label={t("search.placeholder")}
            // ⚠️ border-input (3.42:1), а НЕ border-border (1.27:1):
            // це контрол, для нього WCAG 1.4.11 вимагає 3:1.
            className="h-10 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
          />
        </div>

        <Button type="submit" variant="secondary" size="xl" className="hidden sm:inline-flex">
          {t("search.submit")}
        </Button>
      </form>

      {showDropdown && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-lg border border-border bg-popover shadow-lg"
        >
          {loading && items.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              {t("search.suggesting")}
            </p>
          ) : (
            <ul className="max-h-[70vh] overflow-y-auto py-1">
              {items.map((item, i) => (
                <li key={item.id} id={`${listboxId}-opt-${i}`} role="option" aria-selected={i === active}>
                  <button
                    type="button"
                    // onMouseDown (не onClick): спрацьовує до blur інпута, щоб перехід не гасив клік.
                    onMouseDown={(e) => {
                      e.preventDefault();
                      goToProduct(item);
                    }}
                    onMouseEnter={() => setActive(i)}
                    className={cn(
                      "flex w-full items-center gap-3 px-3 py-2 text-left",
                      i === active ? "bg-accent" : "hover:bg-accent/60",
                    )}
                  >
                    <span className="relative flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted">
                      {item.main_image_url ? (
                        <Image
                          src={item.main_image_url}
                          alt=""
                          fill
                          sizes="48px"
                          className="object-contain"
                        />
                      ) : (
                        <ImageOff aria-hidden className="size-5 text-muted-foreground" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="line-clamp-2 text-sm text-foreground">{item.name}</span>
                      <span className="mt-0.5 flex items-baseline gap-2">
                        <span className="text-sm font-semibold text-foreground tnum">
                          {formatPriceWithCurrency(item.price, locale)}
                        </span>
                        {item.old_price && (
                          <span className="text-xs text-muted-foreground line-through tnum">
                            {formatPriceWithCurrency(item.old_price, locale)}
                          </span>
                        )}
                      </span>
                    </span>
                  </button>
                </li>
              ))}

              <li className="border-t border-border">
                <Link
                  href={localePath(locale, `/search?q=${encodeURIComponent(query)}`)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    goToSearch(query);
                  }}
                  className="flex items-center gap-2 px-3 py-2.5 text-sm font-medium text-foreground hover:bg-accent/60"
                >
                  <Search aria-hidden className="size-4" />
                  {t("search.viewAll")}
                </Link>
              </li>
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
