import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SchemaPilot | Agentic Data Migration",
  description: "Human-guided autonomous migration for messy legacy data."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
