"use client";

import { useState } from "react";
import Link from "next/link";
import { Heart, Scale, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { OneClickDialog } from "./one-click-dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { discountAmount, formatPrice } from "@/lib/format";
import { useCartStore } from "@/store/cart";
import { useCompareStore, COMPARE_LIMIT } from "@/store/compare";
import { useWishlistStore } from "@/store/wishlist";
import { useHydrated } from "@/hooks/use-hydrated";
import type { ProductDetail } from "@/lib/api/types";

import { AvailabilityBadge } from "./availability-badge";
import { Price } from "./price";
import { ConditionBadge, DiscountBadge } from "./product-badges";

/**
 * Права колонка картки товару: код, статус, ціна, бейджі, кнопки дії.
 *
 * Кнопки з референсу (INPUTS §4): «Купити» (акцент), «Купити в 1 клік» (контраст).
 * «Купити в кредит» поки не робимо — у ТЗ його немає, це відкрите питання (INPUTS §5 Q7).
 */
export function BuyPanel({ product }: { product: ProductDetail }) {
  const t = useT();
  const locale = useLocale();
  const hydrated = useHydrated();

  const addAndOpenCart = useCartStore((s) => s.addAndOpen);
  const openCart = useCartStore((s) => s.setOpen);
  const removeFromCart = useCartStore((s) => s.remove);
  const inCart = useCartStore((s) => s.lines.some((l) => l.id === product.id));

  const toggleWish = useWishlistStore((s) => s.toggle);
  const isWished = useWishlistStore((s) => s.ids.includes(product.id));

  const toggleCompare = useCompareStore((s) => s.toggle);
  const compareIds = useCompareStore((s) => s.ids);
  const isComparing = compareIds.includes(product.id);

  const [oneClickOpen, setOneClickOpen] = useState(false);

  const discount = discountAmount(product.price, product.old_price);
  const isOut = product.availability === "out_of_stock";

  const perPayment =
    product.installment_max_payments && product.installment_max_payments > 0
      ? Number(product.price) / product.installment_max_payments
      : null;

  return (
    <div className="flex flex-col gap-4">
      {/* Код товару — над свічером варіантів (референс …_384). */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>{t("product.code")}:</span>
        <span className="product-code font-medium text-foreground">{product.sku}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <AvailabilityBadge
          availability={product.availability}
          leadDays={product.order_lead_days}
          variant="badge"
        />
        <ConditionBadge condition={product.condition} />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Price
          price={product.price}
          oldPrice={product.old_price}
          locale={locale}
          size="xl"
          reserveOldPriceSpace={false}
        />
        {discount !== null && <DiscountBadge amount={discount} locale={locale} className="mb-2" />}
      </div>

      {/* Бейдж «оплата частинами» — з тултипом (на сторінці товару це доречно). */}
      {product.installment_available && product.installment_max_payments && (
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                type="button"
                className="inline-flex w-fit items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-semibold text-foreground"
              >
                {t("product.installment", { n: product.installment_max_payments })}
              </button>
            }
          />
          <TooltipContent>
            {t("product.installmentTooltip", {
              n: product.installment_max_payments,
              sum: perPayment ? `${formatPrice(perPayment, locale)} ₴` : "—",
            })}
          </TooltipContent>
        </Tooltip>
      )}

      <div className="flex flex-col gap-2">
        <Button
          size="xl"
          variant={isOut ? "secondary" : "default"}
          className="w-full"
          /** Та сама логіка, що в картці товару: одразу показуємо кошик, а не тост. */
          onClick={() => {
            if (isOut) {
              toast(t("product.notifyWhenAvailable"), { description: product.name });
              return;
            }
            if (inCart) {
              openCart(true);
              return;
            }
            addAndOpenCart(product.id);
          }}
        >
          {isOut
            ? t("product.notifyWhenAvailable")
            : hydrated && inCart
              ? t("product.inCart")
              : t("product.buy")}
        </Button>

        {/* Кнопка БЕЗ onClick була найгіршим із можливих станів: клік не робив АБСОЛЮТНО
            нічого — ні модалки, ні тосту, ні помилки в консолі. Людина не могла зрозуміти,
            чи спрацював клік, чи завис браузер. Тепер відкриває модалку з реальними
            телефонами (див. OneClickDialog — там пояснено, чому саме телефони, а не форма). */}
        {!isOut && (
          <Button
            variant="contrast"
            size="xl"
            className="w-full"
            onClick={() => setOneClickOpen(true)}
          >
            {t("product.buyOneClick")}
          </Button>
        )}

        {/* ⚠️ Зворотна дія до «Купити». Прибрати товар з кошика можна було ЛИШЕ зі
            сторінки кошика: людина, що передумала прямо на картці товару, мусила йти
            в /cart шукати там цей самий товар. Кнопка з'являється тільки коли є що
            прибирати, тож у звичайному стані панель не роздувається. */}
        {hydrated && inCart && (
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground"
              onClick={() => {
                removeFromCart(product.id);
                toast.success(t("cart.removed"), { description: product.name });
              }}
            >
              <Trash2 className="size-4" />
              {t("product.removeFromCart")}
            </Button>

            <Link
              href={localePath(locale, "/cart")}
              className="text-sm text-primary hover:underline"
            >
              {t("product.goToCart")}
            </Link>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button
          variant="outline"
          size="xl"
          className="flex-1"
          aria-pressed={hydrated ? isWished : undefined}
          onClick={() => {
            toggleWish(product.id);
            // Фідбек, як у «Купити» і ⚖: додавання в бажання мусить підтверджуватись.
            if (!isWished) {
              toast.success(t("wishlist.added"), { description: product.name });
            }
          }}
        >
          <Heart className={cn("size-4", hydrated && isWished && "fill-primary text-primary")} />
          {hydrated && isWished ? t("product.removeFromWishlist") : t("product.addToWishlist")}
        </Button>

        <Button
          variant="outline"
          size="xl"
          aria-label={isComparing ? t("product.removeFromCompare") : t("product.addToCompare")}
          aria-pressed={hydrated ? isComparing : undefined}
          onClick={() => {
            if (!isComparing && compareIds.length >= COMPARE_LIMIT) {
              toast.error(t("compare.limitReached"));
              return;
            }
            toggleCompare(product.id);
            // Фідбек, якого не було: іконка 16px, що змінює колір, — це не підтвердження
            // дії, особливо на телефоні (ховера немає, лічильник у хедері не видно).
            if (!isComparing) {
              toast.success(t("compare.added"), { description: product.name });
            }
          }}
        >
          <Scale className={cn("size-4", hydrated && isComparing && "text-primary")} />
        </Button>
      </div>

      {product.warranty_months && (
        <p className="text-xs text-muted-foreground">
          {t("product.warrantyFromManufacturer")} ·{" "}
          <span className="tnum">
            {t("product.warrantyMonths", { n: product.warranty_months })}
          </span>
        </p>
      )}

      <OneClickDialog product={product} open={oneClickOpen} onOpenChange={setOneClickOpen} />
    </div>
  );
}
