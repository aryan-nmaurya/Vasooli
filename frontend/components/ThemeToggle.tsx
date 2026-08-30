"use client";

/**
 * Light/dark switch.
 *
 * The chosen theme lives on `<html data-theme>`, which is what the CSS variables key
 * off, and is applied by an inline script before first paint so the page never flashes
 * the wrong theme.
 *
 * Read with `useSyncExternalStore` rather than an effect. The DOM attribute IS the
 * source of truth here — set by that inline script before React exists — and copying it
 * into state inside an effect means rendering once with the wrong value and then
 * re-rendering, which React flags for exactly that reason.
 */

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "vasooli-theme";

function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => observer.disconnect();
}

function getSnapshot(): Theme {
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "light"
    : "dark";
}

//: Rendered on the server, where there is no document. The inline script corrects it
//: before paint, so this value is never visible.
function getServerSnapshot(): Theme {
  return "dark";
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    // Writing the attribute is the whole state change — the MutationObserver above
    // notices and re-renders. There is no second copy of this value to keep in step.
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private browsing blocks localStorage; the toggle still works for this session.
    }
  }

  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      className="grid size-9 place-items-center rounded-lg border border-line bg-panel text-ink-3 transition hover:bg-panel-2 hover:text-ink"
    >
      {theme === "dark" ? (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
    </button>
  );
}
