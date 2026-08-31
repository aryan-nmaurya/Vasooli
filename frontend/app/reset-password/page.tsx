import { ResetPassword } from "@/components/LiveIdentityRecovery";

export default async function ResetPasswordPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  return <ResetPassword token={(await searchParams).token || ""} />;
}
