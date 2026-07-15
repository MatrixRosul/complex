"use client";

import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useLocale } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import type { VariantGroupOut } from "@/lib/api/types";

/**
 * Перемикач варіантів (DESIGN_SYSTEM §4.5, INPUTS §4).
 *
 * ⚠️ УЗАГАЛЬНЕНИЙ: вісь — БУДЬ-ЯКА характеристика, не тільки діагональ.
 * На референсах замовника є і те, і те:
 *   widget="buttons"  → діагоналі 50" / 55" / 65" / 75" (скрін …_384)
 *   widget="swatches" → кольори кружечками (скріни …_346/347)
 * Тому подача обирається полем widget, а підпис осі приходить з бекенда (axis_label) —
 * фронт не знає й не має знати, що саме перемикають.
 *
 * ⚠️ Недоступний варіант НЕ ХОВАЄМО — показуємо disabled + line-through.
 * Людина має бачити, що 85" існує, просто зараз його немає. Схований варіант
 * читається як «такого не буває» — і вона піде шукати його в іншому магазині.
 *
 * ⚠️ Зміна варіанта = НАВІГАЦІЯ на інший товар: у кожного варіанта власний
 * артикул, власна ціна й власні фото. Це не локальний стейт.
 */
export function VariantSwitcher({
  group,
  currentProductId,
  className,
}: {
  group: VariantGroupOut;
  currentProductId: number;
  className?: string;
}) {
  const router = useRouter();
  const locale = useLocale();

  if (group.items.length < 2) return null;

  const goTo = (productId: number, slug: string) => {
    router.push(localePath(locale, `/p/${productId}/${slug}`));
  };

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {/* Підпис осі — точний текст характеристики: «Діагональ екрану, дюймів" (см)». */}
      <span className="text-sm font-semibold text-foreground">{group.axis_label}</span>

      {group.widget === "swatches" ? (
        <SwatchGroup group={group} currentProductId={currentProductId} onSelect={goTo} />
      ) : (
        <ButtonGroup group={group} currentProductId={currentProductId} onSelect={goTo} />
      )}
    </div>
  );
}

/** A. Кнопки — діагональ, об'єм, потужність. */
function ButtonGroup({
  group,
  currentProductId,
  onSelect,
}: {
  group: VariantGroupOut;
  currentProductId: number;
  onSelect: (id: number, slug: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label={group.axis_label} className="flex flex-wrap gap-2">
      {group.items.map((item) => {
        const isActive = item.product_id === currentProductId;
        const isUnavailable = !item.is_active;

        return (
          <button
            key={item.product_id}
            type="button"
            role="radio"
            aria-checked={isActive}
            disabled={isUnavailable}
            onClick={() => !isUnavailable && onSelect(item.product_id, item.slug)}
            className={cn(
              "h-11 rounded-lg border px-4 text-sm transition-colors",
              isActive
                ? // 4.97:1 у світлій, 7.69:1 у темній — обидва проходять AA.
                  "border-primary bg-brand-subtle font-semibold text-brand-subtle-foreground"
                : "border-border bg-card text-muted-foreground hover:border-input hover:text-foreground",
              // Варіант існує, але недоступний — видно, але не клікається.
              isUnavailable && "cursor-not-allowed line-through opacity-40 hover:border-border",
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

/** B. Кружечки — колір. */
function SwatchGroup({
  group,
  currentProductId,
  onSelect,
}: {
  group: VariantGroupOut;
  currentProductId: number;
  onSelect: (id: number, slug: string) => void;
}) {
  const current = group.items.find((i) => i.product_id === currentProductId);

  return (
    <div className="flex flex-col gap-2">
      {/* ⚠️ WCAG 1.4.1: колір НЕ МОЖНА кодувати самим кольором.
          Тому поруч зі свотчами — назва обраного кольору ТЕКСТОМ. */}
      {current && (
        <span className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{current.label}</span>
        </span>
      )}

      <div role="radiogroup" aria-label={group.axis_label} className="flex flex-wrap gap-3">
        {group.items.map((item) => {
          const isActive = item.product_id === currentProductId;
          const isUnavailable = !item.is_active;

          return (
            <button
              key={item.product_id}
              type="button"
              role="radio"
              aria-checked={isActive}
              aria-label={item.label}
              title={item.label}
              disabled={isUnavailable}
              onClick={() => !isUnavailable && onSelect(item.product_id, item.slug)}
              className={cn(
                "relative size-9 rounded-full border border-border transition-shadow",
                // ring-offset-card, а не -background: свотч лежить на картці.
                isActive && "ring-2 ring-primary ring-offset-2 ring-offset-card",
                isUnavailable && "cursor-not-allowed opacity-40",
              )}
              // Білий свотч на білій картці видно тільки завдяки border.
              style={{ backgroundColor: item.swatch_hex ?? "transparent" }}
            >
              {isUnavailable && (
                <span
                  aria-hidden
                  className="absolute inset-0 flex items-center justify-center"
                >
                  <span className="h-px w-full rotate-45 bg-foreground/60" />
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
