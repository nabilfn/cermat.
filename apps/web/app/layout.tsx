import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "cermat.",
  description:
    "AI operations intelligence for purchasing documents: evidence-backed extraction, deterministic reconciliation and human review.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <a className="skipLink" href="#main">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
