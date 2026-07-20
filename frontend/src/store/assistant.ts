"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { AssistantProduct } from "@/lib/api/assistant";

/**
 * ⚠️ У localStorage — ІСТОРІЯ ЧАТУ Й ТОКЕН СЕСІЇ, НАВМИСНЕ ЦІЛКОМ (на відміну від
 * cart/wishlist/compare, див. store/cart.ts: там у localStorage лишається ЛИШЕ id,
 * бо ціни протухають, а сторінку можна перезапитати в бекенд у будь-який момент).
 * Відповідь асистента протухнути не може — це живий текст, згенерований один раз
 * і за гроші (Claude API, PLAN §1.3). Перечитати її звідкись, крім localStorage,
 * ніде: історії розмов на бекенді для анонімного відвідувача не існує.
 *
 * `isOpen` / `isStreaming` / `streamPhase` — свідомо ПОЗА partialize: чи відкрита
 * панель і чи триває стрім — стан поточної вкладки, а не те, що має пережити
 * перезавантаження сторінки (інакше після F5 користувач побачив би панель, що
 * «вічно друкує», бо реальний fetch-стрім обірвався разом зі сторінкою).
 *
 * НУДЖ (проактивна бульбашка «Я ШІ-помічник, допомогти?»):
 *   `hasOpenedChat` / `nudgeDismissedAt` — persist (переживають F5): якщо людина ХОЧ РАЗ
 *      відкрила чат, більше не смикаємо ніколи; після закриття бульбашки — пауза NUDGE_COOLDOWN_MS.
 *   `nudgeVisible` — ПОЗА partialize: бульбашка видима лише в поточній вкладці, не «оживає» після F5.
 * ⚠️ Версію стору НЕ піднімаємо: нові поля відсутні у збереженому v1, і zustand змерджить їх
 *    з початкових значень (shallow-merge persisted поверх initialState) — історія чату не втрачається.
 */

/** Пауза між показами бульбашки після того, як користувач її закрив. */
export const NUDGE_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000; // 7 днів

export type AssistantLink = {
  url: string;
  label: string | null;
};

export type AssistantMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  products?: AssistantProduct[];
  link?: AssistantLink | null;
  /** true, якщо це повідомлення — SSE-подія `error` (бюджет, refusal, мережа). */
  isError?: boolean;
  createdAt: number;
};

/** Стан лисички під час стріму: `thinking` на SSE thinking/tool, `talking` — поки йдуть token. */
export type StreamPhase = "idle" | "thinking" | "talking";

type AssistantState = {
  sessionToken: string | null;
  messages: AssistantMessage[];
  isOpen: boolean;
  isStreaming: boolean;
  streamPhase: StreamPhase;

  /** Чи відкривав користувач чат хоч раз. true → нудж більше не показуємо ніколи. */
  hasOpenedChat: boolean;
  /** Коли востаннє закрили бульбашку (для паузи NUDGE_COOLDOWN_MS). null → ще не закривали. */
  nudgeDismissedAt: number | null;
  /** Чи видима бульбашка ЗАРАЗ (стан вкладки, не persist). */
  nudgeVisible: boolean;

  open: () => void;
  close: () => void;
  toggle: () => void;

  /** Показати бульбашку (кличе хук-тригер за сигналом залученості). */
  showNudge: () => void;
  /** Користувач закрив бульбашку хрестиком — ставимо паузу, ховаємо. */
  dismissNudge: () => void;

  setSessionToken: (token: string) => void;
  setStreaming: (value: boolean) => void;
  setStreamPhase: (phase: StreamPhase) => void;

  addUserMessage: (text: string) => string;
  /** Порожнє повідомлення-заглушка асистента, у яке далі дописуються токени. */
  addAssistantPlaceholder: () => string;
  appendAssistantToken: (id: string, delta: string) => void;
  setAssistantProducts: (id: string, products: AssistantProduct[]) => void;
  setAssistantLink: (id: string, link: AssistantLink) => void;
  setAssistantError: (id: string, message: string) => void;

  /** Нова розмова: історія й токен сесії скидаються, бекенд заведе нову сесію сам. */
  reset: () => void;
};

const makeId = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export const useAssistantStore = create<AssistantState>()(
  persist(
    (set) => ({
      sessionToken: null,
      messages: [],
      isOpen: false,
      isStreaming: false,
      streamPhase: "idle",
      hasOpenedChat: false,
      nudgeDismissedAt: null,
      nudgeVisible: false,

      // Відкриття чату (будь-яким шляхом) — це «людина знає про асистента»: гасимо бульбашку
      // назавжди. Тому hasOpenedChat виставляється і в open, і в toggle (коли toggle відкриває).
      open: () => set({ isOpen: true, hasOpenedChat: true, nudgeVisible: false }),
      close: () => set({ isOpen: false }),
      toggle: () =>
        set((state) =>
          state.isOpen
            ? { isOpen: false }
            : { isOpen: true, hasOpenedChat: true, nudgeVisible: false },
        ),

      showNudge: () => set({ nudgeVisible: true }),
      dismissNudge: () => set({ nudgeVisible: false, nudgeDismissedAt: Date.now() }),

      setSessionToken: (token) => set({ sessionToken: token }),
      setStreaming: (value) => set({ isStreaming: value }),
      setStreamPhase: (phase) => set({ streamPhase: phase }),

      addUserMessage: (text) => {
        const id = makeId();
        set((state) => ({
          messages: [...state.messages, { id, role: "user", text, createdAt: Date.now() }],
        }));
        return id;
      },

      addAssistantPlaceholder: () => {
        const id = makeId();
        set((state) => ({
          messages: [
            ...state.messages,
            { id, role: "assistant", text: "", createdAt: Date.now() },
          ],
        }));
        return id;
      },

      appendAssistantToken: (id, delta) =>
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id ? { ...m, text: m.text + delta } : m,
          ),
        })),

      setAssistantProducts: (id, products) =>
        set((state) => ({
          messages: state.messages.map((m) => (m.id === id ? { ...m, products } : m)),
        })),

      setAssistantLink: (id, link) =>
        set((state) => ({
          messages: state.messages.map((m) => (m.id === id ? { ...m, link } : m)),
        })),

      setAssistantError: (id, message) =>
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === id ? { ...m, text: message, isError: true } : m,
          ),
        })),

      reset: () => set({ sessionToken: null, messages: [] }),
    }),
    {
      name: "complex.assistant",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      // Явний whitelist: isOpen/isStreaming/streamPhase/nudgeVisible НІКОЛИ не потрапляють
      // у localStorage (стан вкладки). hasOpenedChat/nudgeDismissedAt — навпаки, persist:
      // рішення «не смикати цю людину» має пережити перезавантаження й нові вкладки.
      partialize: (state) => ({
        sessionToken: state.sessionToken,
        messages: state.messages,
        hasOpenedChat: state.hasOpenedChat,
        nudgeDismissedAt: state.nudgeDismissedAt,
      }),
    },
  ),
);
