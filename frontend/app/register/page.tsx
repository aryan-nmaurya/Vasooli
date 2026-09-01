import Link from "next/link";

import { LiveRegistrationForm } from "@/components/LiveRegistrationForm";

export default function RegisterPage() {
  return <main className="auth-page"><div><LiveRegistrationForm /><p className="auth-switch">Already have a workspace? <Link href="/live/login">Sign in</Link></p></div></main>;
}
