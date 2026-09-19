"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { ApiError, errorMessage } from "../../lib/api";
import { useSession } from "../../lib/session";

function safeNext(value: string | null): string {
  // Only same-site relative paths — never redirect to another origin.
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/workspace";
}

export default function AuthForm({ mode }: { mode: "signin" | "signup" }) {
  const session = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const wantsDemo = params.get("demo") === "1";
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setFieldErrors({});
    try {
      if (mode === "signup") {
        await session.signUp({ email, password, display_name: name });
      } else {
        await session.signIn(email, password);
      }
      if (wantsDemo) await session.openDemo();
      router.replace(next);
    } catch (err) {
      if (err instanceof ApiError && err.details.length) {
        setFieldErrors(Object.fromEntries(err.details.map((d) => [d.field, d.message])));
      }
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const signup = mode === "signup";
  const query = wantsDemo ? "?demo=1" : "";

  return (
    <main className="shell authShell" id="main">
      <header className="topbar">
        <Link className="brand" href="/">cermat.</Link>
      </header>
      <form className="panel authPanel" onSubmit={submit} noValidate>
        <div className="panelLabel">{signup ? "Create account" : "Sign in"}</div>
        <h1>{signup ? "Start a cermat. workspace." : "Welcome back."}</h1>
        {wantsDemo && (
          <p className="muted">
            {signup ? "Create an account" : "Sign in"} to open a demo workspace with labelled sample data. Your own
            workspace stays empty until you add real documents.
          </p>
        )}

        {signup && (
          <label className="field">
            <span>Your name</span>
            <input className="textInput" autoComplete="name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Email</span>
          <input
            className="textInput"
            type="email"
            autoComplete="email"
            required
            value={email}
            aria-invalid={Boolean(fieldErrors.email)}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Password{signup ? " · at least 10 characters" : ""}</span>
          <input
            className="textInput"
            type="password"
            autoComplete={signup ? "new-password" : "current-password"}
            required
            minLength={signup ? 10 : undefined}
            value={password}
            aria-invalid={Boolean(fieldErrors.password)}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          {busy ? (signup ? "Creating account…" : "Signing in…") : signup ? "Create account" : "Sign in"}
        </button>
        <p className="authSwitch">
          {signup ? (
            <>Already have an account? <Link href={`/signin${query}`}>Sign in</Link></>
          ) : (
            <>New to cermat.? <Link href={`/signup${query}`}>Create an account</Link></>
          )}
        </p>
      </form>
    </main>
  );
}
