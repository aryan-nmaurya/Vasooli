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
  const rupees = Math.abs(paise) / 100;
  const whole = Math.floor(rupees);
  const frac = Math.round((rupees - whole) * 100);

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

/** Compact form for tight metric tiles: ₹8.5L, ₹1.2Cr. */
export function formatInrShort(paise: number): string {
  const rupees = Math.abs(paise) / 100;
  const sign = paise < 0 ? "-" : "";
  if (rupees >= 1_00_00_000) return `${sign}₹${(rupees / 1_00_00_000).toFixed(2)}Cr`;
  if (rupees >= 1_00_000) return `${sign}₹${(rupees / 1_00_000).toFixed(2)}L`;
  return formatInr(paise);
}
