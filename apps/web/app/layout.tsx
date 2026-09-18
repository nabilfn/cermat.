import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "cermat.",
  description: "AI operations intelligence for business documents.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
