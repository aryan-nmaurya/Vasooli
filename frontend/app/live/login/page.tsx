import Link from "next/link";

import { LiveLoginForm } from "@/components/LiveLoginForm";

export default function LiveLoginPage() {
  return <main className="auth-page"><div><LiveLoginForm /><p className="auth-switch">New to Vasooli? <Link href="/register">Start a 7-day Starter trial</Link></p></div></main>;
}
