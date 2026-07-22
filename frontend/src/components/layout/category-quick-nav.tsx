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
 * ⚠️ ВИДНО НА ВСІХ ШИРИНАХ — і це виправлення, а не недогляд. Спочатку рядок стояв
 * під `lg:`, і виходила діра: на десктопі категорії показувались ДВІЧІ (тут і в
 * <CatalogSidebar>), а на телефоні — ЖОДНОГО разу, бо плитки категорій тимчасово
 * вимкнені, а сайдбар теж лише з lg. Тобто на одному брейкпоінті все, на іншому
 * нічого — замовник це й побачив як «то так, то так».
 *
 * Тепер це ЄДИНИЙ вхід у розділи, який є завжди: на вузькому екрані рядок
 * горизонтально прокручується (`overflow-x-auto`), на широкому просто вміщається.
 * Прокрутка схована стилями нижче — смуга під шапкою виглядала б як помилка.
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
    <nav aria-label="Розділи каталогу" className="border-t border-border">
      <div
        // `[scrollbar-width:none]` + webkit-правило: горизонтальна смуга під шапкою
        // читається як зламана верстка, хоча скрол тут штатний.
        className="container-complex flex h-11 items-center gap-5 overflow-x-auto text-sm [-ms-overflow-style:none] [scrollbar-width:none] lg:gap-6 [&::-webkit-scrollbar]:hidden"
      >
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
