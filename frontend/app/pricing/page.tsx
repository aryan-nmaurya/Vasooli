import Link from "next/link";

export const metadata = {
  title: "Pricing — Vasooli",
  description: "Simple plans for policy-controlled B2B receivables recovery, from 100 to 2,000 active invoices.",
};

const PLANS = [
  { name: "Starter", price: "₹1,999", description: "For lean finance teams replacing manual follow-up.", invoices: "100 active invoices", seats: "1 user", cta: "Start free for 7 days", features: ["Policy-controlled recovery", "Zoho Books integration", "CSV ledger import", "Payment links and reconciliation", "Promises and disputes", "Complete audit log", "Email support"] },
  { name: "Growth", price: "₹5,999", description: "For growing teams that need connected, repeatable collections.", invoices: "500 active invoices", seats: "Up to 5 users", cta: "Choose Growth", featured: true, features: ["Everything in Starter", "Custom recovery policies", "Role-based access", "Operational exceptions queue", "Priority support"] },
  { name: "Scale", price: "₹14,999", description: "For high-volume operations with deeper controls and oversight.", invoices: "2,000 active invoices", seats: "Up to 15 users", cta: "Choose Scale", features: ["Everything in Growth", "Advanced team controls", "Audit and ledger exports", "Billing reconciliation", "Onboarding support"] },
];

const FAQ = [
  ["What is an active invoice?", "An invoice currently tracked in an open recovery workflow. Paid, cancelled, and archived invoices do not count toward your active limit."],
  ["Can I try Vasooli before paying?", "Yes. Starter includes a 7-day trial with no card required at signup. Growth and Scale begin as paid subscriptions and do not include a trial."],
  ["Will Vasooli send messages without my control?", "No. Communication is governed by your approved policy, quiet windows, attempt caps, suppressions, disputes, and workspace permissions."],
  ["Can I change plans later?", "Yes. Upgrade as your active receivables grow. Downgrades preserve your records and take effect only when your usage fits the new plan."],
  ["Are taxes included?", "Listed prices exclude applicable taxes. Your final amount and renewal date are shown before Razorpay checkout."],
  ["What happens if I cancel?", "Automation stops at the end of the paid period. Your workspace follows the export and retention terms shown during cancellation."],
];

export default function PricingPage() {
  return (
    <main className="pricing-page">
      <section className="pricing-hero">
        <p className="public-eyebrow">Simple, transparent pricing</p>
        <h1>Recover more than<br /><span>your software costs.</span></h1>
        <p>Start with the receivables you manage today. Every plan includes the controlled recovery loop, trusted payment state, and an audit trail your team can rely on.</p>
        <div className="pricing-trust"><span>7-day Starter trial</span><span>₹2 to verify the mandate, refunded</span><span>Cancel anytime</span></div>
      </section>
      <section className="pricing-grid" aria-label="Vasooli plans">
        {PLANS.map((plan) => <article key={plan.name} className={`pricing-card${plan.featured ? " pricing-card-featured" : ""}`}>
          {plan.featured ? <div className="pricing-popular">Most popular</div> : null}
          <div><h2>{plan.name}</h2><p>{plan.description}</p></div>
          <div className="pricing-price"><strong>{plan.price}</strong><span>/ month<br />+ applicable taxes</span></div>
          <div className="pricing-limits"><span>{plan.invoices}</span><span>{plan.seats}</span></div>
          <ul>{plan.features.map((feature) => <li key={feature}><span aria-hidden>✓</span>{feature}</li>)}</ul>
          <Link href="/register">{plan.cta} <span aria-hidden>↗</span></Link>
        </article>)}
      </section>
      <section className="pricing-included">
        <div><p className="public-eyebrow">Included in every plan</p><h2>Financial control is not an add-on.</h2></div>
        <div className="pricing-included-grid">
          <article><span>01</span><h3>Policy before action</h3><p>Cooldowns, attempt limits, quiet hours, promises, disputes, and suppressions decide whether contact is allowed.</p></article>
          <article><span>02</span><h3>Trusted payment state</h3><p>Signed provider events and authorized records—not a customer reply or AI output—change financial status.</p></article>
          <article><span>03</span><h3>Audit from day one</h3><p>Synchronizations, decisions, messages, replies, operator actions, and reconciliation remain attributable.</p></article>
        </div>
      </section>
      <section className="pricing-faq">
        <p className="public-eyebrow">Questions, answered</p><h2>Know exactly what you are buying.</h2>
        <div>{FAQ.map(([question, answer]) => <details key={question}><summary>{question}<span aria-hidden>+</span></summary><p>{answer}</p></details>)}</div>
      </section>
      <section className="pricing-cta"><p className="public-eyebrow">Start with control</p><h2>Your overdue ledger already has a cost.<br />Give it an accountable next action.</h2><div><Link href="/register">Start Starter free for 7 days <span aria-hidden>↗</span></Link><a href="mailto:hello@vasooli.space">Talk to us</a></div></section>
    </main>
  );
}
