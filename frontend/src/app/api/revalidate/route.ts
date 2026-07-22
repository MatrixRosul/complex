import { revalidateTag } from "next/cache";
import { NextResponse } from "next/server";

/**
 * СКИДАННЯ КЕШУ ФРОНТУ НА ВИМОГУ БЕКЕНДА.
 *
 * ⚠️ НАВІЩО. Дані каталогу кешуються в Next на годину (`TTL.tree = 3600`), тому зміна
 * в адмінці доїжджала до сайту із затримкою до 60 хв. Для замовника це виглядало як
 * «не працює»: він завантажував емблему категорії, оновлював сторінку — і бачив старий
 * типовий значок. Те саме било по банерах, назвах і цінах.
 *
 * Змінна `NEXT_REVALIDATE_URL` існувала в конфігу давно, але самого маршруту НЕ БУЛО,
 * і бекенд його не викликав — тобто механізм був лише на папері.
 *
 * Ендпоінт навмисно дурний: приймає перелік тегів і скидає їх. Жодної логіки, ЩО саме
 * інвалідувати, тут немає — це знає бекенд, який і змінив дані.
 */
export async function POST(request: Request) {
  const secret = process.env.NEXT_REVALIDATE_SECRET;

  // ⚠️ Порожній секрет НЕ означає «пускати всіх»: тоді будь-хто ззовні міг би
  // безкоштовно скидати кеш і ганяти нам бекенд. Немає секрета — маршрут вимкнений.
  if (!secret) {
    return NextResponse.json({ error: "revalidate disabled" }, { status: 503 });
  }
  if (request.headers.get("x-revalidate-secret") !== secret) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  let tags: string[] = [];
  try {
    const body = (await request.json()) as { tags?: unknown };
    if (Array.isArray(body.tags)) {
      tags = body.tags.filter((t): t is string => typeof t === "string" && t.length > 0);
    }
  } catch {
    return NextResponse.json({ error: "bad json" }, { status: 400 });
  }

  if (tags.length === 0) {
    return NextResponse.json({ error: "no tags" }, { status: 400 });
  }

  // ⚠️ Другий аргумент обов'язковий з Next 16 — це профіль часу життя кешу, записи
  // якого чистити. "max" = найширший: скидаємо всі записи з цим тегом незалежно від
  // того, з яким TTL їх поклали. Саме те, що треба для «оновити зараз».
  for (const tag of tags) revalidateTag(tag, "max");

  return NextResponse.json({ revalidated: tags });
}
