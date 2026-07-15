"use client";

import { useMemo, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { ImageOff, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useLocale, useT } from "@/i18n/provider";
import { localePath } from "@/i18n/config";
import { formatPrice, formatSpecValue } from "@/lib/format";
import { useCartStore } from "@/store/cart";
import { useCompareStore } from "@/store/compare";
import type { ProductDetail, SpecRow } from "@/lib/api/types";

/**
 * Таблиця порівняння (DESIGN_SYSTEM §4.7).
 *
 * ⚠️ Тумблер «Тільки відмінності» — це ГОЛОВНА цінність порівняння побутової техніки.
 * Два холодильники мають по 40 характеристик, з них збігаються 32. Показувати всі 40 —
 * означає змусити людину шукати різницю очима. Ховаємо однакові рядки одним кліком.
 *
 * ⚠️ На мобільному — та сама таблиця з горизонтальним скролом, а НЕ «карточний» варіант.
 * Картки ламають сам сенс порівняння: значення перестають стояти в одному рядку.
 */
export function CompareTable({ products }: { products: ProductDetail[] }) {
  const t = useT();
  const locale = useLocale();

  const [onlyDiff, setOnlyDiff] = useState(false);

  const removeFromCompare = useCompareStore((s) => s.remove);
  const addToCart = useCartStore((s) => s.add);

  /**
   * ⚠️ Рядки таблиці = БАЗОВІ ФАКТИ + характеристики. Саме в такому порядку, і саме тому:
   *
   * Раніше таблиця будувалась ВИКЛЮЧНО з `p.specs` (значень атрибутів). У цьому каталозі
   * атрибути майже не заповнені: з 314 активних товарів 58 не мають ЖОДНОГО атрибута, ще
   * 221 мають рівно один. Наслідок у браузері: людина додає до порівняння дві духові шафи,
   * відкриває /compare — і бачить фото, ціни, кнопки «Купити»… і ПОРОЖНЮ таблицю під ними.
   * Ні рядка, ні пояснення. Сторінка виглядає зламаною — і формально вона такою і була.
   *
   * Бекенд при цьому ЗАВЖДИ віддає наявність, бренд, гарантію, країну й код товару. Це
   * рівно ті факти, за якими техніку й порівнюють. Тож база більше не залежить від того,
   * наповнив контент-менеджер атрибути чи ні: таблиця не може бути порожньою.
   */
  const rows = useMemo(() => {
    type RowDef = {
      code: string;
      name: string;
      group: string;
      groupSort: number;
      sort: number;
      /** Значення для конкретного товару; null → характеристики немає. */
      value: (p: ProductDetail) => { text: string; num: number | null } | null;
    };

    const baseGroup = t("compare.baseGroup");
    const defs: RowDef[] = [];

    /**
     * Коди атрибутів, які дублюють базовий рядок. Якщо контент-менеджер завів «Бренд»
     * атрибутом — синтетичний рядок не додаємо, інакше в таблиці два рядки про одне.
     */
    const specCodes = new Set(products.flatMap((p) => p.specs.map((s) => s.code)));
    const dupe = (codes: string[]) => codes.some((c) => specCodes.has(c));

    defs.push({
      code: "__availability",
      name: t("compare.availability"),
      group: baseGroup,
      groupSort: -1,
      sort: 0,
      value: (p) => ({ text: t(`availability.${p.availability}`), num: null }),
    });

    if (!dupe(["brend", "brand"])) {
      defs.push({
        code: "__brand",
        name: t("product.brand"),
        group: baseGroup,
        groupSort: -1,
        sort: 1,
        value: (p) => (p.brand ? { text: p.brand.name, num: null } : null),
      });
    }

    if (!dupe(["garantiia", "warranty"])) {
      defs.push({
        code: "__warranty",
        name: t("product.warranty"),
        group: baseGroup,
        groupSort: -1,
        sort: 2,
        value: (p) =>
          p.warranty_months
            ? {
                text: t("product.warrantyMonths", { n: p.warranty_months }),
                num: p.warranty_months,
              }
            : null,
      });
    }

    if (!dupe(["kraina", "country"])) {
      defs.push({
        code: "__country",
        name: t("product.country"),
        group: baseGroup,
        groupSort: -1,
        sort: 3,
        value: (p) => (p.country ? { text: p.country.name, num: null } : null),
      });
    }

    defs.push({
      code: "__sku",
      name: t("product.code"),
      group: baseGroup,
      groupSort: -1,
      sort: 4,
      value: (p) => ({ text: p.sku, num: null }),
    });

    // ── Характеристики (атрибути) ────────────────────────────────
    const seen = new Set<string>();
    const specDefs: RowDef[] = [];

    for (const p of products) {
      for (const spec of p.specs) {
        if (seen.has(spec.code)) continue;
        seen.add(spec.code);
        specDefs.push({
          code: spec.code,
          name: spec.n,
          group: spec.g,
          groupSort: spec.gs,
          sort: spec.s,
          value: (item) => {
            const found = item.specs.find((s) => s.code === spec.code);
            return found ? { text: formatSpecValue(found), num: found.vn } : null;
          },
        });
      }
    }

    specDefs.sort((a, b) => a.groupSort - b.groupSort || a.sort - b.sort);

    return [...defs, ...specDefs].map((meta) => {
      const cells = products.map((p) => {
        const v = meta.value(p);
        return {
          // Порожнє значення — «—», ніколи не порожня комірка: інакше не зрозуміло,
          // це «немає такої характеристики» чи «дані не завантажились».
          text: v ? v.text : "—",
          num: v?.num ?? null,
          present: Boolean(v),
        };
      });

      const distinct = new Set(cells.map((c) => c.text));
      const isDifferent = distinct.size > 1;

      // Найбільше числове значення — крапка акцентом. Заливати рядок не можна:
      // акцент зайнятий ціною і кнопкою.
      const nums = cells.map((c) => c.num).filter((n): n is number => n !== null);
      const best = nums.length > 1 ? Math.max(...nums) : null;

      return { ...meta, cells, isDifferent, best };
    });
  }, [products, t]);

  /** Чи є хоч у когось РЕАЛЬНІ характеристики (а не лише базові факти). */
  const hasSpecs = products.some((p) => p.specs.length > 0);

  const visibleRows = onlyDiff ? rows.filter((r) => r.isDifferent) : rows;

  // Групуємо назад у секції.
  const grouped = useMemo(() => {
    const map = new Map<string, typeof visibleRows>();
    for (const row of visibleRows) {
      const bucket = map.get(row.group);
      if (bucket) bucket.push(row);
      else map.set(row.group, [row]);
    }
    return [...map.entries()];
  }, [visibleRows]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <Switch id="only-diff" checked={onlyDiff} onCheckedChange={setOnlyDiff} />
        <label htmlFor="only-diff" className="cursor-pointer text-sm text-foreground">
          {t("compare.onlyDiff")}
        </label>
      </div>

      {/* Атрибути в цьому каталозі заповнені не в усіх товарів. Мовчати про це не можна:
          людина мусить розуміти, що це не збій сторінки, а незаповнені дані. Порівняти
          базові факти (наявність, бренд, гарантія) вона при цьому все одно може. */}
      {!hasSpecs && (
        <p className="rounded-lg border border-border bg-muted/50 p-4 text-sm text-muted-foreground">
          {t("compare.noSpecs")}
        </p>
      )}

      {onlyDiff && visibleRows.length === 0 ? (
        <p className="rounded-lg border border-border bg-muted/50 p-6 text-center text-sm text-muted-foreground">
          {t("compare.noDiff")}
        </p>
      ) : (
        <>
          {/* Підказка-градієнт справа: на мобільному видно, що таблицю можна скролити. */}
          <p className="text-xs text-muted-foreground lg:hidden">{t("compare.scrollHint")}</p>

          <div className="relative overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              {/* ── Шапка: фото + назва + ціна + Купити + × ─────────── */}
              <thead>
                <tr>
                  {/* Липкий перший стовпець — інакше при скролі вправо
                      незрозуміло, яку саме характеристику ти дивишся. */}
                  <th
                    scope="col"
                    // Ліва колонка вужча на телефоні (112px), повна з sm (192px):
                    // на 390px 192px з'їдали пів-екрана й лишали крихту для товарів.
                    className="sticky left-0 z-10 w-28 min-w-28 sm:w-48 sm:min-w-48 border-b border-r border-border bg-card p-2 sm:p-3 text-left align-bottom"
                  >
                    <span className="sr-only">{t("product.specs")}</span>
                  </th>

                  {products.map((p) => (
                    <th
                      key={p.id}
                      scope="col"
                      className="w-60 min-w-60 border-b border-border bg-card p-3 align-top"
                    >
                      <div className="relative flex flex-col gap-2">
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={t("compare.remove")}
                          className="absolute right-0 top-0"
                          onClick={() => removeFromCompare(p.id)}
                        >
                          <X className="size-3" />
                        </Button>

                        <Link
                          href={localePath(locale, `/p/${p.id}/${p.slug}`)}
                          className="flex flex-col gap-2"
                        >
                          <div className="relative mx-auto size-20">
                            {p.main_image_url ? (
                              <Image
                                src={p.main_image_url}
                                alt={p.name}
                                fill
                                sizes="80px"
                                className="object-contain"
                              />
                            ) : (
                              <div className="flex size-full items-center justify-center rounded bg-muted">
                                <ImageOff aria-hidden className="size-5 text-muted-foreground" />
                              </div>
                            )}
                          </div>

                          <span className="line-clamp-2 text-left text-sm font-medium text-foreground">
                            {p.name}
                          </span>
                        </Link>

                        <span
                          className={cn(
                            "price text-left text-price-lg",
                            p.old_price ? "text-price" : "text-price-regular",
                          )}
                        >
                          {formatPrice(p.price, locale)} ₴
                        </span>

                        <Button
                          size="xl"
                          className="w-full"
                          disabled={p.availability === "out_of_stock"}
                          onClick={() => {
                            addToCart(p.id);
                            toast.success(t("cart.title"), { description: p.name });
                          }}
                        >
                          {t("product.buy")}
                        </Button>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>

              {grouped.map(([group, groupRows]) => (
                <tbody key={group}>
                  <tr>
                    <th
                      scope="colgroup"
                      colSpan={products.length + 1}
                      className="sticky left-0 bg-background p-3 text-left text-h3 text-foreground"
                    >
                      {group}
                    </th>
                  </tr>

                  {groupRows.map((row) => (
                    /* ⚠️ `bg-card` на НЕПАРНИХ рядках — обов'язковий, а не косметика.
                       Липка перша колонка успадковує фон рядка (`bg-inherit`). Раніше тут
                       стояло лише `even:bg-muted`, тож НЕПАРНІ рядки не мали фону взагалі —
                       і липка колонка ставала ПРОЗОРОЮ: при горизонтальному гортанні на
                       телефоні значення товарів їхали ПІД назвами характеристик, накладаючись
                       на них. Обидві парності мусять бути непрозорими. */
                    <tr key={row.code} className="bg-card even:bg-muted">
                      <th
                        scope="row"
                        // Та сама адаптивна ширина, що й у шапці, + перенос довгих назв,
                        // щоб рядковий заголовок не розпирав колонку понад задане.
                        className="sticky left-0 z-10 w-28 min-w-28 sm:w-48 sm:min-w-48 break-words border-r border-border bg-inherit p-2 sm:p-3 text-left font-normal text-muted-foreground"
                      >
                        {/* Назва — БЕЗ одиниці (одиниця вже в значенні). */}
                        {row.name}
                      </th>

                      {row.cells.map((cell, i) => (
                        <td
                          key={products[i].id}
                          className={cn(
                            "p-3 text-foreground tnum",
                            // Відмінне значення — жирним. Це підказка «дивись сюди».
                            row.isDifferent && cell.present && "font-semibold",
                            !cell.present && "text-muted-foreground",
                          )}
                        >
                          <span className="inline-flex items-center gap-1.5">
                            {cell.text}
                            {row.best !== null && cell.num === row.best && (
                              <span
                                aria-hidden
                                title="max"
                                className="size-1.5 rounded-full bg-primary"
                              />
                            )}
                          </span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              ))}
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/** Прибирає з rows-типу залежність від внутрішнього useMemo — для тестів/сторібука. */
export type CompareRow = {
  code: string;
  name: string;
  cells: { text: string; num: number | null; present: boolean }[];
  isDifferent: boolean;
};

export type { SpecRow };
