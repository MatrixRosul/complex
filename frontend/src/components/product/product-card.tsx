"use client";

import Image from "next/image";
import Link from "next/link";
import { Heart, ImageOff, Scale } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { discountPercent } from "@/lib/format";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { useCartStore } from "@/store/cart";
import { useCompareStore, COMPARE_LIMIT } from "@/store/compare";
import { useWishlistStore } from "@/store/wishlist";
import { useHydrated } from "@/hooks/use-hydrated";
import type { ProductListItem } from "@/lib/api/types";

import { AvailabilityBadge } from "./availability-badge";
import { Price } from "./price";
import { ConditionBadge, DiscountBadge, InstallmentBadge, SwatchDots } from "./product-badges";

/**
 * Картка товару (DESIGN_SYSTEM §4.1).
 *
 * Структура рівно як у специфікації:
 *   бейджі (л.вгорі) · ♡ ⚖ (пр.вгорі) · фото 1:1 · свотчі · статус · назва · ціна ·
 *   бейдж «частинами» · кнопка «Купити»
 *
 * ⚠️ Уся картка — посилання. Кнопка «Купити» та іконки ♡/⚖ — ВКЛАДЕНІ кнопки:
 * їм потрібен preventDefault + stopPropagation, інакше клік по «Купити» ще й
 * навігує на товар, і людина втрачає сітку.
 *
 * ⚠️ Висота ціни зарезервована завжди (навіть без старої ціни) — інакше картки
 * зі знижкою і без неї мають різну висоту, і сітка «стрибає».
 */
export function ProductCard({
  product,
  priority = false,
  className,
}: {
  product: ProductListItem;
  /** true для перших карток у сітці — LCP-фото. */
  priority?: boolean;
  className?: string;
}) {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const addToCart = useCartStore((s) => s.add);
  const inCart = useCartStore((s) => s.lines.some((l) => l.id === product.id));

  const toggleWish = useWishlistStore((s) => s.toggle);
  const isWished = useWishlistStore((s) => s.ids.includes(product.id));

  const toggleCompare = useCompareStore((s) => s.toggle);
  const compareIds = useCompareStore((s) => s.ids);
  const isComparing = compareIds.includes(product.id);

  // null, якщо old_price порожня або не більша за price → бейдж не з'явиться.
  const discount = discountPercent(product.price, product.old_price);
  const isOut = product.availability === "out_of_stock";
  const href = localePath(locale, `/p/${product.id}/${product.slug}`);

  const stop = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  return (
    <div
      className={cn(
        "group relative flex flex-col rounded-lg border border-border bg-card p-3",
        // Без тіні у спокої. Підняття на hover — фото НЕ зумиться:
        // у побутовій техніці зум фото не додає інформації.
        "transition-all duration-150 ease-out hover:-translate-y-0.5 hover:border-transparent hover:shadow-lg",
        "focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2",
        className,
      )}
    >
      <Link href={href} className="flex flex-1 flex-col outline-none">
        <span className="sr-only">{product.name}</span>

        {/* ── Фото 1:1 ─────────────────────────────────────────────── */}
        <div className="relative aspect-square w-full">
          {product.main_image_url ? (
            <Image
              src={product.main_image_url}
              alt={product.name}
              fill
              sizes="(max-width: 640px) 45vw, (max-width: 1024px) 30vw, 20vw"
              className={cn(
                "object-contain p-3",
                isOut && "opacity-60 grayscale",
              )}
              priority={priority}
            />
          ) : (
            // Порожній стан — заглушка, ніколи не битий <img>.
            <div className="flex size-full items-center justify-center rounded-md bg-muted">
              <ImageOff aria-hidden className="size-8 text-muted-foreground" />
            </div>
          )}
        </div>

        {/* Свотчі кольорів — видно варіанти, не заходячи в товар. */}
        <SwatchDots swatches={product.swatches} className="mt-1 min-h-4" />

        {/* ── Статус ────────────────────────────────────────────────── */}
        <AvailabilityBadge
          availability={product.availability}
          leadDays={product.order_lead_days}
          className="mt-2"
        />

        {/* ── Назва: фіксовані 2 рядки, щоб сітка не «дихала» ───────── */}
        <h3 className="mt-1 line-clamp-2 min-h-10 text-sm font-medium text-foreground">
          {product.name}
        </h3>
      </Link>

      {/* ── Оплата частинами: місце зарезервоване, щоб сітка не стрибала ── */}
      <div className="mt-2 min-h-7">
        {product.installment_available && product.installment_max_payments ? (
          <InstallmentBadge payments={product.installment_max_payments} />
        ) : null}
      </div>

      {/* ── Ціна + кнопка одним рядком (почерк jabko.ua) ──────────────────
          Ціна і дія стоять поруч, а не одне під одним: погляд не мусить стрибати
          через усю картку, щоб зіставити «скільки це коштує» і «як це купити».

          ⚠️ В один рядок — ЛИШЕ від sm. На мобільній сітці в дві колонки картка
          вужча за 160 px: «36 109 ₴» і «Купити» там не вміщаються поруч і ламають
          одне одного. Тому знизу — стовпчик, від sm — рядок.

          Немає в наявності → secondary + «Повідомити про наявність», але ціна
          лишається на місці: людина має бачити, скільки це коштує. */}
      <div className="mt-2 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Price
          price={product.price}
          oldPrice={product.old_price}
          locale={locale}
          size="lg"
          className="order-1 sm:order-2 sm:text-right"
        />

        <Button
          variant={isOut ? "secondary" : "default"}
          size="lg"
          className="order-2 w-full sm:order-1 sm:w-auto"
          onClick={(e) => {
            stop(e);
            if (isOut) {
              toast(t("product.notifyWhenAvailable"), { description: product.name });
              return;
            }
            addToCart(product.id);
            toast.success(t("cart.title"), { description: product.name });
          }}
        >
          {isOut
            ? t("product.notifyWhenAvailable")
            : hydrated && inCart
              ? t("product.inCart")
              : t("product.buy")}
        </Button>
      </div>

      {/* ── Бейджі, лівий верхній кут (стек, max 2) ───────────────── */}
      <div className="pointer-events-none absolute left-3 top-3 flex flex-col items-start gap-1">
        <DiscountBadge percent={discount} locale={locale} />
        <ConditionBadge condition={product.condition} />
      </div>

      {/* ── ♡ / ⚖, правий верхній кут ──────────────────────────────────
          На тач-екранах видимі завжди (hover там не існує), на ПК — з'являються на hover. */}
      <div className="absolute right-3 top-3 flex flex-col gap-1 opacity-100 transition-opacity md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={isWished ? t("product.removeFromWishlist") : t("product.addToWishlist")}
          aria-pressed={hydrated ? isWished : undefined}
          className="bg-card/80 backdrop-blur-sm"
          onClick={(e) => {
            stop(e);
            toggleWish(product.id);
            // ⚠️ Той самий фідбек, що в «Купити» і ⚖. Без тосту ♡ був ЄДИНОЮ кнопкою в
            // картці, яка на дотик не підтверджувала нічого: на телефоні ховера немає,
            // а зміну заливки серця 16px людина просто не помічає.
            if (!isWished) {
              toast.success(t("wishlist.added"), { description: product.name });
            }
          }}
        >
          <Heart
            className={cn("size-4", hydrated && isWished && "fill-primary text-primary")}
          />
        </Button>

        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={isComparing ? t("product.removeFromCompare") : t("product.addToCompare")}
          aria-pressed={hydrated ? isComparing : undefined}
          className="bg-card/80 backdrop-blur-sm"
          onClick={(e) => {
            stop(e);
            if (!isComparing && compareIds.length >= COMPARE_LIMIT) {
              toast.error(t("compare.limitReached"));
              return;
            }
            toggleCompare(product.id);
            // ⚠️ Єдиною ознакою успіху була зміна кольору іконки 16px. На телефоні (де
            // ховера немає, а лічильник у хедері був СХОВАНИЙ) людина тицяла ⚖ на кількох
            // товарах і не розуміла, чи спрацювало. «Купити» тост показує — тут теж мусить.
            if (!isComparing) {
              toast.success(t("compare.added"), { description: product.name });
            }
          }}
        >
          <Scale className={cn("size-4", hydrated && isComparing && "text-primary")} />
        </Button>
      </div>
    </div>
  );
}

/** Скелет — ті самі розміри блоків, щоб при завантаженні нічого не смикалось. */
export function ProductCardSkeleton() {
  return (
    <div className="flex flex-col rounded-lg border border-border bg-card p-3">
      <div className="aspect-square w-full animate-pulse rounded-md bg-muted" />
      <div className="mt-3 h-4 w-24 animate-pulse rounded bg-muted" />
      <div className="mt-2 h-10 w-full animate-pulse rounded bg-muted" />
      <div className="mt-2 h-5 w-16 animate-pulse rounded bg-muted" />
      <div className="mt-1 h-6 w-24 animate-pulse rounded bg-muted" />
      <div className="mt-2 h-7 w-28 animate-pulse rounded bg-muted" />
      <div className="mt-2 h-10 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}
