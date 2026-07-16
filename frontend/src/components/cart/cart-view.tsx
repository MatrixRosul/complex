"use client";

import Image from "next/image";
import Link from "next/link";
import { AlertTriangle, ImageOff, Minus, Plus, Trash2, X } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { formatPrice } from "@/lib/format";
import { useCartStore } from "@/store/cart";
import { useCartPreview } from "@/hooks/use-cart-preview";
import { useHydrated } from "@/hooks/use-hydrated";
import { AvailabilityBadge } from "@/components/product/availability-badge";
import { ApiErrorState } from "@/components/api-error";

/**
 * Кошик.
 *
 * ⚠️ ВСЕ, що тут показано (назва, фото, ЦІНА, наявність, підсумок), приходить з
 * POST /api/cart/preview. У localStorage лежать голі {id, qty} — див. store/cart.ts.
 * Тому кошик не може показати протухлу ціну навіть теоретично: показувати нема чого,
 * поки бекенд не відповів.
 */
export function CartView() {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const lines = useCartStore((s) => s.lines);
  const setQty = useCartStore((s) => s.setQty);
  const remove = useCartStore((s) => s.remove);
  const clear = useCartStore((s) => s.clear);

  const { data, isLoading, error, removedItems, dismissRemoved, reload } = useCartPreview();

  // До гідратації localStorage недоступний — показуємо скелет, а не «порожньо».
  if (!hydrated) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }

  if (lines.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 p-12 text-center">
        <p className="text-h3 text-foreground">{t("cart.empty")}</p>
        <p className="mt-2 text-sm text-muted-foreground">{t("cart.emptyHint")}</p>
        <Link
          href={localePath(locale, "/catalog")}
          className={cn(buttonVariants({ size: "xl" }), "mt-6")}
        >
          {t("cart.goToCatalog")}
        </Link>
      </div>
    );
  }

  /**
   * ⚠️ Помилка bulk-запиту НЕ МОЖЕ виглядати як порожній кошик.
   * У localStorage лежать голі {id, qty}, а ціни й назви — тільки з сервера. Якщо сервер
   * не відповів, показувати нема чого: або кажемо «недоступно» і даємо «Спробувати ще раз»,
   * або людина бачить «Кошик порожній» і йде, вважаючи, що втратила свої товари.
   */
  if (error) return <ApiErrorState onRetry={reload} />;

  const items = data?.items ?? [];

  return (
    /**
     * ⚠️ `min-w-0` на ОБОХ дітях гріда — не косметика, а фікс горизонтального скролу.
     *
     * Grid-айтем за замовчуванням має `min-width: auto`, тобто НЕ МОЖЕ стати вужчим за
     * min-content свого вмісту. min-content рядка кошика — 360px (фото 96 + лічильник
     * кількості + колонка ціни 84). На iPhone SE (320px) контейнер дає 288px — і колонка
     * гріда роздувалась до 360px, тягнучи за собою <body>: документ отримував scrollWidth
     * 376 при viewport 320. Уся сторінка (шапка, футер, підсумок) їздила вбік.
     * Виміряно в браузері, не на око: grid-template-columns резолвився в «360px».
     */
    <div className="grid gap-8 lg:grid-cols-[1fr_360px]">
      <div className="flex min-w-0 flex-col gap-3">
        {/* Позиції зникли з каталогу — попереджаємо, не мовчимо.
            ⚠️ Джерело — removedItems (липкий стан хука), а НЕ data.unavailable_items:
            останній обнуляється наступним же перезапитом, і банер зникав раніше, ніж
            людина встигала його прочитати (товар при цьому з кошика справді зникав).
            Тому банер живе, доки його не закриють. */}
        {removedItems.length > 0 && (
          <div
            role="status"
            className="flex items-start gap-2 rounded-lg border border-border bg-stock-out-bg p-3"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-stock-out" />
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground">{t("cart.unavailable")}</p>
              <p className="text-xs text-muted-foreground">{t("cart.unavailableHint")}</p>
            </div>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("common.close")}
              onClick={dismissRemoved}
            >
              <X className="size-4" />
            </Button>
          </div>
        )}

        {isLoading && items.length === 0
          ? lines.map((l) => <Skeleton key={l.id} className="h-28 w-full" />)
          : items.map((item) => (
              <article
                key={item.id}
                className={cn(
                  // `flex-wrap`: на вузьких екранах колонка ціни переїжджає на власний
                  // рядок. Без цього рядок кошика не міг стати вужчим за 360px (фото 96 +
                  // лічильник кількості ~140 + ціна 84 + падінги), і на 320px сторінка
                  // отримувала горизонтальний скрол. З ≥640px (sm) верстка як була: усе в рядок.
                  "flex flex-wrap gap-3 rounded-lg border border-border bg-card p-3",
                  isLoading && "opacity-60",
                )}
              >
                <Link
                  href={localePath(locale, `/p/${item.id}/${item.slug}`)}
                  className="relative size-24 shrink-0"
                >
                  {item.main_image_url ? (
                    <Image
                      src={item.main_image_url}
                      alt={item.name}
                      fill
                      sizes="96px"
                      className="object-contain"
                    />
                  ) : (
                    <div className="flex size-full items-center justify-center rounded bg-muted">
                      <ImageOff aria-hidden className="size-5 text-muted-foreground" />
                    </div>
                  )}
                </Link>

                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <Link
                    href={localePath(locale, `/p/${item.id}/${item.slug}`)}
                    className="line-clamp-2 text-sm font-medium text-foreground hover:underline"
                  >
                    {item.name}
                  </Link>

                  <span className="product-code text-xs text-muted-foreground">
                    {item.sku}
                  </span>

                  <AvailabilityBadge availability={item.availability} />

                  <div className="mt-auto flex flex-wrap items-center gap-3">
                    {/* Кількість.
                        ⚠️ aria-label — словами, а не «+» / «−»: скрінрідер зачитував
                        «плюс», не кажучи ні до чого він, ні що це кількість товару. */}
                    <div className="flex shrink-0 items-center rounded-md border border-input">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("cart.decrease")}
                        disabled={item.qty <= 1}
                        onClick={() => setQty(item.id, item.qty - 1)}
                      >
                        <Minus className="size-3" />
                      </Button>
                      <span
                        aria-label={t("cart.quantity")}
                        className="w-8 text-center text-sm font-medium tnum"
                      >
                        {item.qty}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("cart.increase")}
                        onClick={() => setQty(item.id, item.qty + 1)}
                      >
                        <Plus className="size-3" />
                      </Button>
                    </div>

                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t("cart.remove")}
                      onClick={() => remove(item.id)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </div>

                {/* shrink-0 + min-w: на 320px (iPhone SE) фото 96px + gap лишали ~200px на
                    дві колонки, і 5-значна ціна («51 879 ₴» — типова для великої техніки)
                    підтискалась або переносилась. Стискатись має назва (min-w-0 flex-1),
                    а не ціна. */}
                <div className="flex w-full min-w-[84px] shrink-0 flex-row items-baseline justify-end gap-2 sm:w-auto sm:flex-col sm:items-end sm:justify-between">
                  {/* Ціна з БД, не з localStorage. */}
                  <span
                    className={cn(
                      "price text-price-lg",
                      item.old_price ? "text-price" : "text-price-regular",
                    )}
                  >
                    {formatPrice(item.line_total, locale)} ₴
                  </span>

                  {item.qty > 1 && (
                    <span className="text-xs text-muted-foreground tnum">
                      {formatPrice(item.price, locale)} ₴ × {item.qty}
                    </span>
                  )}
                </div>
              </article>
            ))}

        <Button
          variant="ghost"
          size="sm"
          className="w-fit text-muted-foreground"
          onClick={clear}
        >
          <Trash2 className="size-4" />
          {t("cart.clear")}
        </Button>
      </div>

      {/* ── Підсумок ─────────────────────────────────────────────────── */}
      <aside className="h-fit min-w-0 rounded-lg border border-border bg-card p-5 lg:sticky lg:top-32">
        <h2 className="text-h3 text-foreground">{t("cart.total")}</h2>

        <dl className="mt-4 flex flex-col gap-2 text-sm">
          <div className="flex justify-between">
            <dt className="text-muted-foreground">{t("cart.subtotal")}</dt>
            <dd className="font-medium text-foreground tnum">
              {isLoading ? (
                <Skeleton className="h-5 w-20" />
              ) : (
                `${formatPrice(data?.subtotal ?? "0", locale)} ₴`
              )}
            </dd>
          </div>

          <div className="flex justify-between">
            <dt className="text-muted-foreground">{t("cart.delivery")}</dt>
            <dd className="text-muted-foreground">{t("cart.deliveryByCarrier")}</dd>
          </div>
        </dl>

        <div className="mt-4 flex items-baseline justify-between border-t border-border pt-4">
          <span className="text-sm text-muted-foreground">{t("cart.total")}</span>
          <span className="price text-price-xl text-price-regular">
            {formatPrice(data?.subtotal ?? "0", locale)} ₴
          </span>
        </div>

        <Link
          href={localePath(locale, "/checkout")}
          className={cn(buttonVariants({ size: "xl" }), "mt-5 w-full")}
        >
          {t("cart.checkout")}
        </Link>

        {/* installment_allowed — AND по всіх позиціях, рахує СЕРВЕР. */}
        {data?.installment_allowed && (
          <p className="mt-3 text-center text-xs text-muted-foreground">
            {t("checkout.paymentMethod.installment")}
          </p>
        )}
      </aside>
    </div>
  );
}
