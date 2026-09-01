import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { Shell } from "@/components/Shell";
import "./globals.css";

// One engineered superfamily: Plex Sans for everything read as language,
// Plex Mono for everything read as a measurement: amounts, ids, UTRs, hashes.
const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Ledgerline: reconciliation that shows its work",
  description:
    "AI-assisted reconciliation for Indian payment gateways: invoice to payment to settlement to bank line, with every match backed by evidence.",
};

// Runs before the browser paints the first frame, so a viewer who chose dark
// never sees the light default flash first. Light needs no attribute (it is
// what bare `:root` already declares), so only "dark" is ever stamped.
const NO_FLASH = `try{if(localStorage.getItem("ledgerline-theme")==="dark")document.documentElement.dataset.theme="dark"}catch(e){}`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      // The script above mutates <html> before React hydrates.
      suppressHydrationWarning
      className={`${plexSans.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
