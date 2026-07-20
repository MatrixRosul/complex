import { cn } from "@/lib/utils";
import { formatPrice } from "@/lib/format";
import type { Locale } from "@/i18n/config";

/**
 * Ціна (DESIGN_SYSTEM §3, §2.3).
 *
 * ⚠️ Акцентний колір — ТІЛЬКИ коли є знижка. Звичайна ціна чорна (--price-regular).
 * Інакше помаранчевий перестає означати «тут гроші / тут вигода» і стає просто фарбою.
 *
 * ⚠️ Стара ціна ЗВЕРХУ від нової (як на референсі), дрібна, сіра, закреслена.
 * Місце під неї зарезервовано завжди (min-h) — інакше сітка «стрибає».
 */
export function Price({
  price,
  oldPrice,
  locale,
  size = "lg",
  className,
  reserveOldPriceSpace = true,
}: {
  price: string;
  oldPrice?: string | null;
  locale: Locale;
  size?: "lg" | "xl";
  className?: string;
  reserveOldPriceSpace?: boolean;
}) {
  const hasDiscount = Boolean(oldPrice) && Number(oldPrice) > Number(price);

  return (
    <div className={cn("flex flex-col", className)}>
      <div
        className={cn(
          "price text-sm leading-5",
          // Перекреслення й колір старої ціни — ТІЛЬКИ коли знижка реально є.
          // Інакше line-through малює штрих по порожньому рядку — виглядає як «прочерк»
          // над ціною на кожному товарі без знижки.
          hasDiscount && "text-price-old line-through",
          reserveOldPriceSpace && "min-h-5",
        )}
      >
        {hasDiscount ? `${formatPrice(oldPrice!, locale)} ₴` : " "}
      </div>

      <div
        className={cn(
          "price",
          size === "xl" ? "text-price-xl" : "text-price-lg",
          hasDiscount ? "text-price" : "text-price-regular",
        )}
      >
        {formatPrice(price, locale)} <span className="font-semibold">₴</span>
      </div>
    </div>
  );
}
