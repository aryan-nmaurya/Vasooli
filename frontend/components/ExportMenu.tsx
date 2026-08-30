"use client";

/**
 * Export dropdown.
 *
 * A plain <a download> would be simpler, but the download route is session-gated and a
 * failure there returns JSON rather than a file — which a bare link would hand the
 * browser as a corrupt spreadsheet. Fetching first means an expired session or a
 * backend error surfaces as a message instead of an unopenable file.
 */

import { useEffect, useRef, useState } from "react";

type Format = "csv" | "xlsx" | "pdf";
type Dataset = "recovered" | "overview" | "invoices";

const FORMATS: { id: Format; label: string }[] = [
  { id: "csv", label: "CSV" },
  { id: "xlsx", label: "Excel" },
  { id: "pdf", label: "PDF" },
];

export type ExportGroup = {
  dataset: Dataset;
  label: string;
  hint: string;
  /** Filters to carry through, so a download matches what is on screen. */
  params?: Record<string, string | null | undefined>;
};

export function ExportMenu({
  groups,
  label = "Export",
}: {
  groups: ExportGroup[];
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && !containerRef.current?.contains(target)) setOpen(false);
    }
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  async function download(group: ExportGroup, format: Format) {
    setBusy(`${group.dataset}:${format}`);
    setError(null);
    try {
      const query = new URLSearchParams({ format });
      for (const [key, value] of Object.entries(group.params ?? {})) {
        if (value) query.set(key, value);
      }
      const res = await fetch(`/api/download/export/${group.dataset}?${query}`);
      if (!res.ok) {
        setError(res.status === 401 ? "Session expired — sign in again." : "Export failed.");
        return;
      }

      // The filename the backend chose, taken from Content-Disposition so the saved
      // file carries its dataset and timestamp rather than a generic name.
      const disposition = res.headers.get("content-disposition") ?? "";
      const match = /filename="?([^"]+)"?/.exec(disposition);
      const filename = match?.[1] ?? `vasooli-${group.dataset}.${format}`;

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Revoked on the next tick: revoking synchronously can cancel the download in
      // some browsers before it has read the blob.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setOpen(false);
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink"
      >
        <svg
          aria-hidden
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <path d="M7 8l5-5 5 5" />
          <path d="M12 3v12" />
        </svg>
        {label}
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+0.4rem)] z-50 w-72 overflow-hidden rounded-xl border border-line bg-panel shadow-xl"
        >
          {groups.map((group) => (
            <div key={group.dataset} className="border-b border-line-2 last:border-b-0">
              <div className="px-3.5 pb-1 pt-2.5">
                <div className="text-xs font-medium text-ink">{group.label}</div>
                <div className="text-[11px] leading-snug text-ink-3">{group.hint}</div>
              </div>
              <div className="flex gap-1 px-2.5 pb-2.5 pt-1">
                {FORMATS.map((f) => (
                  <button
                    key={f.id}
                    role="menuitem"
                    onClick={() => void download(group, f.id)}
                    disabled={busy !== null}
                    className="flex-1 rounded-md px-2 py-1.5 text-xs text-ink-2 ring-1 ring-inset ring-line transition hover:bg-panel-2 hover:text-ink disabled:opacity-50"
                  >
                    {busy === `${group.dataset}:${f.id}` ? "…" : f.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {error ? (
        <span className="absolute right-0 top-[calc(100%+0.4rem)] z-50 whitespace-nowrap rounded-md border border-rose-300 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
          {error}
        </span>
      ) : null}
    </div>
  );
}
