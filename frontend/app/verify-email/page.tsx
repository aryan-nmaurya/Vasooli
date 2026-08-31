import { VerifyEmail } from "@/components/LiveIdentityRecovery";

export default async function VerifyEmailPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  return <VerifyEmail token={(await searchParams).token || ""} />;
}
