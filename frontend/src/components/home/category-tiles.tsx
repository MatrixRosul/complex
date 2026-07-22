import Image from "next/image";
import Link from "next/link";

import { CategoryIcon } from "@/components/layout/category-icon";
import { categoryIcon } from "@/lib/category-icons";
import { localePath, type Locale } from "@/i18n/config";
import type { CategoryOut } from "@/lib/api/types";

/**
 * Плитки категорій — вхід у каталог з головної (почерк jabko.ua).
 *
 * Три свідомі відмінності від того, що було:
 *
 * 1. ФОТО ТОВАРУ замість generic-іконки. Іконка «коробка» на трьох категоріях поспіль
 *    не несла жодної інформації — це був шум. `image_url` бекенд віддає з фолбеком на
 *    головне фото товару з категорії, тому плитка виглядає як полиця, а не як список.
 *    Іконка лишається запасним варіантом на випадок категорії без жодного фото.
 *
 * 2. БЕЗ ЛІЧИЛЬНИКІВ. «Кліматичне обладнання 0» — це антиреклама на головній сторінці:
 *    порожні категорії тепер узагалі не приходять з API, а писати «164» під плиткою
 *    магазину нічого не дає (це метрика складу, а не аргумент для покупця).
 *
 * 3. Порожній список → секції немає. Заголовка над порожнечею не буває.
 *
 * ⚠️ КОМПОНЕНТ ЗАГАЛЬНИЙ — і для коренів на головній, і для ПІДГРУП на сторінці
 * категорії. Замовниця попросила «фото-плитки для підгруп»: поля `image`/`icon` у
 * моделі спільні для всіх рівнів, і API вже віддавав `image_url` для підкатегорій —
 * але сайт малював їх текстовими чіпами, тож завантажена плитка нікуди не потрапляла.
 * Тому тут не копія компонента, а два параметри: `hrefFor` (у підгруп шлях глибший за
 * `/catalog/{slug}`) і `showCount` (у навігації вглиб лічильник корисний, на головній
 * він був антирекламою — див. пункт 2).
 */
export function CategoryTiles({
  categories,
  locale,
  title,
  hrefFor,
  showCount = false,
}: {
  categories: CategoryOut[];
  locale: Locale;
  title: string;
  /** Куди веде плитка. Типово — корінь каталогу. */
  hrefFor?: (category: CategoryOut) => string;
  /** Показувати кількість товарів (для підгруп — так, на головній — ні). */
  showCount?: boolean;
}) {
  if (categories.length === 0) return null;

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-h2 text-foreground">{title}</h2>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {categories.map((cat, i) => (
          <Link
            key={cat.id}
            href={hrefFor ? hrefFor(cat) : localePath(locale, `/catalog/${cat.slug}`)}
            className="group flex flex-col overflow-hidden rounded-lg border border-border bg-card transition-all duration-150 hover:-translate-y-0.5 hover:border-transparent hover:shadow-lg"
          >
            <div className="relative aspect-[4/3] w-full bg-muted/30">
              {cat.image_url ? (
                <Image
                  src={cat.image_url}
                  alt=""
                  fill
                  sizes="(max-width: 640px) 45vw, (max-width: 1024px) 30vw, 18vw"
                  className="object-contain p-4 transition-transform duration-200 group-hover:scale-[1.03]"
                  // Плитки — найвищий блок головної, перші дві потрапляють у LCP.
                  priority={i < 2}
                />
              ) : (
                <div className="flex size-full items-center justify-center">
                  <CategoryIcon
                    name={categoryIcon(cat.slug)}
                    className="size-10 text-muted-foreground"
                  />
                </div>
              )}
            </div>

            <span className="flex items-center justify-between gap-2 border-t border-border p-3 text-sm font-semibold text-foreground">
              {cat.name}
              {showCount && cat.products_count > 0 && (
                <span className="shrink-0 text-xs font-normal text-muted-foreground tnum">
                  {cat.products_count}
                </span>
              )}
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}
