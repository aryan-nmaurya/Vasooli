"use client";

import { useEffect } from "react";

/**
 * The last resort: an error thrown by the root layout itself.
 *
 * This one replaces the whole document, so unlike `error.tsx` it must render its own
 * `<html>` and `<body>` — the layout that would normally provide them is the thing
 * that failed. It also cannot use the app's fonts, providers or theme tokens for the
 * same reason, so the styles here are deliberately inline and self-contained, and the
 * colours are chosen to be legible on either a light or dark browser default.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Global error:", error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#0b0b0c",
          color: "#f4f4f5",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
        }}
      >
        <main style={{ maxWidth: "32rem", padding: "2rem", textAlign: "center" }}>
          <p
            style={{
              margin: 0,
              fontSize: "11px",
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: "#8a8a92",
            }}
          >
            Vasooli
          </p>
          <h1 style={{ margin: "0.75rem 0", fontSize: "1.5rem", fontWeight: 600 }}>
            The application failed to start.
          </h1>
          <p style={{ margin: "0 0 1.5rem", lineHeight: 1.6, color: "#c4c4cc" }}>
            Nothing has changed in your ledger. This is a problem loading the page
            itself.
          </p>
          {error.digest ? (
            <p style={{ fontFamily: "monospace", fontSize: "11px", color: "#8a8a92" }}>
              Reference: {error.digest}
            </p>
          ) : null}
          <button
            onClick={reset}
            style={{
              marginTop: "0.5rem",
              padding: "0.5rem 1rem",
              borderRadius: "6px",
              border: "none",
              background: "#f4f4f5",
              color: "#0b0b0c",
              fontSize: "14px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
