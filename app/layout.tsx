import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Encar Projects",
  description: "Мониторинг объявлений Encar Korea",
  metadataBase: new URL(process.env.SITE_URL || "http://localhost:3000"),
  openGraph: {
    title: "Encar Projects",
    description: "Мониторинг объявлений Encar Korea",
    images: ["/og.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "Encar Projects",
    description: "Мониторинг объявлений Encar Korea",
    images: ["/og.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
