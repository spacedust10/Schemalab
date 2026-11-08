import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Schemalab",
  description: "Database learning lab and AI data analyst on free, local LLMs",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
