import Link from "next/link";

export default function Landing() {
  return (
    <main className="shell landing" id="main">
      <header className="topbar">
        <span className="brand">cermat.</span>
        <nav className="landingNav" aria-label="Account">
          <Link href="/signin">Sign in</Link>
        </nav>
      </header>

      <section className="hero landingHero">
        <p className="eyebrow">AI operations intelligence for purchasing documents</p>
        <h1>
          Upload.
          <br />
          Reconcile.
          <br />
          Review.
          <br />
          Understand.
        </h1>
        <p className="lede">
          cermat. reads purchase orders, delivery orders and invoices, verifies them against each other with
          deterministic rules, and shows exactly which evidence supports every exception.
        </p>
        <div className="landingActions">
          <Link className="primaryLink" href="/workspace">
            Open workspace
          </Link>
          <Link className="secondaryButton" href="/signup?demo=1">
            View demo
          </Link>
        </div>
      </section>

      <section className="principles" aria-label="How cermat. works">
        <div>
          <span>AI reads</span>
          <p>Multimodal extraction turns each document into structured fields, with the source snippet, page and confidence for every value.</p>
        </div>
        <div>
          <span>Rules verify</span>
          <p>Deterministic code compares ordered, delivered and invoiced quantities and prices. The model never decides a variance.</p>
        </div>
        <div>
          <span>Evidence proves</span>
          <p>Every exception links back to the exact document text it came from.</p>
        </div>
        <div>
          <span>Humans decide</span>
          <p>People resolve or reopen each exception with a note, and every action is written to an audit trail.</p>
        </div>
      </section>
    </main>
  );
}
