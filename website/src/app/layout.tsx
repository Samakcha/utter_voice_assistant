import type { Metadata, Viewport } from "next";
import { Caveat, Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// The handwriting Utter's own windows use
const caveat = Caveat({ variable: "--font-caveat", subsets: ["latin"], weight: ["500", "700"] });

export const metadata: Metadata = {
  // The live address, so link previews (X, WhatsApp, LinkedIn) point at the right image.
  // The preview image itself is app/opengraph-image.jpg, a frame of the launch reel's end card.
  metadataBase: new URL("https://getutter-app.vercel.app"),
  title: "Utter: talk to your Mac",
  description:
    "Free, private voice typing and voice commands for your Mac. Press a shortcut, speak, and Utter types it into any app. Speech is turned into text on your Mac.",
  openGraph: {
    title: "Utter: talk to your Mac",
    description: "Free, private voice typing and voice commands for macOS. Your voice never leaves your Mac.",
    url: "/",
    siteName: "Utter",
    type: "website",
  },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = { themeColor: "#0b0a10", colorScheme: "dark" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${caveat.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-ink text-fg">{children}</body>
    </html>
  );
}
