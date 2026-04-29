import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "IBKR Trading Engine",
  description: "ICT 1m engine - paper validation dashboard",
};

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/journal", label: "Journal" },
  { href: "/analytics", label: "Analytics" },
  { href: "/settings", label: "Settings" },
];

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen flex flex-col">
          <header className="border-b border-slate-800 bg-panel">
            <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
              <div className="flex items-center gap-6">
                <span className="font-semibold tracking-tight">
                  IBKR Trading Engine
                </span>
                <nav className="flex items-center gap-4 text-sm">
                  {NAV.map((item) => (
                    <Link
                      key={item.href}
                      href={item.href}
                      className="text-slate-300 hover:text-white"
                    >
                      {item.label}
                    </Link>
                  ))}
                </nav>
              </div>
              <span className="text-xs text-muted">paper · ICT 1m</span>
            </div>
          </header>
          <main className="flex-1 max-w-7xl mx-auto px-6 py-6 w-full">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
