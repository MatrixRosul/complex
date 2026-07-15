import { ApiError, type ApiLang, type Availability } from "./types";

/**
 * SSE-клієнт чату «Лисичка» (POST /assistant/chat).
 *
 * ⚠️ ЧОМУ НЕ EventSource. EventSource вміє лише GET і не дозволяє передати тіло
 * запиту інакше, ніж рядком у query — для повідомлення користувача (до 1000 символів)
 * це і незручно, і небезпечно (кеш проксі в URL). Бекенд стрімить POST-відповіддю
 * (`StreamingHttpResponse`, `text/event-stream` — PLAN §2.9), тож стрім читаємо руками:
 * `fetch(POST)` → `res.body.getReader()` → `TextDecoder` → парсинг кадрів `data: {...}\n\n`.
 * У проєкті це перший SSE/ReadableStream-клієнт — патерну звідки скалькувати не було.
 *
 * ⚠️ ФОРМАТ КАДРУ — ПРИПУЩЕННЯ, ЗАДОКУМЕНТОВАНЕ НАВМИСНЕ. `backend/assistant/api.py`
 * і `schemas.py` на момент написання цього файлу ще порожні (пишуться паралельно
 * агентом B1), тож точної форми SSE-кадру звірити з кодом не було змоги. Контракт
 * нижче — з PLAN §2.8/§2.9 (events: session · thinking · token · tool · products ·
 * link · done · error) плюс послідовний стиль «дискримінатор `type` + плоскі поля»
 * (session.token, token.text, tool.names, products.items, link.url/label — а не
 * вкладені об'єкти). Якщо бекенд віддасть інше — міняти рівно `parseEvent()` тут,
 * жодного компонента.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001/api/v1";

/**
 * Компактна картка товару в результаті інструмента (PLAN §2.5: «не 40 полів — це прямі
 * гроші на токенах»). Ціна — рядком, ніколи float (PLAN §1.5).
 */
export type AssistantProduct = {
  id: number;
  sku: string;
  name: string;
  price: string;
  old_price: string | null;
  availability: Availability;
  /** Шлях товару (канонічний, локалізований бекендом) — /{locale}/p/{id}/{slug}. */
  url: string;
  image: string | null;
};

export type AssistantEvent =
  | { type: "session"; token: string }
  | { type: "thinking" }
  | { type: "token"; text: string }
  | { type: "tool"; names: string[] }
  | { type: "products"; items: AssistantProduct[] }
  | { type: "link"; url: string; label: string | null }
  | { type: "done" }
  | { type: "error"; message: string };

export type ChatInput = {
  message: string;
  locale: ApiLang;
  /** null для першого повідомлення сесії — бекенд заведе сесію сам і поверне token у 'session'. */
  sessionToken: string | null;
};

/** Розбирає байтовий потік на сирі SSE-кадри (роздільник — порожній рядок). */
async function* readSseFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        // SSE дозволяє кілька рядків `data:` на один кадр — склеюємо через \n
        // (специфікація), хоча наш бекенд шле по одному JSON-об'єкту на кадр.
        const payload = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");

        if (payload) yield payload;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Валідує сирий JSON у типовану подію. Невідомий тип або зіпсоване поле — `null`,
 * а не виняток: один незрозумілий кадр не має рвати весь стрім користувачу.
 */
function parseEvent(raw: string): AssistantEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || !("type" in data)) return null;

  const d = data as Record<string, unknown>;

  switch (d.type) {
    case "session":
      return typeof d.token === "string" ? { type: "session", token: d.token } : null;
    case "thinking":
      return { type: "thinking" };
    case "token":
      return typeof d.text === "string" ? { type: "token", text: d.text } : null;
    case "tool":
      return {
        type: "tool",
        names: Array.isArray(d.names) ? d.names.map(String) : [],
      };
    case "products":
      return Array.isArray(d.items)
        ? { type: "products", items: d.items as AssistantProduct[] }
        : null;
    case "link":
      return typeof d.url === "string"
        ? { type: "link", url: d.url, label: typeof d.label === "string" ? d.label : null }
        : null;
    case "done":
      return { type: "done" };
    case "error":
      return { type: "error", message: typeof d.message === "string" ? d.message : "" };
    default:
      return null;
  }
}

/**
 * Стрімить відповідь асистента. Кожен `yield` — одна SSE-подія.
 *
 * Помилки транспорту (мережа впала / бекенд відповів не 2xx ще ДО початку стріму) —
 * через існуючий `ApiError`, як і решта `lib/api/*`. Помилки ВСЕРЕДИНІ стріму (бюджет
 * вичерпано, refusal, обрізана відповідь) бекенд шле подією `error` — це не виняток,
 * а звичайний елемент послідовності: чат-панель показує її як повідомлення асистента.
 */
export async function* streamChat(input: ChatInput): AsyncGenerator<AssistantEvent> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}/assistant/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: input.message,
        locale: input.locale,
        session_token: input.sessionToken,
      }),
    });
  } catch (cause) {
    throw new ApiError(0, "POST /assistant/chat → недоступний", cause);
  }

  if (!res.ok) {
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      payload = await res.text().catch(() => undefined);
    }
    throw new ApiError(res.status, `POST /assistant/chat → ${res.status}`, payload);
  }

  if (!res.body) {
    throw new ApiError(0, "POST /assistant/chat → порожній потік відповіді", undefined);
  }

  for await (const raw of readSseFrames(res.body)) {
    const event = parseEvent(raw);
    if (event) yield event;
  }
}
