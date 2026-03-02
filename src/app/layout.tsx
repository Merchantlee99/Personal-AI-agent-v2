import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NanoClaw v2",
  description: "Secure 3-agent dashboard for Minerva, Clio and Hermes",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
