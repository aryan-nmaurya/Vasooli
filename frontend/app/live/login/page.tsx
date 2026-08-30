import Link from "next/link";

import { LiveLoginForm } from "@/components/LiveLoginForm";

export default function LiveLoginPage() {
  return <main className="mx-auto max-w-6xl px-4 py-14 sm:px-6"><LiveLoginForm /><p className="mx-auto mt-4 max-w-md text-center text-xs text-ink-4">Need an account? <Link href="/register" className="text-accent hover:underline">Create a live workspace</Link></p></main>;
}
