"use client";

import Image from "next/image";
import Link from "next/link";
import { AlertTriangle, ImageOff, Minus, Plus, Trash2 } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { formatPrice } from "@/lib/format";
import { useCartStore, selectCartUnits } from "@/store/cart";
import { useCartPreview } from "@/hooks/use-cart-preview";
import { AvailabilityBadge } from "@/components/product/availability-badge";
import { ApiErrorState } from "@/components/api-error";

/**
 * Панель кошика, що вилітає справа після «Купити».
 *
 * Навіщо вона взагалі: замовник (Артур) — «клієнт рідко збирає замовлення з 5 одиниць».
 * Тобто типовий шлях тут не «накидав повний кошик → пішов оформлювати», а «одна пральна
 * машина → оформити». Тост «додано в кошик» у такому сценарії коштував людині ще двох
 * кліків (знайти 🛒 в шапці → відкрити сторінку), і саме на цьому кроці замовлення губились.
 *
 * ⚠️ Ціни тут — так само з POST /api/cart/preview, а не з localStorage. Причина та сама,
 * що й у CartView: у localStorage лежать голі {id, qty}. Див. store/cart.ts.
 *
 * ⚠️ Вміст (а разом з ним і useCartPreview) монтується ТІЛЬКИ коли панель відкрита.
 * Інакше компонент висить у Providers на кожній сторінці й ганяє bulk-запит цін
 * при кожній зміні кошика — навіть коли ніхто цей кошик не дивиться.
 */
export function CartDrawer() {
  const t = useT();
  const open = useCartStore((s) => s.isOpen);
  const setOpen = useCartStore((s) => s.setOpen);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        side="right"
        className="w-[min(92vw,420px)] gap-0 p-0 sm:max-w-[420px]"
      >
        <SheetTitle className="border-b border-border px-4 py-3.5 pr-12">
          {t("cart.title")}
        </SheetTitle>
        {open && <CartDrawerBody onClose={() => setOpen(false)} />}
      </SheetContent>
    </Sheet>
  );
}

function CartDrawerBody({ onClose }: { onClose: () => void }) {
  const t = useT();
  const locale = useLocale();

  const lines = useCartStore((s) => s.lines);
  const units = useCartStore(selectCartUnits);
  const setQty = useCartStore((s) => s.setQty);
  const remove = useCartStore((s) => s.remove);

  const { data, isLoading, error, removedItems, reload } = useCartPreview();

  if (lines.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="text-h3 text-foreground">{t("cart.empty")}</p>
        <p className="text-sm text-muted-foreground">{t("cart.emptyHint")}</p>
        <Link
          href={localePath(locale, "/catalog")}
          onClick={onClose}
          className={cn(buttonVariants({ size: "lg" }), "mt-4")}
        >
          {t("cart.goToCatalog")}
        </Link>
      </div>
    );
  }

  /**
   * ⚠️ Помилка запиту НЕ МОЖЕ виглядати як порожній кошик — те саме правило, що й у
   * CartView: показувати нема чого, поки сервер не віддав ціни, тож кажемо це прямо.
   */
  if (error) {
    return (
      <div className="p-4">
        <ApiErrorState onRetry={reload} />
      </div>
    );
  }

  const items = data?.items ?? [];

  return (
    <>
      <div className="flex-1 overflow-y-auto p-3">
        {removedItems.length > 0 && (
          <div
            role="status"
            className="mb-3 flex items-start gap-2 rounded-lg border border-border bg-stock-out-bg p-3"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-stock-out" />
            <div>
              <p className="text-sm font-medium text-foreground">{t("cart.unavailable")}</p>
              <p className="text-xs text-muted-foreground">{t("cart.unavailableHint")}</p>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2">
          {isLoading && items.length === 0
            ? lines.map((l) => <Skeleton key={l.id} className="h-24 w-full" />)
            : items.map((item) => (
                <article
                  key={item.id}
                  className={cn(
                    "flex gap-3 rounded-lg border border-border bg-card p-2.5",
                    isLoading && "opacity-60",
                  )}
                >
                  <Link
                    href={localePath(locale, `/p/${item.id}/${item.slug}`)}
                    onClick={onClose}
                    className="relative size-16 shrink-0"
                  >
                    {item.main_image_url ? (
                      <Image
                        src={item.main_image_url}
                        alt={item.name}
                        fill
                        sizes="64px"
                        className="object-contain"
                      />
                    ) : (
                      <div className="flex size-full items-center justify-center rounded bg-muted">
                        <ImageOff aria-hidden className="size-4 text-muted-foreground" />
                      </div>
                    )}
                  </Link>

                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <Link
                      href={localePath(locale, `/p/${item.id}/${item.slug}`)}
                      onClick={onClose}
                      className="line-clamp-2 text-sm font-medium text-foreground hover:underline"
                    >
                      {item.name}
                    </Link>

                    <AvailabilityBadge availability={item.availability} />

                    <div className="mt-auto flex items-center justify-between gap-2">
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
                          className="w-7 text-center text-sm font-medium tnum"
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

                      <span className="price text-base font-bold text-price-regular">
                        {formatPrice(item.line_total, locale)} ₴
                      </span>
                    </div>
                  </div>

                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="self-start"
                    aria-label={t("cart.remove")}
                    onClick={() => remove(item.id)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </article>
              ))}
        </div>
      </div>

      {/* ── Підсумок + дії ────────────────────────────────────────────── */}
      <div className="mt-auto border-t border-border bg-card p-4">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-muted-foreground">
            {t("cart.total")} · {t("cart.units", { n: units })}
          </span>
          <span className="price text-price-lg text-price-regular">
            {isLoading && !data ? (
              <Skeleton className="h-6 w-24" />
            ) : (
              `${formatPrice(data?.subtotal ?? "0", locale)} ₴`
            )}
          </span>
        </div>

        <Link
          href={localePath(locale, "/checkout")}
          onClick={onClose}
          className={cn(buttonVariants({ size: "xl" }), "mt-3 w-full")}
        >
          {t("cart.checkout")}
        </Link>

        <div className="mt-2 flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            {t("cart.continueShopping")}
          </Button>
          <Link
            href={localePath(locale, "/cart")}
            onClick={onClose}
            className="text-sm text-primary hover:underline"
          >
            {t("product.goToCart")}
          </Link>
        </div>
      </div>
    </>
  );
}
