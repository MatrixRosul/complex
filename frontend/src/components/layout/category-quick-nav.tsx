"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/**
 * РЯДОК РОЗДІЛІВ ПІД ШАПКОЮ — РІВНО ТЕ, ЩО ЗАМОВНИК ВІДМІТИВ В АДМІНЦІ.
 *
 * ⚠️ БУЛО: сюди автоматично падали ВСІ кореневі категорії. Замовник назвав це зайвим —
 * дерево вже є і в кнопці «Каталог», і в сайдбарі, тож рядок дублював навігацію замість
 * того, щоб вести до того, що треба продати. Тепер це кураторський список: галочка
 * «Показувати в рядку під шапкою» в картці категорії (Категорії → список, колонка
 * редагується прямо там).
 *
 * ⚠️ ЖОДНОГО ХАРДКОДУ НАЗВ. Замовник назвав «Акції», «Уцінений товар» і (пізніше)
 * «Комплекти» — але це ЗМІСТ, а не структура. Вписати їх сюди рядками означало б, що
 * перейменована в адмінці категорія лишиться зі старою назвою, а знята з галочки —
 * висітиме далі. Тут приходить готовий список з API.
 *
 * ⚠️ ПОРОЖНЬО → РЯДКА НЕМАЄ ВЗАГАЛІ. Це не деградація, а те саме правило, що й скрізь у
 * каталозі: порожня категорія не показується ніде. Тобто відмічена, але поки безтоварна
 * «Акції» не стане посиланням у глухий кут на найпомітнішому місці сайту — вона просто
 * з'явиться сама, щойно в ній будуть товари.
 *
 * ⚠️ ВИДНО НА ВСІХ ШИРИНАХ. На вузькому екрані рядок горизонтально прокручується
 * (`overflow-x-auto`); смуга прокрутки схована — під шапкою вона читається як зламана
 * верстка, хоча скрол тут штатний.
 *
 * ⚠️ АКТИВНИЙ РОЗДІЛ ВИДІЛЕНИЙ. Замовниця: «щоб якщо вибране щось із цього, то
 * підсвічувалось жирнішим текстом». Порівнюємо з ПОЧАТКОМ шляху, а не з рівністю:
 * усередині «Вбудована / Духові шафи» розділ верхнього рівня теж має лишатись
 * підсвіченим — інакше підсвітка гасне на першому ж кроці вглиб і виглядає як збій.
 * Заради цього компонент клієнтський (`usePathname`).
 */
export type QuickNavItem = {
  id: number;
  name: string;
  /** Готовий канонічний шлях — його будує серверний хедер по дереву категорій. */
  href: string;
};

export function CategoryQuickNav({ items }: { items: QuickNavItem[] }) {
  const pathname = usePathname();

  if (items.length === 0) return null;

  return (
    <nav aria-label="Розділи каталогу" className="border-t border-border">
      <div className="container-complex flex h-11 items-center gap-5 overflow-x-auto text-sm [-ms-overflow-style:none] [scrollbar-width:none] lg:gap-6 [&::-webkit-scrollbar]:hidden">
        {items.map((item) => {
          // `startsWith` + межа сегмента: «/catalog/velyka» не має підсвічувати
          // «/catalog/velyka-tehnika», якщо колись з'явиться схожий slug.
          const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);

          return (
            <Link
              key={item.id}
              href={item.href}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "whitespace-nowrap transition-colors",
                isActive
                  ? "font-bold text-foreground"
                  : "font-medium text-muted-foreground hover:text-foreground",
              )}
            >
              {item.name}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
