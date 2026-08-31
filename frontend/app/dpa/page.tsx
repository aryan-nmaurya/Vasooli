export const metadata = { title: "Data Processing Addendum — Vasooli" };

export default function DpaPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-14 sm:px-6">
      <p className="text-xs font-semibold uppercase tracking-wider text-accent">Legal</p>
      <h1 className="mt-2 text-4xl font-semibold">Data Processing Addendum</h1>
      <p className="mt-3 text-sm text-ink-4">Effective 31 August 2026</p>
      <div className="mt-8 space-y-6 text-sm leading-7 text-ink-3">
        <section><h2 className="text-lg font-semibold text-ink">1. Scope and roles</h2><p>This addendum applies when Vasooli processes personal data for a customer under the Terms of Service. The customer is the controller and Vasooli is the processor unless applicable law assigns different roles.</p></section>
        <section><h2 className="text-lg font-semibold text-ink">2. Instructions and confidentiality</h2><p>Vasooli processes personal data only to provide, secure, support, and improve the contracted service and on the customer&apos;s documented instructions. Personnel with access are bound by confidentiality duties.</p></section>
        <section><h2 className="text-lg font-semibold text-ink">3. Security</h2><p>Vasooli maintains tenant-scoped access, role permissions, encryption for stored integration credentials, transport security, signed provider events, audit records, bounded sessions, backups, and incident procedures appropriate to the service.</p></section>
        <section><h2 className="text-lg font-semibold text-ink">4. Subprocessors and transfers</h2><p>Infrastructure, email, AI, payment, and ERP providers may process limited data needed for their service. Vasooli remains responsible for subprocessors as required by law and will use lawful transfer mechanisms where data crosses borders.</p></section>
        <section><h2 className="text-lg font-semibold text-ink">5. Assistance and incidents</h2><p>Taking account of the nature of processing, Vasooli will reasonably assist with data-subject requests, security assessments, breach obligations, and regulator inquiries. Vasooli will notify the customer without undue delay after confirming a personal-data breach affecting customer data.</p></section>
        <section><h2 className="text-lg font-semibold text-ink">6. Return and deletion</h2><p>On termination or written request, Vasooli will return or delete customer data subject to legal retention, security evidence, backup cycles, and immutable financial or audit records. Remaining protected copies are isolated from ordinary processing until deletion.</p></section>
        <section><h2 className="text-lg font-semibold text-ink">7. Audit and contact</h2><p>Vasooli will provide reasonable compliance information and cooperate with proportionate audits subject to confidentiality and security controls. DPA notices may be sent to privacy@vasooli.space.</p></section>
        <p className="rounded-xl border border-line bg-panel p-4 text-xs text-ink-4">This repository copy is the product&apos;s operational baseline and must be reviewed by qualified counsel for the launch entity, governing law, transfer mechanism, subprocessor list, and customer-specific requirements before production use.</p>
      </div>
    </main>
  );
}
