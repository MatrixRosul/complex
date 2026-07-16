import Image from "next/image";
import Link from "next/link";
import { Clock } from "lucide-react";

import { api } from "@/lib/api";
import { safe } from "@/lib/api/safe";
import { iconMap } from "@/lib/category-icons";
import { FALLBACK_CONTACTS } from "@/lib/site";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatPhone } from "@/lib/format";

import { CategoryMegaMenu } from "./category-mega-menu";
import { HeaderActions } from "./header-actions";
import { LocaleSwitcher } from "./locale-switcher";
import { MobileBurgerMenu } from "./mobile-burger-menu";
import { SearchBar } from "./search-bar";
import { ThemeToggle } from "./theme-toggle";

/**
 * Хедер — серверний компонент: дерево категорій, меню й контакти тягнуться на сервері
 * і не роздувають клієнтський бандл. Інтерактив винесений у дочірні "use client".
 */
export async function Header({ locale }: { locale: Locale }) {
  const t = getT(locale);
  const lang = localeToApiLang[locale];

  // ⚠️ safe(): хедер — у layout, ВИЩЕ за error.tsx. Якщо бекенд моргнув, сайт має
  // лишитись клікабельним (лого, пошук, кошик), а не впасти цілком у global-error.
  const [categories, menuItems, contacts] = await Promise.all([
    safe(api.getCategoryTree(lang), []),
    safe(api.getMenuItems(lang), []),
    safe(api.getContacts(lang), FALLBACK_CONTACTS(lang)),
  ]);

  // Мапа емблем: external_id → ключ іконки. Передаємо готовою, щоб клієнтські
  // компоненти не тягли за собою мапінг.
  const iconOf = iconMap(categories);

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur">
      {/* ── Верхня смуга: час роботи + телефон + мова + тема ─────────── */}
      <div className="hidden border-b border-border lg:block">
        <div className="container-complex flex h-9 items-center justify-between text-xs">
          <div className="flex items-center gap-4 text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Clock aria-hidden className="size-3.5" />
              {contacts.working_hours.map((wh) => `${wh.days} ${wh.time}`).join(" · ")}
            </span>
          </div>

          <div className="flex items-center gap-3">
            {contacts.phones.slice(0, 1).map((phone) => (
              <a
                key={phone}
                href={`tel:${phone}`}
                className="font-medium text-foreground tnum hover:underline"
              >
                {formatPhone(phone)}
              </a>
            ))}
            <LocaleSwitcher />
            <ThemeToggle />
          </div>
        </div>
      </div>

      {/* ── Основний рядок ────────────────────────────────────────────── */}
      <div className="container-complex flex h-16 items-center gap-3">
        <MobileBurgerMenu
          categories={categories}
          menuItems={menuItems}
          contacts={contacts}
          iconOf={iconOf}
        />

        <Link
          href={localePath(locale)}
          className="flex shrink-0 items-center"
          aria-label={t("brand.name")}
        >
          {/* Логотип чорний на прозорому фоні → dark:invert робить його білим у темній темі. */}
          <Image
            src="/images/logo.png"
            alt={t("brand.name")}
            width={1031}
            height={319}
            priority
            className="h-8 w-auto md:h-9 dark:invert"
          />
        </Link>

        <div className="hidden lg:block">
          <CategoryMegaMenu categories={categories} iconOf={iconOf} />
        </div>

        <SearchBar className="mx-2 hidden flex-1 md:flex" />

        <div className="ml-auto flex items-center gap-1">
          <HeaderActions />
        </div>
      </div>

      {/* Пошук на мобільному — окремим рядком, бо в один рядок не влазить. */}
      <div className="container-complex pb-3 md:hidden">
        <SearchBar />
      </div>
    </header>
  );
}
