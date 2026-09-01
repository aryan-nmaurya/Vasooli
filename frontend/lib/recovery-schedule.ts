const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

/** Next daily recovery cycle: 10:00 in Asia/Kolkata (which has no DST). */
export function nextRecoveryCycle(now = new Date()) {
  const istNow = new Date(now.getTime() + IST_OFFSET_MS);
  const istCycle = new Date(istNow);
  istCycle.setUTCHours(10, 0, 0, 0);
  if (istCycle.getTime() <= istNow.getTime()) istCycle.setUTCDate(istCycle.getUTCDate() + 1);
  return new Date(istCycle.getTime() - IST_OFFSET_MS);
}

export function formatRecoveryCycle(value: Date) {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(value) + " IST";
}
