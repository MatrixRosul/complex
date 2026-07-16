import Link from "next/link";

import { localePath, type Locale } from "@/i18n/config";
import type { BrandListItem } from "@/lib/api/types";

/**
 * Рядок брендів — другий за силою вхід у каталог після категорій.
 * Людина, яка прийшла «за Bosch», не має продиратись крізь дерево категорій.
 *
 * ⚠️ ЛОГОТИП, А ЯКЩО ЙОГО НЕМАЄ — НАЗВА. Логотипи лежать у `Brand.logo` (адмінка → Бренди),
 * тому це не хардкод: замовник може замінити будь-який або додати свій, і рядок оновиться сам.
 * Зараз лого є у 10 брендів з 12; Sencor і Concept показуються типографікою — і це нормальний
 * стан, а не діра. Саме тому фолбек написаний, а не «домалюємо потім».
 *
 * ⚠️ У ТЕМНІЙ ТЕМІ лого інвертуються в білий (`dark:invert`). Майже всі марки тут — чорні
 * монохромні SVG: на темному фоні вони б просто зникли.
 *
 * ⚠️ Фільтр іде по SLUG (`?brand=bosch`), а не по назві: фасет `brand` на бекенді приймає
 * саме slug — `?brand=Bosch` мовчки дає 0 товарів.
 */
export function BrandStrip({
  brands,
  locale,
  title,
}: {
  brands: BrandListItem[];
  locale: Locale;
  title: string;
}) {
  if (brands.length === 0) return null;

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-h2 text-foreground">{title}</h2>

      {/* Мобільний — свайп зі snap; ПК — рівна сітка. Стиснути 12 брендів у 2 колонки
          означало б зробити їх нечитабельними.
          ⚠️ `scroll-px-4` ОБОВ'ЯЗКОВИЙ у парі з `-mx-4 px-4`, і це не косметика.
          Точки snap рахуються від краю scrollport, а не від padding-box: без scroll-padding
          браузер вважає валідною позицією лише scrollLeft=16 (де плитка впирається в край
          екрана), тому `snap-mandatory` СРАЗУ ж зʼїдає лівий відступ — на завантаженні перша
          плитка обрізана краєм екрана, хоча заголовок над нею стоїть по сітці. */}
      <div className="no-scrollbar -mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto overscroll-x-contain scroll-px-4 px-4 pb-1 md:mx-0 md:grid md:grid-cols-6 md:overflow-visible md:px-0">
        {brands.map((brand) => (
          <Link
            key={brand.id}
            href={localePath(locale, `/catalog?brand=${encodeURIComponent(brand.slug)}`)}
            title={brand.name}
            className="group flex min-w-32 shrink-0 snap-start flex-col items-center justify-center gap-1.5 rounded-lg border border-border bg-card px-4 py-5 transition-all duration-150 hover:-translate-y-0.5 hover:border-transparent hover:shadow-lg md:min-w-0"
          >
            {/*
             * Бокс однакового розміру для лого й тексту — інакше плитки в рядку різного зросту.
             *
             * ⚠️ Ширина НЕ обмежена окремим лімітом (`w-full`, а не `max-w-24`) саме через
             * wordmark-и: «SAMSUNG» — марка з пропорцією близько 10:1. У вузькому боксі вона
             * впирається в ШИРИНУ, а не у висоту, і всихає до нечитабельного напису на 10px,
             * тоді як компактний «BOSCH» поруч займає всі 32px. Тепер `object-contain` вписує
             * лого в повну ширину плитки, і витягнуті марки виходять настільки великими,
             * наскільки дозволяє висота рядка.
             */}
            <div className="flex h-8 w-full items-center justify-center">
              {brand.logo_url ? (
                /*
                 * ⚠️ Звичайний <img>, а НЕ next/image — свідомо, з двох причин:
                 *   1. next/image відхиляє SVG (потрібен `dangerouslyAllowSVG`, а вмикати його
                 *      глобально заради 10 логотипів — погана угода);
                 *   2. SVG усередині <img> рендериться як статична картинка: скрипти в ньому
                 *      НЕ виконуються. Для файлів стороннього походження це саме те, що треба.
                 * Оптимізувати тут нічого: логотипи важать 1–8 КБ.
                 */
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={brand.logo_url}
                  alt={brand.name}
                  loading="lazy"
                  className="size-full object-contain opacity-80 transition-opacity group-hover:opacity-100 dark:invert"
                />
              ) : (
                <span className="text-base font-semibold tracking-tight text-foreground">
                  {brand.name}
                </span>
              )}
            </div>

            <span className="text-xs text-muted-foreground tnum">{brand.products_count}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}
