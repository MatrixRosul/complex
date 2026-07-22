"use client";

import { useRef, type ReactNode } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { CategoryEmblem } from "@/components/layout/category-icon";
import { useCatalogMenu } from "@/components/layout/catalog-menu";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import type { CategoryOut } from "@/lib/api/types";
import type { CategoryEmblemData } from "@/lib/category-icons";

/**
 * КАТАЛОГ ЗЛІВА НА ГОЛОВНІЙ (референс denika.ua). Ліворуч — список коренів (320px), а
 * ПРАВА ЗОНА ПЕРЕМИКАЄТЬСЯ ЗА СТАНОМ `open` — це два різні екрани, а не один:
 *
 *   СТАН 1 — КАТАЛОГ ЗАКРИТИЙ (спокій при завантаженні):
 *     [ список ][ ШИРОКИЙ промо-банер ]. Підгруп НЕ видно. Немає банера → права зона
 *     просто порожня (жодної заглушки).
 *
 *   СТАН 2 — КАТАЛОГ ВІДКРИТИЙ (навели на список/кнопку або клікнули кнопку):
 *     [ список ][ підгрупи активного кореня, 3 колонки ][ опційна ВУЗЬКА реклама 300px ].
 *     Це РІВНО ТА САМА єдина форма, що й dropdown мегаменю (замовник: «тільки 1 форма,
 *     не як зараз дві»). Немає вузького банера → просто [список][підгрупи].
 *
 * ⚠️ КНОПКА «КАТАЛОГ» ВІДКРИВАЄ КАТАЛОГ ЗВІДУСІЛЬ. Стан спільний із кнопкою (useCatalogMenu).
 * Поки цей сайдбар у в'юпорті — кнопка розгортає ЙОГО (СТАН 2 просто в самому сайдбарі),
 * dropdown не малюється, нічого не дублюється. Прокрутили сайдбар за екран — секція
 * повідомляє це через IntersectionObserver (`setInlineEl`), і кнопка перемикається на
 * dropdown зі стікі-хедера — ту саму єдину форму.
 *
 * Клавіатура: ↑↓ по коренях, → у підгрупи, Esc закриває.
 */
export function CatalogSidebar({
  categories,
  iconOf,
  promoBanner,
  sideAd,
}: {
  categories: CategoryOut[];
  iconOf: Record<string, CategoryEmblemData>;
  /** ШИРОКИЙ банер для ЗАКРИТОГО стану (home_promo). null → права зона порожня. */
  promoBanner: ReactNode | null;
  /** ВУЗЬКА вертикальна реклама для ВІДКРИТОГО стану (home_side). null → без третьої колонки. */
  sideAd: ReactNode | null;
}) {
  const t = useT();
  const locale = useLocale();

  // Стан спільний з кнопкою «Каталог» — Esc і клік повз обробляє провайдер.
  const { open, activeIndex, setActiveIndex, openNow, closeSoon, closeNow, setInlineEl } =
    useCatalogMenu();
  const panelRef = useRef<HTMLDivElement>(null);

  if (categories.length === 0) return null;

  const active = categories[activeIndex] ?? categories[0];

  /** Наведення й фокус — одне й те саме: відкрити каталог на підгрупах цього кореня. */
  const activate = (index: number) => {
    setActiveIndex(index);
    openNow();
  };

  const onRootKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activate((index + 1) % categories.length);
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      activate((index - 1 + categories.length) % categories.length);
    }
    if (e.key === "ArrowRight") {
      e.preventDefault();
      panelRef.current?.querySelector<HTMLAnchorElement>("[data-sub-link]")?.focus();
    }
  };

  return (
    // ⚠️ `hidden lg:flex` — на вузьких екранах двох колонок не буває фізично, а
    // акордеон дублював би бургер-меню. Мобільний вхід у каталог — плитки
    // <CategoryTiles> (вони, навпаки, сховані на lg+), тож дублювання немає ніде.
    <section
      // ref → провайдер: реєструє сайдбар як ЄДИНУ форму каталогу на цій сторінці
      // (dropdown тут не малюється) і, якщо секція за екраном, везе до неї сторінку.
      ref={setInlineEl}
      // z-30 у відкритому стані: НАД підкладкою (z-20), але ПІД хедером (z-40).
      // Спершу тут стояв z-40 — рівний хедеру, і сайдбар налазив на шапку при
      // прокрутці, бо при однакових z виграє пізніший у DOM.
      //
      // ⚠️ ОДНА СУЦІЛЬНА ПАНЕЛЬ, А НЕ ТРИ КАРТКИ. Рамка й заокруглення живуть ТУТ, на
      // зовнішньому контейнері, а колонки всередині розділені лише лінією (border-r/l).
      // Тому між списком, підгрупами й банером немає ні відступів, ні внутрішніх
      // закруглень — заокруглені лише зовнішні кути (прохання замовника).
      // `overflow-hidden` обов'язковий: без нього кути дітей вилазять за радіус рамки.
      className={cn(
        "hidden overflow-hidden rounded-xl border border-border lg:flex",
        open && "relative z-30",
      )}
      data-catalog-menu
      onMouseLeave={closeSoon}
      aria-label={t("home.catalogHeading")}
    >
      {/* Панель каталогу залита брендовим відтінком (референс denika.ua): колір несе САМ
          СПИСОК, а не кнопка в шапці. Активний пункт при цьому світлий — він «випадає»
          з заливки. Підсвітка лише коли каталог ВІДКРИТИЙ: у стані спокою (показуємо промо)
          жоден пункт не «обраний». */}
      <nav className="w-[320px] shrink-0 border-r border-border bg-brand-subtle p-2">
        <h2 className="px-3 py-2 text-sm font-semibold text-foreground">
          {t("home.catalogHeading")}
        </h2>

        {categories.map((cat, i) => (
          <Link
            key={cat.id}
            href={localePath(locale, `/catalog/${cat.slug}`)}
            onMouseEnter={() => activate(i)}
            onFocus={() => activate(i)}
            onClick={closeNow}
            onKeyDown={(e) => onRootKeyDown(e, i)}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              open && i === activeIndex
                ? "bg-card font-medium text-foreground shadow-sm"
                : "text-foreground hover:bg-card/60",
            )}
          >
            <CategoryEmblem emblem={iconOf[cat.external_id]} />
            <span className="flex-1">{cat.name}</span>
            <ChevronRight aria-hidden className="size-4 opacity-60" />
          </Link>
        ))}
      </nav>

      {/* Права зона: ЗАКРИТО → широкий промо; ВІДКРИТО → підгрупи + опційна вузька реклама.
          Обидва стани тягнуться на висоту списку (flex align-stretch), тож при перемиканні
          немає вертикального стрибка — міняється лише вміст. */}
      <div className="flex min-w-0 flex-1">
        {open ? (
          <>
            <div ref={panelRef} className="min-w-0 flex-1 overflow-y-auto bg-popover p-6">
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
                    <span className="text-xs text-muted-foreground tnum">{sub.products_count}</span>
                  </Link>
                ))}
              </div>
            </div>

            {sideAd && (
              <aside className="hidden w-[300px] shrink-0 border-l border-border xl:block">
                {sideAd}
              </aside>
            )}
          </>
        ) : (
          promoBanner
        )}
      </div>
    </section>
  );
}
