/**
 * Money display. Mirrors backend/app/core/money.py — keep the two in step.
 *
 * The backend already sends a preformatted `*_display` string for every amount, and
 * that is what the UI should render. These helpers exist for the few places a number
 * has to be derived client-side (a progress bar, a chart axis), and they use the
 * Indian numbering system: ₹6,40,000, not ₹640,000. `toLocaleString('en-US')` is
 * wrong here and quietly produces a figure no Indian merchant would recognise.
 */

export function formatInr(paise: number): string {
  // Integer arithmetic, deliberately. `paise / 100` puts money through a float and
  // then rounds back — which is exactly the discipline the backend refuses to allow
  // (see the float guard in tests/architecture/test_layering.py). Splitting with
  // divmod on integers keeps every value exact, and keeps this helper honest with
  // the claim that the frontend does not do float arithmetic on currency.
  const abs = Math.abs(Math.trunc(paise));
  const whole = Math.floor(abs / 100);
  const frac = abs % 100;

  let s = String(whole);
  if (s.length > 3) {
    const tail = s.slice(-3);
    let head = s.slice(0, -3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    s = [...groups, tail].join(",");
  }

  const sign = paise < 0 ? "-" : "";
  return frac ? `${sign}₹${s}.${String(frac).padStart(2, "0")}` : `${sign}₹${s}`;
}

/** Compact form for tight metric tiles: ₹8.5L, ₹1.2Cr.
 *
 * Approximate by definition — it exists to fit a number into a tile, and says so by
 * showing two decimals of a lakh or crore. The exact figure always remains available
 * through `formatInr`, which is integer-based.
 */
export function formatInrShort(paise: number): string {
  const abs = Math.abs(Math.trunc(paise));
  const sign = paise < 0 ? "-" : "";
  // Compare in paise so the threshold test itself never rounds.
  if (abs >= 1_00_00_000_00) return `${sign}₹${(abs / 1_00_00_000_00).toFixed(2)}Cr`;
  if (abs >= 1_00_000_00) return `${sign}₹${(abs / 1_00_000_00).toFixed(2)}L`;
  return formatInr(paise);
}
