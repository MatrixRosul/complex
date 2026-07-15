"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronRight, LayoutGrid } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import type { CategoryOut } from "@/lib/api/types";
import { CategoryIcon } from "./category-icon";

/**
 * Мегаменю каталогу (DESIGN_SYSTEM §4.9, референс — скрін …_337).
 *
 * Ліва колонка 280px: кореневі категорії з МІНІ-ЕМБЛЕМАМИ 20px.
 * Права зона: підкатегорії обраного кореня в 2–3 колонках.
 *
 * ⚠️ Затримки на hover — не примха:
 *   150 мс на відкриття — щоб меню не спалахувало, коли миша просто пролітає повз;
 *   200 мс на закриття  — щоб воно не зривалось при діагональному русі миші
 *                          від кореня до підкатегорії (класична проблема мегаменю).
 *
 * Клавіатура: ↑↓ по кореневих, → у підкатегорії, Esc закриває й повертає фокус.
 */
export function CategoryMegaMenu({
  categories,
  iconOf,
}: {
  categories: CategoryOut[];
  /** external_id → ключ іконки. Мапа приходить зверху, щоб не тягнути дані в клієнт. */
  iconOf: Record<string, string>;
}) {
  const t = useT();
  const locale = useLocale();

  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = () => {
    if (openTimer.current) clearTimeout(openTimer.current);
    if (closeTimer.current) clearTimeout(closeTimer.current);
  };

  const scheduleOpen = () => {
    clearTimers();
    openTimer.current = setTimeout(() => setOpen(true), 150);
  };

  const scheduleClose = () => {
    clearTimers();
    closeTimer.current = setTimeout(() => setOpen(false), 200);
  };

  // Esc закриває, фокус повертається на тригер.
  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    const onClickOutside = (e: MouseEvent) => {
      if (
        !panelRef.current?.contains(e.target as Node) &&
        !triggerRef.current?.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open]);

  useEffect(() => clearTimers, []);

  const active = categories[activeIndex];

  const onRootKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((index + 1) % categories.length);
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((index - 1 + categories.length) % categories.length);
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      panelRef.current?.querySelector<HTMLAnchorElement>("[data-sub-link]")?.focus();
    }
  };

  return (
    <div
      className="relative"
      onMouseEnter={scheduleOpen}
      onMouseLeave={scheduleClose}
    >
      <Button
        ref={triggerRef}
        variant="default"
        size="xl"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        <LayoutGrid className="size-4" />
        {t("nav.catalog")}
      </Button>

      {open && (
        <>
          {/*
           * ⚠️ ТУТ БУВ ОВЕРЛЕЙ НА ВЕСЬ ЕКРАН (`fixed inset-0 bg-foreground/20 backdrop-blur-[2px]`),
           * і він ламав меню одразу двома способами:
           *
           * 1. ЗАЛИПАННЯ. Оверлей — дитина того самого div, що слухає `onMouseLeave`. Курсор,
           *    який стоїть над fixed-оверлеєм на весь екран, з погляду DOM усе ще ВСЕРЕДИНІ
           *    цього div — тому `mouseleave` не спрацьовував ніколи. Меню відкривалось на hover
           *    і більше не закривалось, поки не клікнеш повз.
           * 2. ЛАГИ. `backdrop-blur` на весь в'юпорт змушує браузер перераховувати розмиття
           *    фону на кожен кадр руху миші — на десктопі це відчутне гальмо.
           *
           * Оверлей був не потрібен і функціонально: клік повз меню вже ловить `onClickOutside`,
           * а Esc — `onKey`. Тому його просто немає.
           */}
          <div
            ref={panelRef}
            className="absolute left-0 top-full z-50 mt-2 flex w-[min(90vw,1280px)] overflow-hidden rounded-b-lg rounded-t-md border border-border bg-popover shadow-2xl"
          >
            {/* ── Ліва колонка: кореневі + міні-емблеми ─────────────── */}
            <nav
              aria-label={t("nav.allCategories")}
              className="w-[280px] shrink-0 border-r border-border p-2"
            >
              {categories.map((cat, i) => (
                <Link
                  key={cat.id}
                  href={localePath(locale, `/catalog/${cat.slug}`)}
                  onMouseEnter={() => setActiveIndex(i)}
                  onFocus={() => setActiveIndex(i)}
                  onKeyDown={(e) => onRootKeyDown(e, i)}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm transition-colors",
                    i === activeIndex
                      ? "bg-brand-subtle text-brand-subtle-foreground"
                      : "text-foreground hover:bg-accent",
                  )}
                >
                  {/* Іконка успадковує колір тексту — працює і в темній темі. */}
                  <CategoryIcon name={iconOf[cat.external_id] ?? "package"} />
                  <span className="flex-1">{cat.name}</span>
                  <ChevronRight aria-hidden className="size-4 opacity-60" />
                </Link>
              ))}
            </nav>

            {/* ── Права зона: підкатегорії у 2–3 колонках ──────────── */}
            <div className="flex-1 p-6">
              {active && (
                <>
                  <Link
                    href={localePath(locale, `/catalog/${active.slug}`)}
                    className="text-sm font-semibold text-foreground hover:text-primary"
                  >
                    {active.name}
                  </Link>

                  <div className="mt-4 grid grid-cols-2 gap-x-8 gap-y-1 lg:grid-cols-3">
                    {(active.children ?? []).map((sub) => (
                      <Link
                        key={sub.id}
                        data-sub-link
                        href={localePath(locale, `/catalog/${active.slug}/${sub.slug}`)}
                        className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:text-primary"
                      >
                        <span>{sub.name}</span>
                        <span className="text-xs text-muted-foreground tnum">
                          {sub.products_count}
                        </span>
                      </Link>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
