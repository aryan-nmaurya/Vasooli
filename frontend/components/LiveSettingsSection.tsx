import type { ReactNode } from "react";

/** Every settings section opens the same way, so the rail is not the only thing tying them together. */
export function SettingsSectionHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-4">{eyebrow}</p>
      <h2 className="mt-1.5 text-lg font-semibold tracking-[-0.02em] text-ink">{title}</h2>
      <p className="mt-1 max-w-2xl text-sm leading-6 text-ink-3">{description}</p>
    </div>
  );
}

const TONES = {
  error: "border-rose-500/30 bg-rose-500/10 text-rose-800 dark:text-rose-200",
  warning: "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200",
  success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
} as const;

export function SettingsAlert({
  tone,
  children,
}: {
  tone: keyof typeof TONES;
  children: ReactNode;
}) {
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={`break-words rounded-xl border px-4 py-3 text-sm ${TONES[tone]}`}
    >
      {children}
    </p>
  );
}

/** A titled card. Used for every form and list so spacing and borders never drift. */
export function SettingsCard({
  title,
  hint,
  children,
  className = "",
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-panel ${className}`}>
      <div className="border-b border-line px-5 py-4">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {hint ? <p className="mt-1 text-xs leading-5 text-ink-4">{hint}</p> : null}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

export const fieldClass =
  "mt-1 w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/25";

export const labelClass = "block text-sm font-medium text-ink-2";

export const primaryButtonClass =
  "inline-flex min-h-9 items-center justify-center rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50";

export const secondaryButtonClass =
  "inline-flex min-h-9 items-center justify-center rounded-lg border border-line px-4 py-2.5 text-sm font-medium text-ink-2 transition hover:border-accent hover:text-ink disabled:opacity-50";
