import { NextResponse, type NextRequest } from "next/server";
import { defaultLocale, isLocale, localeAliases } from "@/i18n/config";

/**
 * Проксі (у Next ≤15 цей файл називався middleware.ts).
 *
 * Локаль — ЗАВЖДИ в шляху, ніколи в куках чи Accept-Language.
 *
 * ⚠️ Свідоме рішення (ARCHITECTURE §2.2): якби ми обирали мову за заголовком,
 * одна й та сама URL віддавала б різний контент різним людям — і кеш (Next ISR,
 * Caddy, CDN) або кешував би не ту мову, або мусив би Vary: Accept-Language,
 * що вбиває hit-rate. Тому /uk/… і /ru/… — це фізично різні сторінки.
 *
 * Тут ми лише:
 *   1) редіректимо голий шлях на дефолтну локаль:  /cart    → /uk/cart  (307)
 *   2) нормалізуємо аліаси:                        /ua/cart → /uk/cart  (308)
 */
export default function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const segments = pathname.split("/");
  const first = segments[1]?.toLowerCase() ?? "";

  // Уже канонічна локаль — пропускаємо.
  if (isLocale(first)) {
    /**
     * ⚠️ ПОДВІЙНА ЛОКАЛЬ (/uk/uk/…, /uk/ru/…, /ru/uk/…) — СХЛОПУЄМО.
     *
     * `localePath()` уже нормалізує посилання, які генерує сам застосунок, — але це
     * захищає РІВНО ті посилання, що ми відрендерили. Прямий перехід за таким URL
     * (стара закладка, посилання з розсилки чи месенджера, зовнішній сайт, редірект зі
     * старого фронта) middleware пропускав як є: Next шукав роут `[locale]/uk/...`,
     * якого не існує, і віддавав ЖОРСТКИЙ 404 без жодного редіректу.
     *
     * Перший сегмент — джерело правди (людина прийшла саме за цією мовою), зайвий
     * другий викидаємо. 308, а не 307: посилання-дублікати не мусять жити в індексі
     * пошуковика.
     */
    const second = segments[2]?.toLowerCase() ?? "";
    if (isLocale(second) || second in localeAliases) {
      segments.splice(2, 1);
      const collapsed = segments.join("/") || `/${first}`;
      return NextResponse.redirect(new URL(`${collapsed}${search}`, request.url), 308);
    }

    /**
     * ⚠️ `x-pathname` — щоб layout міг дізнатись повний шлях.
     *
     * Потрібно рівно одному місцю: catalog/layout.tsx перевіряє існування категорії
     * ДО Suspense-межі (інакше неіснуюча категорія віддає soft-404 — 200 замість 404;
     * докладніше в самому layout). Сегменти [...slug] належать дочірньому роуту, і в
     * params самого layout їх немає — а заголовок є.
     */
    const headers = new Headers(request.headers);
    headers.set("x-pathname", pathname);
    return NextResponse.next({ request: { headers } });
  }

  // Аліас (/ua → /uk) — ПОСТІЙНИЙ редірект, щоб не плодити дублі для SEO.
  const alias = localeAliases[first];
  if (alias) {
    segments[1] = alias;
    return NextResponse.redirect(new URL(`${segments.join("/")}${search}`, request.url), 308);
  }

  // Голий шлях — дописуємо дефолтну локаль. 307, а не 308: дефолтна локаль
  // теоретично може змінитись, і кешувати цей редірект назавжди не варто.
  return NextResponse.redirect(
    new URL(`/${defaultLocale}${pathname === "/" ? "" : pathname}${search}`, request.url),
    307,
  );
}

export const config = {
  matcher: [
    /**
     * Усе, крім:
     *   /api/*    — проксі-роути (якщо з'являться)
     *   /_next/*  — бандли й оптимізовані зображення
     *   /images/* — статика
     *   /admin/*  — Django Admin, який next.config.ts проксує на бекенд
     *   /static/* — статика адмінки звідти ж (whitenoise)
     *   файли з розширенням (favicon.ico, robots.txt, sitemap.xml, *.svg)
     *
     * ⚠️ admin і static тут ОБОВ'ЯЗКОВІ: без них цей proxy припише локаль
     *    (/admin → /uk/admin, 307) ЩЕ ДО rewrite'у, і адмінка стане недосяжною —
     *    Next віддасть 404 своєї сторінки замість того, щоб сходити на Django.
     */
    "/((?!api|_next|images|admin|static|.*\\..*).*)",
  ],
};
