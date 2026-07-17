"use client";

import { useEffect, useState } from "react";

type ViewportRect = { height: number; offsetTop: number };

/**
 * Розмір ВІЗУАЛЬНОГО вьюпорта — тобто того, що реально лишається видимим після
 * того, як екранна клавіатура з'їла пів екрана.
 *
 * ⚠️ Навіщо це взагалі, якщо панель і так `fixed` на всю висоту.
 *
 * `inset-0` / `100dvh` міряються від ЛЕЙАУТ-вьюпорта, а він при відкритті клавіатури
 * НЕ змінюється — ні в iOS Safari, ні в Chrome з дефолтним `interactive-widget=
 * resizes-visual`. Тобто панель лишається на всю висоту екрана, а її низ — поле вводу,
 * заради якого клавіатуру й відкрили, — опиняється ПІД клавіатурою. Саме це виглядало
 * як «вилізає клавіатура і нічого не видно».
 *
 * `interactiveWidget: "resizes-content"` (app/[locale]/layout.tsx) лікує це на Android
 * нативно, але iOS Safari цей meta ігнорує — там рятує лише VisualViewport API.
 * Тому обидва механізми потрібні разом, це не дублювання.
 *
 * offsetTop потрібен окремо від height: на iOS клавіатура не лише вкорочує видиму
 * область, а й ЗСУВАЄ її вгору відносно лейаут-вьюпорта. Без урахування зсуву панель
 * правильної висоти все одно стояла б не на своєму місці.
 *
 * Повертає null до першого виміру (SSR + перший рендер) — споживач мусить мати
 * власний фолбек, а не чекати на число.
 */
export function useVisualViewport(): ViewportRect | null {
  const [rect, setRect] = useState<ViewportRect | null>(null);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;

    // rAF-тротлінг: на iOS `resize`/`scroll` візуального вьюпорта сиплються десятками
    // за час анімації виїзду клавіатури. Без тротлінгу це стільки ж ре-рендерів панелі
    // разом зі всією історією повідомлень — тобто рівно ті лаги, які ми лікуємо.
    let frame = 0;
    const read = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() =>
        setRect({ height: vv.height, offsetTop: vv.offsetTop }),
      );
    };

    read();
    vv.addEventListener("resize", read);
    vv.addEventListener("scroll", read);

    return () => {
      cancelAnimationFrame(frame);
      vv.removeEventListener("resize", read);
      vv.removeEventListener("scroll", read);
    };
  }, []);

  return rect;
}
