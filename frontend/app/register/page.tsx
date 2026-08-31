import Link from "next/link";

import { LiveRegistrationForm } from "@/components/LiveRegistrationForm";

export default function RegisterPage() {
  return <main className="mx-auto max-w-6xl px-4 py-12 sm:px-6"><LiveRegistrationForm /><p className="mx-auto mt-4 max-w-xl text-center text-xs text-ink-4">Already registered? <Link href="/live/login" className="text-accent hover:underline">Open live sign in</Link></p></main>;
}
