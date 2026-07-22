import Image from "next/image";
import Link from "next/link";
import { Clock, MapPin } from "lucide-react";

import { api } from "@/lib/api";
import { safe } from "@/lib/api/safe";
import { emblemMap } from "@/lib/category-icons";
import { FALLBACK_CONTACTS } from "@/lib/site";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { formatPhone } from "@/lib/format";
import { slugPathIn } from "@/lib/catalog-path";
import type { MenuItemOut } from "@/lib/api/types";
import { SideAd } from "@/components/cms/banner-slots";

import { CategoryMegaMenu } from "./category-mega-menu";
import { CategoryQuickNav } from "./category-quick-nav";
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
  const [categories, quickNav, menuItems, contacts, banners] = await Promise.all([
    safe(api.getCategoryTree(lang), []),
    // Рядок під шапкою — НЕ підмножина дерева: замовник відмічає категорії галочкою в
    // адмінці, і серед них бувають підкатегорії та віртуальні («Акції», «Уцінка»).
    safe(api.getQuickNav(lang), []),
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

  /**
   * ЦЕНТР ВЕРХНЬОЇ СМУГИ — ЦІЛКОМ З АДМІНКИ (зона «Верхнє меню», порядок — поле «Порядок»).
   *
   * 🔴 Було: захардкоджений список із чотирьох ключів сторінок, по якому фронт ФІЛЬТРУВАВ
   *    пункти меню. Замовник додавав п'ятий пункт в адмінці — і той не з'являвся ніде,
   *    без жодної помилки. Саме це він і спитав («ще два пункти зверху додаються?»).
   *    Тепер список тут не живе взагалі: що заведено в зоні header — те й видно.
   *
   * ⚠️ Пункти зі ЩЕ порожнім `url` пропускаємо: у моделі це «пункт-заголовок розділу»
   *    (він осмислений у бургер-меню, але в горизонтальній смузі був би мертвим текстом).
   */
  const topbarLinks = menuItems.filter(
    (item): item is MenuItemOut => item.block === "header" && Boolean(item.url),
  );

  // Рядок під шапкою приходить ПЛОСКИМ (там можуть бути й підкатегорії), а посилання на
  // підкатегорію мусить бути повним — `/catalog/vbudovana/dukhovi-shafy`, як у мегаменю.
  // Шлях будуємо по дереву, яке хедер уже отримав; фолбек на власний slug — на випадок,
  // коли вузла в дереві чомусь немає (тоді резолвер каталогу все одно знайде його по
  // останньому сегменту).
  const quickNavItems = quickNav.map((category) => ({
    id: category.id,
    name: category.name,
    href: localePath(
      locale,
      `/catalog/${(slugPathIn(categories, category.external_id) ?? [category.slug]).join("/")}`,
    ),
  }));

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur">
      {/* ── Верхня смуга: локація · інфо-сторінки · телефон + мова + тема ──
          Фон — брендовий темно-синій (референс denika.ua). Час роботи, який стояв тут
          раніше, прибраний свідомо: смуга тепер тримає три зони, і четверта не влазить
          без тісноти. Графік нікуди не подівся — він у футері й у бургер-меню. */}
      <div className="hidden bg-brand-deep text-brand-deep-foreground lg:block">
        {/* 1.05rem = 16.8 px — рівно +20% до попереднього text-sm (14 px), на прохання
            замовника. Іконки й висота смуги підняті пропорційно, інакше текст починає
            тиснути на межі смуги, а 16-піксельні іконки поруч виглядають дрібними. */}
        <div className="container-complex flex h-12 items-center justify-between gap-6 text-[1.05rem]">
          {/* Статична адреса магазину (INPUTS §1), а НЕ вибір міста: магазин один.
              Поруч — графік: він у контактах уже є, а покупцю важливо бачити «зараз
              відчинено?» до того, як він набере номер. Крапка — просто маркер, НЕ
              індикатор «зараз працює»: рахувати це чесно треба з урахуванням свят,
              а вигадувати зелений вогник, який світить у неділю о 23:00, гірше,
              ніж не показувати його взагалі. */}
          <div className="flex shrink-0 items-center gap-4">
            {/* Адреса — з адмінки (Налаштування сайту → Адреса магазину), а не зі словника
                i18n. Було: захардкоджене «Ужгород», яке замовник не міг змінити ніде. */}
            {contacts.address && (
              <span className="flex items-center gap-1.5 font-medium">
                <MapPin aria-hidden className="size-[1.2rem]" />
                {contacts.address}
              </span>
            )}
            {contacts.working_hours.length > 0 && (
              <span className="hidden items-center gap-1.5 text-white/85 xl:flex">
                <Clock aria-hidden className="size-[1.2rem]" />
                {contacts.working_hours.map((wh) => `${wh.days} ${wh.time}`).join(" · ")}
              </span>
            )}
          </div>

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

      {/* ── Основний рядок ──────────────────────────────────────────────
          ⚠️ h-24 (96 px) замість початкових h-16. Рядок «губився під стрічкою»:
          синя смуга вгорі темна й контрастна, тому при близькій висоті око
          читало головною її, а не логотип із пошуком.
          ⚠️ ЦИФРА ПРОЙШЛА ТРИ ІТЕРАЦІЇ — не міняти її «на око». 80 px здалось
          завеликим («щоб в екран вміщалось»), 72 — замалим; замовник двічі
          попросив ЩЕ більше, тому 96. Ціна відома і прийнята свідомо: шапка
          липка, тож ці пікселі з'їдають видиму частину КОЖНОЇ сторінки.
          Далі рости нікуди — на ноутбуці 768 px разом зі смугою і рядком
          розділів це вже понад чверть екрана. */}
      <div className="container-complex flex h-24 items-center gap-4">
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
           * працювало, поки лого було чорним. Новий фірмовий знак СИНІЙ (#144772), а
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
            height={222}
            priority
            className="h-12 w-auto md:h-14 dark:hidden"
          />
          <Image
            src="/images/logo-light.png"
            alt=""
            aria-hidden
            width={720}
            height={222}
            priority
            className="hidden h-12 w-auto md:h-14 dark:block"
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

      {/* ── Рядок розділів: те, що замовник відмітив в адмінці (порожньо → рядка немає) ── */}
      <CategoryQuickNav items={quickNavItems} />

      {/* Пошук на мобільному — окремим рядком, бо в один рядок не влазить. */}
      <div className="container-complex pb-3 md:hidden">
        <SearchBar />
      </div>
    </header>
  );
}
