"use client";

import { cn } from "@/lib/utils";

/**
 * Анімована лисичка-маскот асистента. Тільки inline SVG + CSS — у проєкті
 * немає framer-motion/lottie/rive, і додавати їх заради однієї іконки не варто
 * (PLAN §0: заміна на Rive пізніше — це один файл, бо компонент ізольований).
 *
 * Чому keyframes у <style> тегу компонента, а не в globals.css:
 * globals.css — спільний файл (правиться окремо, див. інструкцію хвилі W1),
 * а Tailwind 4 не згенерує @keyframes для довільного `animate-[...]` —
 * вони мають існувати в CSS заздалегідь. Тому кадри лисички живуть локально,
 * у власному <style> компонента, під унікальним префіксом класів `cx-fox-*`,
 * щоб гарантовано не перетнутися з чимось глобальним.
 *
 * Дисципліна акценту (docs/research/DESIGN_SYSTEM.md §2.3: «акцент = тут гроші
 * / тут дія»): у стані idle й thinking лисичка руда, але ПРИГЛУШЕНА
 * (--brand-subtle-foreground) — вона не повинна конкурувати з кнопкою
 * «Купити». Повний --primary вмикається лише в hover і talking, тобто саме
 * тоді, коли лисичка сама стає закликом до дії (наведення / відповідь).
 */

type FoxState = "idle" | "hover" | "thinking" | "talking";

interface FoxProps {
  state: FoxState;
  className?: string;
}

/* Кадри й прив'язка анімацій до стану — заведені один раз як рядок і
   вставляються через <style>. Рендер компонента кілька разів на сторінці
   продублює тег стилю, але це нешкідливо (той самий CSS перезаписує сам
   себе); наразі лисичка монтується рівно один раз (providers.tsx). */
const FOX_STYLE = `
  /* transform-box: view-box переносить точку відліку в систему координат
     viewBox (0..64), тому origin для кожної частини заданий явно в px —
     інакше "center" означав би центр усього 64×64 квадрата, а не деталі. */
  .cx-fox-tail { transform-box: view-box; transform-origin: 40px 46px; }
  .cx-fox-head { transform-box: view-box; transform-origin: 32px 46px; }
  .cx-fox-ear-left { transform-box: view-box; transform-origin: 17px 21px; }
  .cx-fox-ear-right { transform-box: view-box; transform-origin: 47px 21px; }
  .cx-fox-muzzle { transform-box: view-box; transform-origin: 32px 40px; }
  /* Очі — власний bounding box кожного кола, тому тут якраз fill-box/center. */
  .cx-fox-eye { transform-box: fill-box; transform-origin: center; }

  @keyframes cx-fox-blink {
    0%, 90%, 100% { transform: scaleY(1); }
    95% { transform: scaleY(0.15); }
  }
  @keyframes cx-fox-ear-wiggle {
    0%, 100% { transform: rotate(0deg); }
    50% { transform: rotate(-10deg); }
  }
  @keyframes cx-fox-tail-sway {
    0%, 100% { transform: rotate(-6deg); }
    50% { transform: rotate(6deg); }
  }
  @keyframes cx-fox-head-turn {
    0%, 100% { transform: rotate(0deg) translateX(0); }
    50% { transform: rotate(-7deg) translateX(-2px); }
  }
  @keyframes cx-fox-ears-perk {
    0%, 100% { transform: translateY(0) scaleY(1); }
    50% { transform: translateY(-2px) scaleY(1.12); }
  }
  @keyframes cx-fox-head-tilt {
    0%, 100% { transform: rotate(-8deg); }
    50% { transform: rotate(8deg); }
  }
  @keyframes cx-fox-tail-spin {
    0%, 100% { transform: rotate(-12deg); }
    50% { transform: rotate(28deg); }
  }
  @keyframes cx-fox-muzzle-talk {
    0%, 100% { transform: scaleY(1) translateY(0); }
    50% { transform: scaleY(0.82) translateY(1.5px); }
  }

  .cx-fox[data-fox-state="idle"] .cx-fox-eye { animation: cx-fox-blink 4.4s ease-in-out infinite; }
  .cx-fox[data-fox-state="idle"] .cx-fox-ear-left { animation: cx-fox-ear-wiggle 4.4s ease-in-out infinite; }
  .cx-fox[data-fox-state="idle"] .cx-fox-tail { animation: cx-fox-tail-sway 3.6s ease-in-out infinite; }

  .cx-fox[data-fox-state="hover"] .cx-fox-head { animation: cx-fox-head-turn 1.4s ease-in-out infinite; }
  .cx-fox[data-fox-state="hover"] .cx-fox-ear-left,
  .cx-fox[data-fox-state="hover"] .cx-fox-ear-right { animation: cx-fox-ears-perk 1.4s ease-in-out infinite; }

  .cx-fox[data-fox-state="thinking"] .cx-fox-head { animation: cx-fox-head-tilt 1.8s ease-in-out infinite; }
  .cx-fox[data-fox-state="thinking"] .cx-fox-tail { animation: cx-fox-tail-spin 1s ease-in-out infinite; }

  .cx-fox[data-fox-state="talking"] .cx-fox-muzzle { animation: cx-fox-muzzle-talk 0.5s ease-in-out infinite; }

  @media (prefers-reduced-motion: reduce) {
    .cx-fox * { animation: none !important; transition: none !important; }
  }
`;

/** Білі патчі (щічки, груди, кінчик хвоста, середина вух) — завжди світлі,
 * незалежно від теми й стану: це видова ознака лисиці, а не акцентний колір. */
const FOX_WHITE = "#fdf6f0";
const FOX_DARK = "#241812";

export function Fox({ state, className }: FoxProps) {
  const isAccent = state === "hover" || state === "talking";

  return (
    <>
      <style>{FOX_STYLE}</style>
      <svg
        viewBox="0 0 64 64"
        aria-hidden="true"
        data-fox-state={state}
        className={cn(
          "cx-fox size-16 transition-colors duration-300 motion-reduce:transition-none",
          isAccent ? "text-primary" : "text-brand-subtle-foreground",
          className,
        )}
      >
        {/* Хвіст — пухнастий, з білим кінчиком.
            Статичний поворот (SVG-атрибут transform на внутрішній групі) і
            анімаційний (CSS transform на зовнішній .cx-fox-tail) навмисно
            розведені по різних елементах: CSS transform ПОВНІСТЮ замінює,
            а не додає до SVG-атрибута transform того самого вузла — якби
            обидва висіли на одній групі, анімація "стирала" б базовий
            поворот хвоста в кадрі. Обидва обертання — навколо тієї самої
            точки (40,46), тому кути просто додаються. */}
        <g className="cx-fox-tail">
          <g transform="rotate(-60 40 46)">
            <ellipse cx="38" cy="46" rx="24" ry="10" fill="currentColor" />
            <ellipse cx="62" cy="46" rx="9" ry="9" fill={FOX_WHITE} />
          </g>
        </g>

        {/* Тулуб/плечі + білі груди. Навмисно вписані у viewBox (0..64) — не
            покладаємось на overflow-visible, бо зовнішня кнопка-обгортка
            (fox-widget) цілком може мати свій overflow-hidden. */}
        <ellipse cx="32" cy="52" rx="20" ry="10" fill="currentColor" />
        <ellipse cx="32" cy="54" rx="10" ry="7" fill={FOX_WHITE} />

        {/* Голова — рухається як єдина група (hover: поворот, thinking: нахил). */}
        <g className="cx-fox-head">
          <g className="cx-fox-ear-left">
            <polygon points="8,20 22,2 26,24" fill="currentColor" />
            <polygon points="12,18 21,8 23,20" fill={FOX_WHITE} />
          </g>
          <g className="cx-fox-ear-right">
            <polygon points="56,20 42,2 38,24" fill="currentColor" />
            <polygon points="52,18 43,8 41,20" fill={FOX_WHITE} />
          </g>

          <circle cx="32" cy="28" r="17" fill="currentColor" />

          <g className="cx-fox-muzzle">
            {/* Гостра мордочка: трикутник додає загострений низ до круглої голови. */}
            <polygon points="17,33 47,33 32,50" fill="currentColor" />
            <ellipse cx="32" cy="39" rx="13" ry="11" fill={FOX_WHITE} />
            <polygon points="29,45 35,45 32,50" fill={FOX_DARK} />
          </g>

          <g className="cx-fox-eye">
            <circle cx="25" cy="26" r="2.6" fill={FOX_DARK} />
          </g>
          <g className="cx-fox-eye">
            <circle cx="39" cy="26" r="2.6" fill={FOX_DARK} />
          </g>
        </g>
      </svg>
    </>
  );
}
