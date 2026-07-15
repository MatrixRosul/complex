"use client";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";
import type { Availability } from "@/lib/api/types";

/**
 * Статус наявності (DESIGN_SYSTEM §5).
 *
 * ⚠️ Ніколи не покладаємось на колір самотою — КОЛІР + КРАПКА + ТЕКСТ (WCAG 1.4.1).
 * Дальтонік має зрозуміти статус, навіть якщо всі три кольори для нього однакові.
 *
 * ⚠️ «Під замовлення» — бурштиновий, а НЕ червоний: товар можна купити, це не помилка.
 * Червоний у системі зарезервований виключно за --destructive.
 *
 * Дві форми подачі:
 *   variant="dot"   — у картці сітки: крапка 6px + text-xs, без підкладки,
 *                     щоб не змагатись із бейджем знижки.
 *   variant="badge" — на сторінці товару: повний бейдж з підкладкою.
 */
const STYLES: Record<Availability, { dot: string; text: string; bg: string }> = {
  in_stock: { dot: "bg-stock-in", text: "text-stock-in", bg: "bg-stock-in-bg" },
  on_order: { dot: "bg-stock-order", text: "text-stock-order", bg: "bg-stock-order-bg" },
  out_of_stock: { dot: "bg-stock-out", text: "text-stock-out", bg: "bg-stock-out-bg" },
};

export function AvailabilityBadge({
  availability,
  leadDays,
  variant = "dot",
  className,
}: {
  availability: Availability;
  leadDays?: number | null;
  variant?: "dot" | "badge";
  className?: string;
}) {
  const t = useT();
  const style = STYLES[availability];

  const label = t(`availability.${availability}`);
  const suffix =
    availability === "on_order" && leadDays
      ? ` · ${t("availability.leadDays", { n: leadDays })}`
      : "";

  if (variant === "dot") {
    return (
      <span className={cn("inline-flex items-center gap-1.5 text-xs", style.text, className)}>
        <span
          aria-hidden
          className={cn(
            "size-1.5 shrink-0 rounded-full",
            style.dot,
            // «Немає» — порожнє коло: форма теж несе сенс, не тільки колір.
            availability === "out_of_stock" && "border border-current bg-transparent",
          )}
        />
        {label}
        {suffix}
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        style.bg,
        style.text,
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          style.dot,
          availability === "out_of_stock" && "border border-current bg-transparent",
        )}
      />
      {label}
      {suffix}
    </span>
  );
}
