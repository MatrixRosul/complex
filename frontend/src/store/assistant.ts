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
 */

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

  open: () => void;
  close: () => void;
  toggle: () => void;

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

      open: () => set({ isOpen: true }),
      close: () => set({ isOpen: false }),
      toggle: () => set((state) => ({ isOpen: !state.isOpen })),

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
      // Явний whitelist: isOpen/isStreaming/streamPhase НІКОЛИ не потрапляють
      // у localStorage, навіть якщо хтось випадково розширить стор.
      partialize: (state) => ({ sessionToken: state.sessionToken, messages: state.messages }),
    },
  ),
);
