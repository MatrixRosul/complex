import type { ComplexApi } from "./contract";
import { httpApi } from "./http";
import { mockApi } from "./mock";

/**
 * Перемикач реалізації.
 *
 * ⚠️ ДЕФОЛТ ТЕПЕР — РЕАЛЬНИЙ API (/api/v1). Моки лишились рівно для двох випадків:
 *   1) бекенд недоступний, а треба показати фронт (NEXT_PUBLIC_USE_MOCKS=true);
 *   2) роутери, яких ще НЕМАЄ (orders, delivery) — httpApi делегує їх у mock сам.
 *
 * Прапорець читається строго: моки вмикає тільки явне "true". Раніше було навпаки
 * (`!== "false"`), тому забутий/пустий env тихо повертав сайт на моки — і його
 * неможливо було відрізнити від живих даних, поки не звіриш ціни з БД.
 */
const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true";

export const api: ComplexApi = USE_MOCKS ? mockApi : httpApi;

export { USE_MOCKS };
export type { ComplexApi };
export * from "./types";
