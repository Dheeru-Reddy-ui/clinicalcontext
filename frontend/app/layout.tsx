import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { cookies, headers } from "next/headers";
import "./globals.css";

import { AppProviders } from "@/components/providers/app-providers";
import { parseTextSize, TEXT_SIZE_COOKIE } from "@/lib/text-size";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "ClinicalContext",
    template: "%s · ClinicalContext",
  },
  description:
    "Evidence-grounded clinical question answering over public medical literature.",
  applicationName: "ClinicalContext",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "default", title: "ClinicalContext" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbfc" },
    { media: "(prefers-color-scheme: dark)", color: "#131417" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // The per-request CSP nonce the middleware minted (lib/csp.ts). Reading a
  // request header here is also what makes every page render dynamically,
  // which a nonce requires: a page prerendered at build time carries no
  // nonce, so its scripts would not match the header and never run — the
  // blank /login on the first deployment.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  // Settings → Appearance: this device's text size, applied before the first paint.
  const textSize = parseTextSize((await cookies()).get(TEXT_SIZE_COOKIE)?.value);
  return (
    // suppressHydrationWarning: next-themes sets the class on <html> before hydration.
    <html lang="en" data-text-size={textSize} suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        {/* Auth lives on the authenticated segments only (app/app, app/onboarding),
            so public pages never boot the Supabase client. */}
        <AppProviders nonce={nonce}>{children}</AppProviders>
      </body>
    </html>
  );
}
