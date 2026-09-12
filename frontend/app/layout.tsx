import type { Metadata } from "next";
import Script from "next/script";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

export const metadata: Metadata = {
  title: "Law Prep Tutorial Console",
  // Describes what this extract actually is. The line it replaces was the full
  // AgentOS console's ("marketing creatives and brand assets"), which belonged
  // to the Graphics Designer agent — not present here.
  description: "SEO and content intelligence for Law Prep Tutorial",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Settle the theme before first paint, so a reload never flashes the
            one the reader did not pick.

            A saved choice wins; with none, the system setting decides. That
            second half was missing — the attribute was only ever written for an
            explicit "dark" — so somebody whose machine is in dark mode got the
            light console, while the Settings panel told them appearance
            "follows your system setting until you choose here". Legacy saved
            values (ocean/sky/prussian) are not "dark", so they fall through to
            the system exactly as an unset one does. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var t=localStorage.getItem('app-theme');"
              + "var d=t?t==='dark':matchMedia('(prefers-color-scheme: dark)').matches;"
              + "document.documentElement.dataset.theme=d?'dark':'light';}"
              + "catch(e){}",
          }}
        />
      </head>
      <body>
        <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
