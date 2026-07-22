"use client";

import { useRef, type ReactNode } from "react";
import Link from "next/link";
import { ChevronRight, LayoutGrid, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import type { CategoryOut } from "@/lib/api/types";
import type { CategoryEmblemData } from "@/lib/category-icons";
import { CategoryEmblem } from "./category-icon";
import { useCatalogMenu } from "./catalog-menu";

/**
 * Мегаменю каталогу (DESIGN_SYSTEM §4.9, референс — скрін …_337).
 *
 * Ліва колонка 280px: кореневі категорії з МІНІ-ЕМБЛЕМАМИ 20px.
 * Права зона: підкатегорії обраного кореня в 2–3 колонках.
 *
 * ⚠️ НА ГОЛОВНІЙ ЦЕЙ КОМПОНЕНТ СВОЄЇ ПАНЕЛІ НЕ МАЛЮЄ — тільки кнопку.
 * Там уже стоїть <CatalogSidebar> з тим самим списком категорій, і власна панель
 * лягала поверх нього зі зсувом: виходили два списки один на одному. Тепер кнопка
 * на головній керує САЙДБАРОМ через спільний стан (useCatalogMenu), тобто
 * розгортає той самий каталог, а не другий. На решті сторінок сайдбара немає —
 * панель малюється як і раніше.
 *
 * Стан (відкрито / активна категорія / таймери ховера / Esc / клік повз) винесений
 * у <CatalogMenuProvider> — див. коментар там, чому саме так.
 *
 * Клавіатура: ↑↓ по кореневих, → у підкатегорії, Esc закриває й повертає фокус.
 */
export function CategoryMegaMenu({
  categories,
  iconOf,
  sideAd,
}: {
  categories: CategoryOut[];
  /** external_id → емблема. Мапа приходить зверху, щоб не тягнути дані в клієнт. */
  iconOf: Record<string, CategoryEmblemData>;
  /** Вузька вертикальна реклама (home_side) праворуч у dropdown — щоб форма збігалася
      з inline-сайдбаром головної. null → колонки просто немає. */
  sideAd?: ReactNode | null;
}) {
  const t = useT();
  const locale = useLocale();

  const {
    open,
    activeIndex,
    setActiveIndex,
    openSoon,
    closeSoon,
    closeNow,
    toggle,
    triggerRef,
    hasInline,
  } = useCatalogMenu();

  const panelRef = useRef<HTMLDivElement>(null);

  /**
   * Dropdown малюється ЛИШЕ там, де inline-каталогу немає (тобто не на головній).
   *
   * ⚠️ Раніше умова була складнішою: на головній dropdown усе-таки з'являвся, щойно
   * сайдбар прокручували за екран. Виходило дві різні форми одного каталогу на одній
   * сторінці — замовник це помітив і попросив прибрати. Тепер на головній форма рівно
   * одна: відкриття з будь-якої точки прокручує сторінку до сайдбара (див. провайдер).
   */
  const showDropdown = open && !hasInline;

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
      data-catalog-menu
      onMouseEnter={openSoon}
      onMouseLeave={closeSoon}
    >
      <Button
        ref={triggerRef}
        variant="default"
        size="xl"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={toggle}
      >
        {/* Відкрито → сітка міняється на ✕: кнопка і відкриває, і закриває (референс denika). */}
        {open ? <X className="size-4" /> : <LayoutGrid className="size-4" />}
        {t("nav.catalog")}
      </Button>

      {showDropdown && (
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
           * ⚡ Розмиття тла ПОВЕРНУЛОСЬ (замовник попросив), але вже НЕ ТУТ, а в
           * <CatalogMenuProvider>: там воно сусід усього дерева, а не дитина цього div,
           * і має `pointer-events-none` — тож обидві проблеми вище не відтворюються.
           * Закриття, як і раніше, тримають `onClickOutside` і Esc у провайдері.
           */}
          <div
            ref={panelRef}
            className="absolute left-0 top-full z-50 mt-2 flex w-[min(90vw,1280px)] overflow-hidden rounded-b-lg rounded-t-md border border-border bg-popover shadow-2xl"
          >
            {/* ── Ліва колонка: кореневі + міні-емблеми ─────────────── */}
            <nav
              aria-label={t("nav.allCategories")}
              className="w-[280px] shrink-0 border-r border-border bg-brand-subtle p-2"
            >
              {categories.map((cat, i) => (
                <Link
                  key={cat.id}
                  href={localePath(locale, `/catalog/${cat.slug}`)}
                  onMouseEnter={() => setActiveIndex(i)}
                  onFocus={() => setActiveIndex(i)}
                  onClick={closeNow}
                  onKeyDown={(e) => onRootKeyDown(e, i)}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm transition-colors",
                    i === activeIndex
                      ? "bg-popover font-medium text-foreground shadow-sm"
                      : "text-foreground hover:bg-popover/60",
                  )}
                >
                  {/* Круглий бейдж: емблема з адмінки або lucide-фолбек (див. CategoryEmblem). */}
                  <CategoryEmblem emblem={iconOf[cat.external_id]} className="size-8" />
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
                    onClick={closeNow}
                    className="text-sm font-semibold text-foreground hover:underline"
                  >
                    {active.name}
                  </Link>

                  <div className="mt-4 grid grid-cols-2 gap-x-8 gap-y-1 xl:grid-cols-3">
                    {(active.children ?? []).map((sub) => (
                      <Link
                        key={sub.id}
                        data-sub-link
                        href={localePath(locale, `/catalog/${active.slug}/${sub.slug}`)}
                        onClick={closeNow}
                        className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
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

            {/* ── Вузька реклама (home_side) — та сама третя колонка, що в inline-сайдбарі.
                Так відкритий каталог виглядає ідентично, звідки б його не відкрили. */}
            {sideAd && (
              <aside className="hidden w-[300px] shrink-0 border-l border-border xl:block">
                {sideAd}
              </aside>
            )}
          </div>
        </>
      )}
    </div>
  );
}
