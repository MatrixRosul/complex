import type { NextConfig } from "next";

/**
 * ⚠️ МЕДІА ЙДЕ ЧЕРЕЗ ORIGIN ФРОНТА (rewrite /media/** → Django), а не напряму на :8001.
 *
 * ЧОМУ. Реальні дані дають два джерела фото:
 *   1) прод: Cloudflare R2 → АБСОЛЮТНІ https-URL (лишаються як є — див. remotePatterns);
 *   2) дев: Django на ІНШОМУ порту → ВІДНОСНІ шляхи `/media/…` (банери, іконки, фото товарів).
 *
 * Спокуса №1 — приклеїти origin бекенда (`http://localhost:8001/media/…`) і дописати цей хост
 * у remotePatterns. Саме так тут і було — і ЖОДНЕ фото на сайті не показувалось. Причина НЕ в
 * remotePatterns (вони збігались точно): next/image має SSRF-захист, який резолвить хост і
 * відмовляється проксувати все, що вказує на loopback/приватну адресу:
 *     /tmp/fe3001.log: upstream image http://localhost:8001/media/… resolved to private ip
 *                      ["::1","127.0.0.1"]
 *     GET /_next/image?url=…localhost%3A8001… → 400 «"url" parameter is not allowed»
 * У деві бекенд ЗАВЖДИ на loopback — тобто фото були биті ЗАВЖДИ, хоч що пиши в remotePatterns.
 *
 * Спокуса №2 — `images.dangerouslyAllowLocalIP: true`: вимкнути цей захист глобально заради
 * зручності в деві. Ми цього не робимо.
 *
 * Правильно — прибрати крос-origin як такий: `/media/**` віддає САМ фронт (rewrite → Django).
 * Для next/image це стає ЛОКАЛЬНИМ зображенням (same-origin): SSRF-guard не застосовується,
 * оптимізація працює. Бонусом зникає другий баг — `http://localhost:3001/media/…` більше не 404.
 * У проді той самий шлях віддає Caddy (або R2 абсолютним URL) — поведінка збігається з девом.
 */

/** http://localhost:8001/api/v1 → http://localhost:8001 */
function backendOrigin(): string {
  const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001/api/v1";
  try {
    return new URL(api).origin;
  } catch {
    return "http://localhost:8001";
  }
}

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      // Фото товарів у проді — Cloudflare R2 (S3-сумісний), django-storages.
      { protocol: "https", hostname: "**.r2.dev" },
      { protocol: "https", hostname: "**.cloudflarestorage.com" },
      // Тимчасовий хостинг (Heroku + Vercel) віддає медіа з Cloudinary, а не з R2:
      // R2 не активується без картки. Див. backend/config/settings/heroku.py.
      { protocol: "https", hostname: "res.cloudinary.com" },
      // Демо-фото товарів із seed_demo.
      { protocol: "https", hostname: "picsum.photos" },
    ],
    // AVIF першим: у побутовій техніці фото — це основна вага сторінки.
    formats: ["image/avif", "image/webp"],
  },

  /**
   * Медіа Django (банери, іконки категорій, обкладинки новин, фото товарів) віддаються
   * з origin фронта. Django лишається джерелом байтів, але браузер і next/image бачать
   * їх як свої, same-origin. Один рядок замість абсолютизації + remotePatterns + SSRF-винятків.
   */
  async rewrites() {
    return [
      {
        source: "/media/:path*",
        destination: `${backendOrigin()}/media/:path*`,
      },
      /**
       * Django Admin з origin'а фронта — ТІЛЬКИ для тимчасового хостингу (Vercel + Heroku),
       * де бекенд живе на окремому домені. У проді цього не потрібно: там Caddy віддає
       * і фронт, і Django з ОДНОГО домену, тому адмінка й так своя.
       *
       * ⚠️ Вимагає ADMIN_URL=admin/ на бекенді (за замовчуванням там непередбачуваний
       *    префікс — ARCHITECTURE §5). Тобто цей rewrite СВІДОМО повертає адмінку на
       *    вгадуваний шлях; прийнятно на кілька днів показу, не прийнятно в проді.
       *
       * ⚠️ /static/** — це НЕ статика Next (вона живе в /_next/static), а статика
       *    Django Admin, яку віддає whitenoise. Без цього рядка адмінка відкриється
       *    голим HTML без жодного стилю.
       */
      {
        source: "/admin/:path*",
        destination: `${backendOrigin()}/admin/:path*`,
      },
      {
        source: "/static/:path*",
        destination: `${backendOrigin()}/static/:path*`,
      },
      // Перемикач мови в адмінці (Unfold) постить саме сюди — див. config/urls.py.
      {
        source: "/i18n/:path*",
        destination: `${backendOrigin()}/i18n/:path*`,
      },
    ];
  },

  // Зменшує бандл: імпорт однієї іконки не тягне всю бібліотеку.
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
};

export default nextConfig;
