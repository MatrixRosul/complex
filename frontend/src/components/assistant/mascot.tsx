"use client";

import { cn } from "@/lib/utils";

/**
 * Маскот асистента — робокіт. Раніше тут жила намальована примітивами SVG
 * лисичка; тепер образ приходить готовою 6-кадровою анімацією від замовника
 * (спрайт-аркуш public/images/assistant/cat.webp, зібраний з animation.gif).
 *
 * ⚠️ КІТ ЗАВЖДИ ЦІЛКОМ, ніяких портретів-голів. Тут був другий варіант —
 * квадратний кроп по морді для дрібних місць (кнопка 56 px, хедер 32 px), і він
 * двічі вийшов боком: по-перше, вуха ширші за екран-морду, і кроп їх зрізав;
 * по-друге, весь рух цієї анімації несе ХВІСТ — без нього лишалась майже
 * нерухома морда, що моргає раз на 2 секунди. Кіт сидить у пропорції 136:224,
 * тож місце під нього треба ВИТЯГУВАТИ, а не обрізати його під квадрат.
 *
 * ЧОМУ СПРАЙТ, А НЕ ГОТОВИЙ GIF / animated WebP у <img>.
 * Анімований файл крутиться сам по собі: його не пришвидшити під стан
 * «відповідає» і не зупинити в prefers-reduced-motion — браузер не дає
 * керувати відтворенням анімованого зображення ні з CSS, ні з JS. Тому кадри
 * розкладені в один ряд, а перемикає їх CSS `steps(6)` по background-position:
 *   — темп анімації = її тривалість, тобто станом керуємо одним числом;
 *   — reduced-motion = `animation: none`, і кіт просто завмирає на 1-му кадрі.
 * Бонусом один запит замість шести і жодного JS.
 *
 * АРИФМЕТИКА СПРАЙТА. background-size: 600% 100% → аркуш ушестеро ширший за
 * елемент. У відсотковому позиціюванні 0% — це 1-й кадр, 100% — 6-й (крок 20%),
 * тому keyframes ідуть 0% → 120%: steps(6, end) дає рівно 0/20/40/60/80/100
 * і 120% не показує ніколи. Аркуш нарізаний так, що ширина = рівно 6×кадр
 * (960 = 6×160), інакше на межах кадрів підмішувався б сусідній.
 *
 * КОЛІР. Токенів --fox / --fox-muted більше немає: кіт — растр, його колір
 * усередині файлу (і він синій, родич #0E3F68 з вордмарку). Дисципліна акценту
 * з DESIGN_SYSTEM §2.3 лишається, але тримається інакше: idle — спокійний
 * повільний цикл, а увагу в hover/talking додає РУХ (швидший темп + підскок),
 * а не друга помаранчева шкала поруч із синьою кнопкою «Купити».
 */

export type MascotState = "idle" | "hover" | "thinking" | "talking";

interface MascotProps {
  state: MascotState;
  /** Задає ВИСОТУ (`h-*`); ширину рахує aspect-ratio, тому `w-auto`. */
  className?: string;
}

/* Кадри й прив'язка до стану — рядком, вставляється через <style> (так само,
   як було в лисички: Tailwind 4 не згенерує @keyframes для довільного
   `animate-[...]`, а globals.css правиться окремо). Префікс `cx-cat-*`
   унікальний, тому з глобальним CSS не перетнеться. */
const MASCOT_STYLE = `
  .cx-cat {
    display: block;
    aspect-ratio: 160 / 264;
    background-image: url("/images/assistant/cat.webp");
    background-repeat: no-repeat;
    background-size: 600% 100%;
    background-position: 0% 50%;
  }

  @keyframes cx-cat-frames {
    from { background-position-x: 0%; }
    to   { background-position-x: 120%; }
  }
  /* Підскок і нахил живуть на transform — окремою анімацією поверх покадрової,
     щоб не сперечатися з нею за background-position. */
  @keyframes cx-cat-bob {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(-6%); }
  }
  @keyframes cx-cat-lean {
    0%, 100% { transform: rotate(-3deg); }
    50%      { transform: rotate(3deg); }
  }

  /* 1.98s = 6 кадрів × 330 ms — рідний темп вихідної анімації. */
  .cx-cat[data-cat-state="idle"] {
    animation: cx-cat-frames 1.98s steps(6) infinite;
  }
  .cx-cat[data-cat-state="hover"] {
    animation: cx-cat-frames 1.2s steps(6) infinite,
               cx-cat-bob 1.2s ease-in-out infinite;
  }
  .cx-cat[data-cat-state="thinking"] {
    animation: cx-cat-frames 1.2s steps(6) infinite,
               cx-cat-lean 1.8s ease-in-out infinite;
  }
  .cx-cat[data-cat-state="talking"] {
    animation: cx-cat-frames 0.84s steps(6) infinite,
               cx-cat-bob 0.84s ease-in-out infinite;
  }

  @media (prefers-reduced-motion: reduce) {
    .cx-cat { animation: none !important; }
  }
`;

export function Mascot({ state, className }: MascotProps) {
  return (
    <>
      <style>{MASCOT_STYLE}</style>
      <span aria-hidden="true" data-cat-state={state} className={cn("cx-cat", className)} />
    </>
  );
}
