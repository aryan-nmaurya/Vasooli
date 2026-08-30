"use client";

/**
 * Two-step ledger import: preview, then commit.
 *
 * The preview is the reason this exists as a component rather than a file input.
 * Importing four hundred rows blind and discovering afterwards that row 47 was
 * malformed is the version that wastes an afternoon — so the first upload only parses
 * and reports, and nothing is written until the merchant has seen what will happen.
 *
 * Errors carry the spreadsheet line number, because "a row is invalid" sends someone
 * bisecting the file by hand.
 *
 * Shaped as a dropdown to sit beside Export in the page header: import and export are
 * the same job in two directions, and a merchant looking for one looks where the other
 * is. Panel state survives closing, so dismissing it mid-preview does not throw away a
 * parse the operator was still reading.
 */

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

type Problem = { line: number; invoice_number: string; message: string };

type Preview = {
  filename: string;
  parsed: number;
  problems: Problem[];
  unknown_columns: string[];
  sample: string[];
  duplicates: string[];
  would_import: number;
};

type Result = {
  ingested: number;
  skipped_duplicates: number;
  failed: number;
  customers_created: number;
};

export function ImportLedger() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState<null | "preview" | "commit">(null);
  const [error, setError] = useState<string | null>(null);

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

  async function send(chosen: File, dryRun: boolean) {
    setBusy(dryRun ? "preview" : "commit");
    setError(null);
    try {
      const body = new FormData();
      body.set("file", chosen);
      body.set("dry_run", dryRun ? "true" : "false");

      const res = await fetch("/api/upload", { method: "POST", body });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? data.error ?? "Import failed.");
        return;
      }
      if (dryRun) {
        setPreview(data as Preview);
        setResult(null);
      } else {
        setResult(data.result as Result);
        setPreview(null);
        setFile(null);
        if (inputRef.current) inputRef.current.value = "";
        router.refresh();
      }
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(null);
    }
  }

  function reset() {
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <div ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
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
          <path d="M7 10l5 5 5-5" />
          <path d="M12 15V3" />
        </svg>
        Import
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="Import invoices"
          // Anchored to the button's right edge, except on narrow screens: the header
          // wraps there and the button lands near the left, so a right-anchored panel
          // this wide runs off the left edge and takes the file input with it.
          className="absolute left-0 right-auto top-[calc(100%+0.4rem)] z-50 w-[27rem] max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-panel p-4 shadow-xl sm:left-auto sm:right-0"
        >
          <div className="flex items-center gap-x-3">
            <h2 className="text-sm font-semibold text-ink">Import invoices</h2>
            {/* A real <a>, not next/link: this is a file download, not a page. Link
                would client-side route to an API path and render nothing. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a
              href="/api/download/invoices/import/template"
              className="ml-auto text-xs text-ink-3 underline-offset-2 hover:text-ink hover:underline"
            >
              Download template CSV
            </a>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-3">
            Upload a receivables ledger as CSV. Nothing is written until you have seen what
            will happen — the first step only reads the file and reports what it found.
            Invoices already in the ledger are left untouched.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              aria-label="Ledger CSV"
              onChange={(e) => {
                const chosen = e.target.files?.[0] ?? null;
                setFile(chosen);
                setPreview(null);
                setResult(null);
                setError(null);
                if (chosen) void send(chosen, true);
              }}
              className="max-w-full text-xs text-ink-2 file:mr-3 file:rounded-md file:border-0 file:bg-invert file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-invert-ink hover:file:opacity-90"
            />
            {busy === "preview" ? <span className="text-xs text-ink-3">Reading…</span> : null}
          </div>

          {preview ? (
            <div className="mt-3 flex flex-col gap-2.5 rounded-lg border border-line bg-surface px-3.5 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
                <span className="font-medium text-ink">{preview.filename}</span>
                <span className="text-ink-2">
                  <strong className="text-ink">{preview.would_import}</strong> to import
                </span>
                {preview.duplicates.length ? (
                  <span className="text-ink-3">
                    {preview.duplicates.length} already in the ledger, will be skipped
                  </span>
                ) : null}
                {preview.problems.length ? (
                  <span className="text-amber-700 dark:text-amber-400">
                    {preview.problems.length} row
                    {preview.problems.length === 1 ? "" : "s"} cannot be read
                  </span>
                ) : null}
              </div>

              {preview.problems.length ? (
                <div className="max-h-40 overflow-y-auto rounded-md border border-line-2">
                  <table className="w-full text-[11px]">
                    <tbody className="divide-y divide-line-2">
                      {preview.problems.map((p) => (
                        <tr key={`${p.line}-${p.invoice_number}`}>
                          <td className="whitespace-nowrap px-2 py-1.5 font-mono text-ink-3">
                            line {p.line}
                          </td>
                          <td className="px-2 py-1.5 font-mono text-ink-2">{p.invoice_number}</td>
                          <td className="px-2 py-1.5 text-ink-3">{p.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}

              {preview.unknown_columns.length ? (
                <p className="text-[11px] leading-snug text-ink-3">
                  Ignored columns: {preview.unknown_columns.join(", ")}
                </p>
              ) : null}

              <div className="flex flex-wrap items-center gap-2 pt-0.5">
                <button
                  onClick={() => file && void send(file, false)}
                  disabled={busy !== null || preview.would_import === 0}
                  className="rounded-md bg-invert px-3 py-1.5 text-xs font-medium text-invert-ink transition hover:opacity-90 disabled:opacity-40"
                >
                  {busy === "commit"
                    ? "Importing…"
                    : preview.would_import === 0
                      ? "Nothing new to import"
                      : `Import ${preview.would_import} invoice${preview.would_import === 1 ? "" : "s"}`}
                </button>
                <button
                  onClick={reset}
                  disabled={busy !== null}
                  className="rounded-md px-3 py-1.5 text-xs text-ink-3 transition hover:text-ink disabled:opacity-40"
                >
                  Cancel
                </button>
              </div>
              {preview.problems.length ? (
                <span className="text-[11px] text-ink-3">
                  Rows that cannot be read are skipped; the rest still import.
                </span>
              ) : null}
            </div>
          ) : null}

          {result ? (
            <p className="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 px-3.5 py-2.5 text-xs text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">
              Imported <strong>{result.ingested}</strong> invoice
              {result.ingested === 1 ? "" : "s"}
              {result.customers_created ? `, ${result.customers_created} new customer(s)` : ""}
              {result.skipped_duplicates ? `, ${result.skipped_duplicates} already present` : ""}.
            </p>
          ) : null}

          {error ? (
            <p className="mt-3 rounded-lg border border-rose-300 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
