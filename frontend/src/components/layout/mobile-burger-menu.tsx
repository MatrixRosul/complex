"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ChevronLeft,
  ChevronRight,
  Heart,
  Info,
  LayoutGrid,
  Menu,
  Phone,
  Scale,
  ShoppingBag,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { formatPhone } from "@/lib/format";
import { useCompareStore } from "@/store/compare";
import { useWishlistStore } from "@/store/wishlist";
import { useHydrated } from "@/hooks/use-hydrated";
import type { CategoryOut, ContactsOut, MenuItemOut } from "@/lib/api/types";

import { CategoryIcon } from "./category-icon";
import { LocaleSwitcher } from "./locale-switcher";
import { ThemeToggle } from "./theme-toggle";

/**
 * Бургер-меню (DESIGN_SYSTEM §4.10, референс — скрін …_394).
 *
 * ⚠️ БЕЗ «Особистого кабінету» — його в проєкті НЕМАЄ. На референсі він є,
 * але замовник підтвердив: реєстрації покупця не буде, замовлення живуть
 * по публічному токену. Пункт свідомо викинутий.
 *
 * Склад (точно за скріном):
 *   Каталог › · Мої бажання › · Порівняння › · Інформація › · Покупцям ›
 *   + Час роботи · Контакти · перемикачі мови й теми
 *
 * ⚠️ «Каталог ›» відкриває ДРУГИЙ РІВЕНЬ УСЕРЕДИНІ ТОГО Ж Sheet (slide-in),
 * а не вкладений Sheet: вкладені діалоги ламають фокус-трап і кнопку «Назад» на Android.
 */
export function MobileBurgerMenu({
  categories,
  menuItems,
  contacts,
  iconOf,
}: {
  categories: CategoryOut[];
  menuItems: MenuItemOut[];
  contacts: ContactsOut;
  iconOf: Record<string, string>;
}) {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<"root" | "catalog" | "info" | "buyers">("root");

  const wishCount = useWishlistStore((s) => s.ids.length);
  const compareCount = useCompareStore((s) => s.ids.length);

  const close = () => {
    setOpen(false);
    // Скидаємо рівень із затримкою — щоб під час анімації закриття
    // меню не «перескакувало» назад на корінь на очах у користувача.
    setTimeout(() => setLevel("root"), 200);
  };

  const infoItems = menuItems.filter((m) => m.block === "info");
  const buyersItems = menuItems.filter((m) => m.block === "buyers");

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setTimeout(() => setLevel("root"), 200);
      }}
    >
      <SheetTrigger
        render={
          <Button variant="ghost" size="icon-lg" aria-label={t("nav.menu")} className="lg:hidden">
            <Menu className="size-5" />
          </Button>
        }
      />

      <SheetContent side="left" className="w-[min(88vw,380px)] gap-0 overflow-y-auto p-0">
        <SheetTitle className="sr-only">{t("nav.menu")}</SheetTitle>

        {level === "root" ? (
          <div className="flex flex-col">
            <MenuRow
              icon={<LayoutGrid className="size-5" />}
              label={t("nav.catalog")}
              onClick={() => setLevel("catalog")}
            />
            <MenuRow
              icon={<Heart className="size-5" />}
              label={t("nav.wishlist")}
              badge={hydrated && wishCount > 0 ? wishCount : undefined}
              href={localePath(locale, "/wishlist")}
              onClick={close}
            />
            <MenuRow
              icon={<Scale className="size-5" />}
              label={t("nav.compare")}
              badge={hydrated && compareCount > 0 ? compareCount : undefined}
              href={localePath(locale, "/compare")}
              onClick={close}
            />
            <MenuRow
              icon={<Info className="size-5" />}
              label={t("nav.info")}
              onClick={() => setLevel("info")}
            />
            <MenuRow
              icon={<ShoppingBag className="size-5" />}
              label={t("nav.forBuyers")}
              onClick={() => setLevel("buyers")}
            />

            {/* ── Час роботи (INPUTS §4) ─────────────────────────────── */}
            <section className="border-b border-border p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("nav.workingHours")}
              </h2>
              <dl className="mt-3 space-y-1.5">
                {contacts.working_hours.map((wh) => (
                  <div key={wh.days} className="flex items-center justify-between text-sm">
                    <dt className="text-muted-foreground">{wh.days}</dt>
                    <dd className="font-medium text-foreground tnum">{wh.time}</dd>
                  </div>
                ))}
              </dl>
            </section>

            {/* ── Контакти: телефони як tel: посилання ───────────────── */}
            <section className="border-b border-border p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("nav.contacts")}
              </h2>
              <ul className="mt-3 space-y-2">
                {contacts.phones.map((phone) => (
                  <li key={phone}>
                    <a
                      href={`tel:${phone}`}
                      className="flex items-center gap-2 text-sm font-medium text-foreground tnum"
                    >
                      <Phone aria-hidden className="size-4 text-muted-foreground" />
                      {formatPhone(phone)}
                    </a>
                  </li>
                ))}
              </ul>
              <a
                href={`mailto:${contacts.email}`}
                className="mt-3 block text-sm text-muted-foreground underline"
              >
                {contacts.email}
              </a>
            </section>

            <div className="flex items-center justify-between p-4">
              <LocaleSwitcher />
              <ThemeToggle />
            </div>
          </div>
        ) : level === "catalog" ? (
          <SubLevel title={t("nav.catalog")} onBack={() => setLevel("root")}>
            {categories.map((cat) => (
              <Link
                key={cat.id}
                href={localePath(locale, `/catalog/${cat.slug}`)}
                onClick={close}
                className="flex h-14 items-center gap-3 border-b border-border px-4 text-base text-foreground"
              >
                <CategoryIcon name={iconOf[cat.external_id] ?? "package"} />
                <span className="flex-1">{cat.name}</span>
                <span className="text-xs text-muted-foreground tnum">
                  {cat.products_count}
                </span>
              </Link>
            ))}
          </SubLevel>
        ) : (
          <SubLevel
            title={level === "info" ? t("nav.info") : t("nav.forBuyers")}
            onBack={() => setLevel("root")}
          >
            {(level === "info" ? infoItems : buyersItems).map((item) => (
              <Link
                key={item.id}
                href={localePath(locale, item.url)}
                onClick={close}
                className="flex h-14 items-center border-b border-border px-4 text-base text-foreground"
              >
                {item.title}
              </Link>
            ))}
          </SubLevel>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** Рядок меню: 56px, іконка 20px, назва, лічильник/шеврон справа. */
function MenuRow({
  icon,
  label,
  badge,
  href,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  badge?: number;
  href?: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="text-muted-foreground">{icon}</span>
      <span className="flex-1 text-base text-foreground">{label}</span>
      {badge !== undefined && (
        <span className="rounded-full bg-contrast px-2 py-0.5 text-xs font-semibold text-contrast-foreground tnum">
          {badge}
        </span>
      )}
      <ChevronRight aria-hidden className="size-4 text-muted-foreground" />
    </>
  );

  const className = cn(
    "flex h-14 w-full items-center gap-3 border-b border-border px-4 text-left",
  );

  return href ? (
    <Link href={href} onClick={onClick} className={className}>
      {content}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={className}>
      {content}
    </button>
  );
}

/** Другий рівень усередині того ж Sheet — slide-in вправо + кнопка «‹ Назад». */
function SubLevel({
  title,
  onBack,
  children,
}: {
  title: string;
  onBack: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="flex animate-in flex-col slide-in-from-right-4 duration-200">
      <button
        type="button"
        onClick={onBack}
        className="flex h-14 items-center gap-2 border-b border-border px-4 text-base font-semibold text-foreground"
      >
        <ChevronLeft aria-hidden className="size-5" />
        {title}
      </button>
      {children}
    </div>
  );
}
