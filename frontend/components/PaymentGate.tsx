"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { isPaymentRequired } from "@/lib/live-api";

/**
 * Sends a merchant to billing when the server says their workspace is unpaid.
 *
 * The payment gate returns 402 from every live route. Without this the merchant saw
 * "Choose a plan and confirm payment to activate your workspace" as an anonymous
 * error toast with nothing to click — told what to do and given no way to do it.
 *
 * Listening for unhandled rejections rather than wrapping every call site: the 402
 * can come from any of dozens of requests across the workspace, and a rule that has
 * to be remembered at each one is a rule that will be missed by the next page.
 *
 * Deliberately NOT part of `useSubscription`. That hook fails open so a network blip
 * cannot hide a merchant's own ledger from them; a redirect there would turn a
 * transient error into a lockout.
 */
export function PaymentGate() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    // Already where we would send them; redirecting again would loop.
    if (pathname.startsWith("/live/start")) return;

    function onRejection(event: PromiseRejectionEvent) {
      if (!isPaymentRequired(event.reason)) return;
      // Handled: without this the browser also logs it as uncaught noise.
      event.preventDefault();
      router.replace("/live/start?reason=payment_required");
    }

    window.addEventListener("unhandledrejection", onRejection);
    return () => window.removeEventListener("unhandledrejection", onRejection);
  }, [router, pathname]);

  return null;
}
