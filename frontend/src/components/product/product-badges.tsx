"use client";

import { CreditCard } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";
import { formatPrice } from "@/lib/format";
import type { Locale } from "@/i18n/config";
import type { Condition } from "@/lib/api/types";

/**
 * Бейдж знижки (DESIGN_SYSTEM §4.1).
 *
 * ⚠️ ВІДСОТКИ («−12%»), а не гривні. OPEN_QUESTIONS «гривні vs відсотки» ЗАКРИТО: замовник
 * відповів — показуємо відсоток. Гривнева гілка (`amount`) навмисно ЛИШИЛАСЬ робочою: щоб
 * повернути «−3 500 ₴», досить перестати передавати `percent` з ProductCard. Це те саме
 * «єдине місце», про яке тут писало старе TODO.
 *
 * ⚠️ Бейдж НЕ РЕНДЕРИТЬСЯ взагалі, якщо знижки немає: і `percent`, і `amount` приходять з
 * discountPercent()/discountAmount(), які повертають null, коли old_price порожня або
 * НЕ БІЛЬША за price. Тобто намалювати «−0%» або «знижку» на товарі без знижки тут
 * структурно неможливо.
 *
 * ⚠️ Заливка — АКЦЕНТ, ніколи не червоний. Червоний = помилка (--destructive).
 * Плутати знижку з помилкою — найдешевший спосіб зіпсувати сигнал.
 */
export function DiscountBadge({
  percent,
  amount,
  locale,
  className,
}: {
  /** Відсоток знижки. Має пріоритет над `amount`. */
  percent?: number | null;
  /** Абсолютна економія в грн — запасна гілка (див. коментар вище). */
  amount?: number | null;
  locale: Locale;
  className?: string;
}) {
  const t = useT();
  const value =
    percent != null
      ? `−${percent}%`
      : amount != null
        ? `−${formatPrice(amount, locale)} ₴`
        : null;

  if (value === null) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-xs font-semibold",
        "text-primary-foreground shadow-sm",
        className,
      )}
    >
      {/* Слово + число, а не саме число. Замовник просив «наліпку» як на референсі:
          «−12%» у кутку читається як службова позначка, «Акція −12%» — як привід
          зупинитись. Відсоток лишається — рішення «відсотки, не гривні» чинне. */}
      {t("product.saleBadge")}
      <span className="tnum">{value}</span>
    </span>
  );
}

/**
 * Наліпка стану товару («Уцінка», «Відновлений», «Б/в»).
 *
 * ⚠️ БУЛА ОБВОДКА, СТАЛА ЗАЛИВКА (прохання замовника з референсом). Аргумент «не має
 * конкурувати зі знижкою» був хибний: уцінка — це не менша за важливістю новина, а
 * ІНША. Тонка сіра обводка на білій картці її просто ховала.
 *
 * Колір — власний токен `--clearance`, а не `--primary`: «Акція» вже синя, і двома
 * синіми наліпками поспіль ці два різні факти неможливо розрізнити.
 */
export function ConditionBadge({
  condition,
  className,
}: {
  condition: Condition;
  className?: string;
}) {
  const t = useT();
  if (condition === 0) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md bg-clearance px-2.5 py-1 text-xs font-semibold",
        "text-clearance-foreground shadow-sm",
        className,
      )}
    >
      {t(`condition.${condition}` as "condition.2")}
    </span>
  );
}

/**
 * Бейдж «оплата частинами» (DESIGN_SYSTEM §4.1).
 *
 * Живе ПІД ЦІНОЮ, а не в кутку картки — це не знижка й не статус, це спосіб оплати.
 * Обводка + нейтральний текст: акцент зайнятий кнопкою і ціною.
 *
 * ⚠️ У сітці — без тултипа (не чіпляти hover на мобільному).
 * На сторінці товару тултип додає ProductPage.
 */
export function InstallmentBadge({
  payments,
  className,
}: {
  payments: number;
  className?: string;
}) {
  const t = useT();

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 text-xs font-semibold text-foreground",
        className,
      )}
    >
      <CreditCard aria-hidden className="size-4 text-muted-foreground" />
      {t("product.installment", { n: payments })}
    </span>
  );
}

/** Свотчі кольорів прямо у видачі — видно варіанти, не заходячи в товар. */
export function SwatchDots({
  swatches,
  className,
  max = 5,
}: {
  swatches: { hex: string; product_id: number }[];
  className?: string;
  max?: number;
}) {
  if (swatches.length === 0) return null;

  const shown = swatches.slice(0, max);
  const rest = swatches.length - shown.length;

  return (
    <div className={cn("flex items-center gap-1", className)} aria-hidden>
      {shown.map((s) => (
        <span
          key={s.product_id}
          className="size-4 rounded-full border border-border"
          style={{ backgroundColor: s.hex }}
        />
      ))}
      {rest > 0 && <span className="text-xs text-muted-foreground tnum">+{rest}</span>}
    </div>
  );
}
