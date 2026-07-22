import Link from "next/link";

import { localePath, type Locale } from "@/i18n/config";
import type { CategoryOut } from "@/lib/api/types";

/**
 * РЯДОК КАТЕГОРІЙ ПІД ШАПКОЮ — швидкий вхід у розділи без відкривання каталогу.
 *
 * ⚠️ Категорії беруться з БД, а НЕ хардкодяться списком. Замовник назвав приблизний
 * перелік («Аудіо-відео, Вбудована, Велика побутова…»), але вписати його рядками
 * означало б, що доданий в адмінці розділ сюди не потрапить, а перейменований —
 * лишиться зі старою назвою. Тут той самий масив, що й у меню каталогу.
 *
 * ⚠️ Тільки з lg: на вузьких екранах дев'ять пунктів або переносяться в три ряди,
 * або перетворюються на горизонтальний скрол, який на телефоні майже ніхто не
 * прокручує. Мобільний вхід у каталог — бургер.
 *
 * `overflow-x-auto` лишається як страховка: якщо назви категорій колись стануть
 * довшими, рядок прокрутиться, а не поламає шапку.
 */
export function CategoryQuickNav({
  categories,
  locale,
}: {
  categories: CategoryOut[];
  locale: Locale;
}) {
  if (categories.length === 0) return null;

  return (
    <nav
      aria-label="Розділи каталогу"
      className="hidden border-t border-border lg:block"
    >
      <div className="container-complex flex h-11 items-center gap-6 overflow-x-auto text-sm">
        {categories.map((category) => (
          <Link
            key={category.id}
            href={localePath(locale, `/catalog/${category.slug}`)}
            className="whitespace-nowrap font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {category.name}
          </Link>
        ))}
      </div>
    </nav>
  );
}
