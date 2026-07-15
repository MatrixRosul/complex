"use client";

/**
 * Остання лінія оборони: помилка в КОРЕНЕВОМУ layout (там, де <html> і <body>).
 *
 * ⚠️ Сюди не долітають ні провайдери, ні словники, ні стилі — тому розмітка тут
 * навмисно примітивна й самодостатня, з інлайн-стилями і без жодного імпорту з @/…
 * Файл існує рівно для того, щоб замість БІЛОГО ЕКРАНА людина побачила текст і кнопку.
 * Мова — uk (дефолтна): локаль на цьому рівні вже невідома.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  console.error("[complex] global error:", error);

  return (
    <html lang="uk">
      <body
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "16px",
          fontFamily: "system-ui, sans-serif",
          padding: "24px",
          textAlign: "center",
        }}
      >
        <h1 style={{ fontSize: "24px", fontWeight: 600 }}>Сталася помилка</h1>
        <p style={{ maxWidth: "420px", color: "#666", fontSize: "14px", lineHeight: 1.5 }}>
          Сервіс тимчасово недоступний. Спробуйте ще раз за хвилину.
        </p>
        <button
          type="button"
          onClick={reset}
          style={{
            padding: "10px 20px",
            borderRadius: "8px",
            border: "1px solid #ccc",
            background: "#111",
            color: "#fff",
            cursor: "pointer",
            fontSize: "14px",
          }}
        >
          Спробувати ще раз
        </button>
      </body>
    </html>
  );
}
