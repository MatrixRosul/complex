import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

/**
 * tailwind-merge, ДОВЧЕНИЙ НАШІЙ ТИПОГРАФІЧНІЙ ШКАЛІ.
 *
 * 🔴 БАГ, ЯКИЙ ЦЕ ЛІКУЄ (знайдено при задачі «Сайт §3» — «ціна більшого шрифту»).
 *
 * У проєкті є власні розміри тексту (globals.css @theme): `text-display`, `text-h1`,
 * `text-h2`, `text-h3`, `text-price-lg`, `text-price-xl`, `text-price-2xl`. І власні
 * кольори: `text-price`, `text-price-regular`, `text-price-old`.
 *
 * Ванільний twMerge про них не знає. Для нього `text-*` із незнайомим значенням — це
 * КОЛІР, тому `text-price-2xl` і `text-price-regular` попадали в одну групу конфлікту,
 * і вигравав останній. Результат:
 *
 *     cn("price", "text-price-2xl", "text-price-regular")  →  "price text-price-regular"
 *                  ^^^^^^^^^^^^^^^ розмір ЗНИКАВ
 *
 * Тобто <Price> рендерився БЕЗ жодного класу розміру, і ціна успадковувала кегль
 * оточення. Той самий механізм з'їдав `text-h2`/`text-h3` у будь-якому виклику cn(),
 * де поруч стояв колір.
 *
 * Чому це не помітили раніше: у більшості місць класи написані ОДНИМ РЯДКОМ у
 * className (`className="text-h1 text-foreground"`) — там cn() не викликається взагалі,
 * рядок їде в DOM як є, і розмір застосовується. Ламалось рівно те, що збиралось
 * через cn() — а це якраз <Price>, тобто ціни на всьому сайті.
 *
 * ⚠️ Реєструємо ТІЛЬКИ РОЗМІРИ. Кольори (`text-price`, `text-price-old`, `text-stock-*`
 * тощо) чіпати не треба: дефолтна класифікація «незнайоме text-* = колір» для них і є
 * правильною. Додавати сюди новий ступінь шкали ПОТРІБНО одночасно з токеном у
 * globals.css — інакше він знову почне тихо зникати.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        { text: ["display", "h1", "h2", "h3", "price-lg", "price-xl", "price-2xl"] },
      ],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
