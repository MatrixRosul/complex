"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n/provider";

export type SectionNavItem = {
  /** id елемента-секції на сторінці. Має збігатись з id у розмітці. */
  id: string;
  label: string;
};

/**
 * Бар навігації по розділах сторінки товару — плиткою НАД ФОТО.
 *
 * Задача «Сайт §4»: «над фото зробити плиткою бар з навігацією по розділах (Основне,
 * Характеристики/Опис, З цим товаром купують і далі що додамо)». Референс — картка на
 * zhuk.ua. «І далі що додамо» — тому список приходить пропом, а не захардкоджений тут:
 * додати розділ = дописати один рядок у page.tsx і поставити id на секцію.
 *
 * ⚠️ ПУНКТИ — СПРАВЖНІ <a href="#id">, а не кнопки з onClick. Причини дві:
 *   1. Без JS (і до гідратації) бар усе одно працює — це звичайні внутрішні посилання.
 *   2. Скрінрідер оголошує їх як навігацію по сторінці, а не як набір кнопок.
 * Плавний скрол додано поверх цього через preventDefault + scrollIntoView; якщо JS не
 * доїхав, браузер просто стрибне до секції — без плавності, але без поломки.
 *
 * ⚠️ ПРО ЗСУВ ПІД ШАПКУ: шапка липка й висока (смуга 48 px + рядок 96 px + рядок
 * розділів), тому «доскролити до секції» без поправки означає завести заголовок ПІД неї.
 * Поправку тримає НЕ цей компонент, а `scroll-mt-*` на самих секціях у page.tsx: і
 * нативний стрибок по хешу, і scrollIntoView однаково враховують scroll-margin-top,
 * тобто одна CSS-властивість лікує обидва шляхи. Дублювати її тут числом = гарантовано
 * розсинхронити з реальною висотою шапки при наступній її правці.
 */
export function SectionNav({ items }: { items: SectionNavItem[] }) {
  const t = useT();
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    const targets = items
      .map((item) => document.getElementById(item.id))
      .filter((el): el is HTMLElement => el !== null);

    if (targets.length === 0) return;

    /**
     * Скрол-спай через вузьку смугу-«приціл» замість повного вікна.
     *
     * rootMargin звужує область спостереження до горизонтальної смуги приблизно на
     * висоті 40% екрана. Без цього в кадр одночасно потрапляє 2–3 секції (характеристики
     * й «купують разом» невисокі), і активний пункт блимав би між ними на кожному
     * піксельному скролі. Зі смугою в неї попадає майже завжди одна секція; коли все ж
     * дві — беремо ту, що вище, тобто ту, яку людина зараз читає.
     */
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);

        if (visible.length > 0) {
          setActive(visible[0].target.id);
          return;
        }

        /**
         * ⚠️ У СМУЗІ НЕМА НІЧОГО — І ЦЕ НЕ «лишити як було».
         *
         * Перша секція починається аж під шапкою, крихтами, заголовком і самим баром
         * (~420 px), а смуга стоїть на 331–372 px. Тому у верхів'ї сторінки в неї не
         * попадає жодна секція. Ловилось так: доскролити до характеристик, вернутись
         * догори — і бар далі показує «Характеристики і опис», хоча людина дивиться на
         * фото. Тобто активний пункт залипав на останньому побаченому.
         *
         * Розрізняємо два «нічого»: якщо ПЕРША секція ще нижче верху вікна — ми над нею,
         * отже активний перший пункт. Якщо ж вона вже вище (проскролили вниз за останню
         * секцію, наприклад у футер) — лишаємо останній активний, це правильна відповідь.
         */
        if (targets[0].getBoundingClientRect().top > 0) setActive(targets[0].id);
      },
      { rootMargin: "-40% 0px -55% 0px", threshold: 0 },
    );

    targets.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [items]);

  if (items.length === 0) return null;

  /**
   * ⚠️ ФОЛБЕК НА ПЕРШИЙ ПУНКТ, поки скрол-спай ще не спрацював.
   *
   * Смуга-«приціл» IntersectionObserver'а стоїть на 40–45% висоти екрана, а перша секція
   * («Основне») на самому верху сторінки починається НИЖЧЕ неї — під шапкою, крихтами й
   * заголовком це ~420 px при смузі 331–372 px. Тобто на щойно відкритій картці в приціл
   * не попадає жодна секція, `active` лишався null, і бар стояв БЕЗ активного пункту —
   * виглядало так, ніби підсвітка не працює взагалі (перевірено в браузері).
   *
   * Розширювати смугу — гірше: вона тому й вузька, щоб активний пункт не блимав між
   * двома секціями на кожному пікселі скролу. Тому просто: не знаємо → людина на початку
   * сторінки, а початок сторінки — це перший розділ.
   */
  const activeId = active ?? items[0].id;

  return (
    <nav
      // no-scrollbar + overflow-x-auto: на телефоні чотири пункти в рядок не влазять,
      // і горизонтальний свайп тут доречніший за перенос у два рядки — бар мусить
      // лишатись однорядковою смугою, інакше він з'їдає перший екран.
      className="no-scrollbar flex gap-1 overflow-x-auto rounded-lg border border-border bg-card p-1.5"
      aria-label={t("product.sectionsNav")}
    >
      {items.map((item) => (
        <a
          key={item.id}
          href={`#${item.id}`}
          // aria-current="location" — саме для «ось цей розділ ви зараз дивитесь»
          // (aria-current="page" означало б іншу сторінку, а ми лишаємось на цій).
          aria-current={activeId === item.id ? "location" : undefined}
          onClick={(event) => {
            const target = document.getElementById(item.id);
            if (!target) return; // секції немає — лишаємо браузеру нативний перехід

            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            setActive(item.id);
          }}
          className={cn(
            "shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-sm font-semibold transition-colors",
            activeId === item.id
              ? "bg-brand-subtle text-brand-subtle-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          {item.label}
        </a>
      ))}
    </nav>
  );
}
