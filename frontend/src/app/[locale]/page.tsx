import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { BannerCard } from "@/components/cms/banner-card";
import { BrandStrip } from "@/components/home/brand-strip";
import { CategoryTiles } from "@/components/home/category-tiles";
import { RecentlyViewed } from "@/components/home/recently-viewed";
import { TrustStrip } from "@/components/home/trust-strip";
import { RelatedProducts } from "@/components/product/related-products";
import { api } from "@/lib/api";
import { getT } from "@/i18n/dictionary";
import { localePath, localeToApiLang, type Locale } from "@/i18n/config";
import { cn } from "@/lib/utils";
import type { CollectionKey } from "@/lib/api/types";
import type { TranslationKey } from "@/i18n/dictionary";

/** ISR: ADR-023 — time-based fallback поверх on-demand revalidateTag після синку. */
export const revalidate = 600;

/**
 * Добірки ЗА ПРАПОРЦЕМ товару. Заголовок мусить точно відповідати полю, за яким добірка
 * зібрана: «Хіти» = is_featured, «Акції» = є справжня стара ціна, «Новинки» = created_at,
 * «Уцінка» = condition ≠ «Новий». Придумувати тут маркетинг — означає обіцяти те, чого
 * в базі немає (саме за це знесено старі демо-банери).
 *
 * ⚠️ ЗАРАЗ ТРИ З ЧОТИРЬОХ ПОРОЖНІ: у БД 0 товарів з is_featured, 0 зі старою ціною і 0
 * з уцінкою — тобто малюються лише «Новинки». Тому головну несуть НЕ ці добірки, а секції
 * по категоріях (`by_category`) нижче: вони тримаються на самому каталозі й наповнені
 * завжди. Щойно замовник поставить галочку в адмінці — блок з'явиться сам.
 *
 * «Акції» перед «Хітами» навмисно: знижка — найсильніший привід клікнути.
 */
const FLAG_SECTIONS: { key: CollectionKey; title: TranslationKey; href: string }[] = [
  { key: "sale", title: "home.sale", href: "/catalog" },
  { key: "featured", title: "home.bestsellers", href: "/catalog" },
  { key: "new", title: "home.newArrivals", href: "/catalog" },
  { key: "clearance", title: "home.clearance", href: "/catalog" },
];

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: Locale }>;
}) {
  const { locale } = await params;
  const t = getT(locale);
  const lang = localeToApiLang[locale];

  const [banners, categories, collections, brands] = await Promise.all([
    api.getBanners(lang),
    api.getCategoryTree(lang),
    api.getCollections(lang),
    api.getBrands(lang, 12),
  ]);

  /**
   * ⚠️ БАНЕРІВ У БАЗІ НЕМАЄ — І ЦЕ ШТАТНИЙ СТАН. Три демо-банери («Вбудована техніка Bosch /
   * знижка до 15%») були вигадкою розробника: таких акцій у магазині не існує. Модель,
   * адмінка і GET /cms/banners лишились — замовник заведе банер під СПРАВЖНЮ акцію, і він
   * з'явиться тут сам. Поки банерів нема — секції просто немає, головна починається з
   * категорій (жодної діри в макеті, жодної заглушки).
   */
  const hero = banners.find((b) => b.placement === "home_hero") ?? banners[0];
  const rest = banners.filter((b) => b.id !== hero?.id);

  return (
    <div className="container-complex flex flex-col gap-12 py-6">
      {/* ── Банери (лише якщо замовник їх завів) ─────────────────────── */}
      {hero && (
        <section className="grid gap-4 lg:grid-cols-3">
          <BannerCard
            banner={hero}
            locale={locale}
            priority
            sizes="(max-width: 1024px) 100vw, 66vw"
            className="group min-h-64 p-6 lg:col-span-2 lg:min-h-80"
          >
            <h1 className="text-display banner-text">{hero.title}</h1>
            <p className="max-w-md text-base banner-text-muted">{hero.subtitle}</p>
            {hero.cta_label && (
              <span
                className={cn(buttonVariants({ variant: "default", size: "xl" }), "mt-2 w-fit")}
              >
                {hero.cta_label}
              </span>
            )}
          </BannerCard>

          {rest.length > 0 && (
            <div className="grid gap-4">
              {rest.slice(0, 2).map((banner) => (
                <BannerCard
                  key={banner.id}
                  banner={banner}
                  locale={locale}
                  sizes="(max-width: 1024px) 100vw, 33vw"
                  className="min-h-32 p-4"
                >
                  <h2 className="text-h3 banner-text">{banner.title}</h2>
                  <p className="mt-1 text-sm banner-text-muted">{banner.subtitle}</p>
                </BannerCard>
              ))}
            </div>
          )}
        </section>
      )}

      {/* ── Категорії: плитки з фото товару, без лічильників ──────────── */}
      <CategoryTiles categories={categories} locale={locale} title={t("home.categories")} />

      {/* ── Бренди: вхід у каталог для тих, хто прийшов «за Bosch» ────── */}
      <BrandStrip brands={brands} locale={locale} title={t("home.brands")} />

      {/* ── Секції по категоріях ──────────────────────────────────────────
          Головний блок сторінки: наповнений завжди, бо збирається з каталогу, а не
          з галочок замовника. Заголовок веде В САМУ КАТЕГОРІЮ (а не в загальний
          каталог) — інакше «Дивитись усі» під заголовком «Вбудована техніка» вело б
          у список з усіма 314 товарами, і людина губила б контекст. */}
      {collections.by_category.map((section) => (
        <RelatedProducts
          key={section.id}
          title={section.name}
          products={section.products}
          action={
            <Link
              href={localePath(locale, `/catalog/${section.slug}`)}
              className="text-sm text-primary hover:underline"
            >
              {t("home.toCategory")}
            </Link>
          }
        />
      ))}

      {/* ── Добірки за прапорцем ──────────────────────────────────────────
          ⚠️ ПОРОЖНЯ ДОБІРКА = БЛОКУ НЕМАЄ. Не заголовок над порожнечею, не скелет,
             не «товари скоро з'являться» — блок просто не рендериться. */}
      {FLAG_SECTIONS.map(({ key, title, href }) => {
        const products = collections[key];
        if (products.length === 0) return null;

        return (
          <RelatedProducts
            key={key}
            title={t(title)}
            products={products}
            action={
              <Link
                href={localePath(locale, href)}
                className="text-sm text-primary hover:underline"
              >
                {t("home.showAll")}
              </Link>
            }
          />
        );
      })}

      {/* ── Ви переглядали ───────────────────────────────────────────────
          Клієнтський блок: id лежать у localStorage, ціни тягне /products/bulk.
          Порожня історія → компонент повертає null, і блоку немає взагалі. */}
      <RecentlyViewed />

      {/* ── Смуга довіри: замикає сторінку перед футером ──────────────── */}
      <TrustStrip locale={locale} />
    </div>
  );
}
