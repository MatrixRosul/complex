/**
 * Нормалізація медіа-URL.
 *
 * ⚠️ ЩО ТУТ ЗМІНИЛОСЬ І ЧОМУ. Раніше ця функція ПРИКЛЕЮВАЛА origin бекенда:
 * `/media/banners/x.png` → `http://localhost:8001/media/banners/x.png`. Мотив був
 * здоровий (фронт на іншому порту — інакше 404), але результат — ЖОДНОГО видимого фото
 * на сайті: next/image резолвить хост і БЛОКУЄ loopback/приватні адреси (SSRF-захист),
 * віддаючи 400 «"url" parameter is not allowed» — НЕЗАЛЕЖНО від remotePatterns.
 *
 * Тепер `/media/**` віддає сам фронт (rewrite у next.config.ts → Django). Тому відносний
 * шлях лишається ВІДНОСНИМ: для браузера й next/image це локальне зображення на власному
 * origin — ні CORS, ні SSRF-guard, ні remotePatterns тут уже ні при чому.
 *
 * Абсолютні URL (R2 у проді, picsum у демо) не чіпаємо — але їхні хости МАЮТЬ бути
 * у next.config.ts::images.remotePatterns, інакше next/image кине помилку в рантаймі.
 *
 * ⚠️ Відносний шлях у og:image коректно абсолютизує Next — через `metadataBase`
 * (див. app/[locale]/layout.tsx). Без нього OG-картинки поїхали б на localhost:3000.
 */

export function mediaUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  // Абсолютний (R2 / picsum / data:) — віддаємо як є.
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  // Відносний з бекенда — лишається відносним: його проксує rewrite `/media/**`.
  return url.startsWith("/") ? url : `/${url}`;
}
