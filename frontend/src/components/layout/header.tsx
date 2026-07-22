import Image from "next/image";
import Link from "next/link";
import { MapPin } from "lucide-react";

import { api } from "@/lib/api";
import { safe } from "@/lib/api/safe";
import { emblemMap } from "@/lib/category-icons";
import { FALLBACK_CONTACTS } from "@/lib/site";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatPhone } from "@/lib/format";
import type { MenuItemOut } from "@/lib/api/types";
import { SideAd } from "@/components/cms/banner-slots";

import { CategoryMegaMenu } from "./category-mega-menu";
import { HeaderActions } from "./header-actions";
import { LocaleSwitcher } from "./locale-switcher";
import { MobileBurgerMenu } from "./mobile-burger-menu";
import { SearchBar } from "./search-bar";
import { ThemeToggle } from "./theme-toggle";

/**
 * Пункти ЦЕНТРУ верхньої смуги — порядок замовлений замовницею дослівно:
 * «Доставка та оплата», «Кредит або розстрочка», «Гарантія», «Контакти».
 *
 * ⚠️ Це ключі СТАТИЧНИХ СТОРІНОК (`StaticPage.key`), а не готові посилання, і список
 * тут — лише ПОРЯДОК. Сам пункт береться з /cms/menu (див. `api.getMenuItems`, який
 * добудовує меню зі списку реальних сторінок), тому:
 *   • назва приходить перекладена з адмінки — хардкодити її тут не можна;
 *   • якщо сторінки в БД НЕМАЄ — пункт просто не рендериться, і посилання в нікуди
 *     не з'являється.
 *
 * 🔴 Станом на 20.07.2026 у БД немає сторінки `credit` — «Кредит або розстрочка»
 *    в смузі не показується, доки замовник її не заведе в адмінці. Це не баг фронта.
 */
const TOPBAR_PAGE_KEYS = ["payment-delivery", "credit", "warranty", "contacts"];

/**
 * Хедер — серверний компонент: дерево категорій, меню й контакти тягнуться на сервері
 * і не роздувають клієнтський бандл. Інтерактив винесений у дочірні "use client".
 */
export async function Header({ locale }: { locale: Locale }) {
  const t = getT(locale);
  const lang = localeToApiLang[locale];

  // ⚠️ safe(): хедер — у layout, ВИЩЕ за error.tsx. Якщо бекенд моргнув, сайт має
  // лишитись клікабельним (лого, пошук, кошик), а не впасти цілком у global-error.
  const [categories, menuItems, contacts, banners] = await Promise.all([
    safe(api.getCategoryTree(lang), []),
    safe(api.getMenuItems(lang), []),
    safe(api.getContacts(lang), FALLBACK_CONTACTS(lang)),
    // Банери потрібні хедеру заради вузької реклами в dropdown каталогу (home_side):
    // dropdown відкривається з будь-якої сторінки, тож дані тягне саме хедер.
    safe(api.getBanners(lang), []),
  ]);

  // Вузька вертикальна реклама у відкритому dropdown — та сама, що в inline-сайдбарі
  // головної (home_side). Немає — колонки в dropdown просто не буде.
  const sideBanner = banners.find((b) => b.placement === "home_side");
  const sideAd = sideBanner ? <SideAd banner={sideBanner} locale={locale} /> : null;

  // Мапа емблем: external_id → {іконка, icon_url}. Передаємо готовою, щоб клієнтські
  // компоненти не тягли за собою ні дерево категорій, ні мапінг.
  const iconOf = emblemMap(categories);

  // Порядок — з TOPBAR_PAGE_KEYS, наявність — з реальних пунктів меню.
  const topbarLinks = TOPBAR_PAGE_KEYS.map((key) =>
    menuItems.find((item) => item.url === `/page/${key}`),
  ).filter((item): item is MenuItemOut => Boolean(item));

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur">
      {/* ── Верхня смуга: локація · інфо-сторінки · телефон + мова + тема ──
          Фон — брендовий темно-синій (референс denika.ua). Час роботи, який стояв тут
          раніше, прибраний свідомо: смуга тепер тримає три зони, і четверта не влазить
          без тісноти. Графік нікуди не подівся — він у футері й у бургер-меню. */}
      <div className="hidden bg-brand-deep text-brand-deep-foreground lg:block">
        <div className="container-complex flex h-11 items-center justify-between gap-6 text-sm">
          {/* Статична адреса магазину (INPUTS §1), а НЕ вибір міста: магазин один. */}
          <span className="flex shrink-0 items-center gap-1.5 font-medium">
            <MapPin aria-hidden className="size-4" />
            {t("topbar.location")}
          </span>

          {topbarLinks.length > 0 && (
            <nav aria-label={t("topbar.nav")} className="flex items-center gap-6">
              {topbarLinks.map((item) => (
                <Link
                  key={item.id}
                  href={localePath(locale, item.url)}
                  className="whitespace-nowrap text-white/85 transition-colors hover:text-white hover:underline"
                >
                  {item.title}
                </Link>
              ))}
            </nav>
          )}

          <div className="flex shrink-0 items-center gap-3">
            {contacts.phones.slice(0, 1).map((phone) => (
              <a
                key={phone}
                href={`tel:${phone}`}
                className="font-semibold tnum hover:underline"
              >
                {formatPhone(phone)}
              </a>
            ))}
            <LocaleSwitcher onBrand />
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
          {/*
           * ДВА ФАЙЛИ, А НЕ ФІЛЬТР. Раніше тут стояв один логотип з `dark:invert` — це
           * працювало, поки лого було чорним. Новий фірмовий знак СИНІЙ (#2E5F87), а
           * invert синього дає брудно-жовтий, тобто в темній темі бренд ламався б.
           * Заміна кольору фільтром для кольорового лого неможлива в принципі, тому
           * світлу версію намальовано окремим файлом.
           *
           * Перемикання класами (`dark:hidden` / `hidden dark:block`), а не через
           * useTheme: клас .dark стоїть на <html> ще ДО гідратації (інлайн-скрипт
           * next-themes), тож потрібна картинка видима з першого кадру — без спалаху
           * чужим логотипом і без розсинхрону сервер/клієнт.
           */}
          <Image
            src="/images/logo.png"
            alt={t("brand.name")}
            width={720}
            height={229}
            priority
            className="h-8 w-auto md:h-9 dark:hidden"
          />
          <Image
            src="/images/logo-light.png"
            alt=""
            aria-hidden
            width={720}
            height={229}
            priority
            className="hidden h-8 w-auto md:h-9 dark:block"
          />
        </Link>

        <div className="hidden lg:block">
          <CategoryMegaMenu categories={categories} iconOf={iconOf} sideAd={sideAd} />
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
