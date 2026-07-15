"use client";

import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";
import { formatSpecValue, groupSpecs } from "@/lib/format";
import type { SpecRow } from "@/lib/api/types";

/**
 * Блок «Характеристики | Опис» (INPUTS §2 — ЖОРСТКА ВИМОГА замовника,
 * специфікація — DESIGN_SYSTEM §4.4).
 *
 * Що саме вимагав замовник (зберігаємо формат, який зараз на galiton):
 *   1. Блок поділений НАВПІЛ: зліва Характеристики, справа Опис.
 *   2. На ПК — дві колонки поруч. На мобільному — одна під одною (Характеристики ПЕРШИМИ).
 *   3. Обидві згорнуті, з посиланнями «Усі характеристики» / «Весь опис».
 *   4. Опис — rich HTML із вбудованими фотографіями.
 *
 * ⚠️⚠️ ФОРМАТ РЯДКА — найважливіше:
 *      ПРАВИЛЬНО:   «Висота»       →  «284 мм»     (читається «Висота: 284 мм»)
 *      НЕПРАВИЛЬНО: «Висота (мм)»  →  «284»
 *   Одиниця виміру клеїться до ЗНАЧЕННЯ, а не до назви. У БД вона в окремій колонці (u).
 *   Склеюванням займається ВИКЛЮЧНО formatSpecValue() — тут ми навіть не бачимо row.u.
 *
 * ⚠️ SEO: обидва блоки завжди повністю в DOM. Згортання — це max-height у CSS,
 *   а не умовний рендер. Інакше краулер побачить обрізаний текст, і сторінка
 *   втратить весь довгий хвіст запитів по характеристиках.
 */
export function SpecsTable({
  specs,
  description,
  className,
}: {
  specs: SpecRow[];
  /** Rich HTML. Санітизація — на бекенді (bleach), не тут. */
  description: string;
  className?: string;
}) {
  const t = useT();
  const groups = groupSpecs(specs);

  return (
    <div className={cn("grid grid-cols-1 gap-6 lg:grid-cols-2", className)}>
      {/* ── Характеристики. На мобільному ПЕРШІ (порядок у DOM = порядок на екрані). ── */}
      <CollapsibleSection
        title={t("product.specs")}
        expandLabel={t("product.allSpecs")}
        collapseLabel={t("product.collapse")}
        empty={groups.length === 0}
        emptyLabel={t("product.noSpecs")}
      >
        {groups.map((group) => (
          <section key={group.group} className="mt-6 first:mt-0">
            <h3 className="text-h3 text-foreground">{group.group}:</h3>

            <dl className="mt-2">
              {group.rows.map((row) => (
                <div
                  key={`${row.code}-${row.s}`}
                  className="grid grid-cols-[1fr_auto] gap-4 rounded-sm px-2 py-2.5 even:bg-muted"
                >
                  {/* Назва — БЕЗ одиниці. Ніколи не row.n + row.u. */}
                  <dt className="text-sm text-muted-foreground">{row.n}</dt>
                  {/* Значення + одиниця. Єдине місце склеювання — formatSpecValue. */}
                  <dd className="text-right text-sm font-medium text-foreground tnum">
                    {formatSpecValue(row)}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </CollapsibleSection>

      {/* ── Опис: rich HTML із зображеннями між абзацами. ─────────────── */}
      <CollapsibleSection
        title={t("product.description")}
        expandLabel={t("product.allDescription")}
        collapseLabel={t("product.collapse")}
        empty={description.trim().length === 0}
        emptyLabel={t("product.noDescription")}
      >
        <div
          className={cn(
            "prose prose-zinc max-w-[68ch] dark:prose-invert",
            "prose-headings:text-h3 prose-headings:mt-6 prose-headings:mb-2",
            "prose-p:text-base prose-p:leading-6",
            "prose-img:rounded-lg prose-img:my-4 prose-img:max-w-full prose-img:h-auto",
            "prose-figcaption:text-xs prose-figcaption:text-muted-foreground",
          )}
          // ⚠️ Санітизація — на бекенді (bleach з whitelist тегів). Фронт лише рендерить.
          dangerouslySetInnerHTML={{ __html: description }}
        />
      </CollapsibleSection>
    </div>
  );
}

/**
 * Згортана секція з градієнт-фейдом.
 *
 * ⚠️ Тригер — <button> з aria-expanded/aria-controls, а НЕ <a href="#">.
 * Посилання, що нікуди не веде, ламає навігацію скрінрідером і додає # в історію.
 */
function CollapsibleSection({
  title,
  expandLabel,
  collapseLabel,
  empty,
  emptyLabel,
  children,
}: {
  title: string;
  expandLabel: string;
  collapseLabel: string;
  empty: boolean;
  emptyLabel: string;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();

  return (
    <div className="flex flex-col">
      <h2 className="border-b border-border pb-3 text-h2 text-foreground">{title}</h2>

      {empty ? (
        <p className="mt-4 text-sm text-muted-foreground">{emptyLabel}</p>
      ) : (
        <>
          <div
            id={contentId}
            className={cn(
              "relative mt-4 overflow-hidden",
              // Згорнуто: 320px + фейд знизу. Розгорнуто: без обмеження, фейд зникає.
              expanded ? "max-h-none" : "max-h-[320px] fade-bottom",
            )}
          >
            {children}
          </div>

          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-controls={contentId}
            className="mt-3 inline-flex w-fit items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            {expanded ? collapseLabel : expandLabel}
            <ChevronDown
              aria-hidden
              className={cn("size-4 transition-transform", expanded && "rotate-180")}
            />
          </button>
        </>
      )}
    </div>
  );
}
