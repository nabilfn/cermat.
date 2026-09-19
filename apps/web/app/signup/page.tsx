import { Suspense } from "react";
import AuthForm from "../components/shell/AuthForm";
import { SessionProvider } from "../lib/session";

export default function Page() {
  return (
    <SessionProvider>
      <Suspense>
        <AuthForm mode="signup" />
      </Suspense>
    </SessionProvider>
  );
}
