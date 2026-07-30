import localFont from "next/font/local";

/**
 * Montserrat Variable, self-hosted.
 *
 * ⚠️ ЗАМІНИВ INTER НА ПРЯМИЙ ЗАПИТ ЗАМОВНИКА (задача «Сайт §2»: «змінити шрифт на той
 * що у прикладі»). Референс — zhuk.ua: там `--font-body: "Montserrat", sans-serif`,
 * підключений з fonts.googleapis.com. Ми беремо той самий шрифт, але НЕ з CDN:
 * .woff2 лежить у репозиторії, тож білд не залежить від мережі, а рантайм — від
 * сторонніх доменів (те саме правило, що діяло для Inter).
 *
 * Файли зібрані з офіційного google/fonts `Montserrat[wght].ttf` (OFL) через
 * pyftsubset: сабсет latin + latin-ext + cyrillic + cyrillic-ext, вісь wght 100–900
 * ЗБЕРЕЖЕНА (не інстансовано). Перевірено після сабсету:
 *   • і ї є ґ ’ № — на місці (на них ламаються дешеві гротески);
 *   • ₴ (U+20B4) — на місці. Без нього ціни на сайті були б без символу гривні;
 *   • фіча `tnum` — на місці, вона критична для цін у сітці (див. .tnum у globals.css).
 *
 * Побічний виграш: 166 + 180 КБ проти 352 + 388 КБ в Inter — сабсет під наші дві мови
 * вдвічі легший за повний реліз Inter, який тягнув усі сабсети, включно з грецькою й
 * в'єтнамською, що на цьому сайті не використовуються.
 *
 * ⚠️ `cv05` з font-feature-settings прибрано: це фіча Inter (варіант «1» без засічки),
 * у Montserrat її немає — лишався б мертвий декларатив. Лишився `tnum`.
 */
export const montserrat = localFont({
  src: [
    { path: "./fonts/Montserrat.woff2", weight: "100 900", style: "normal" },
    { path: "./fonts/Montserrat-Italic.woff2", weight: "100 900", style: "italic" },
  ],
  variable: "--font-sans",
  display: "swap",
  preload: true,
  fallback: ["system-ui", "Segoe UI", "Roboto", "sans-serif"],
});
