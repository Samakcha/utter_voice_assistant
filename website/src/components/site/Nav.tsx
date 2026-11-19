import Image from "next/image";
import { GITHUB } from "./constants";

const LINKS = [
  ["Features", "#features"],
  ["How it works", "#how"],
  ["Privacy", "#privacy"],
  ["FAQ", "#faq"],
];

export function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-50">
      <nav className="mx-auto mt-3 flex max-w-6xl items-center justify-between rounded-2xl border border-white/[0.07] bg-ink/60 px-4 py-2.5 backdrop-blur-xl sm:px-5">
        <a href="#top" className="flex items-center gap-2.5" aria-label="Utter home">
          <Image src="/utter-icon.png" alt="" width={32} height={32} className="size-8 rounded-lg" />
          <span className="font-hand text-3xl font-bold leading-none tracking-tight">Utter</span>
        </a>
        <div className="hidden items-center gap-7 text-sm text-dim md:flex">
          {LINKS.map(([title, href]) => (
            <a key={href} href={href} className="transition hover:text-fg">
              {title}
            </a>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <a
            href={GITHUB}
            className="hidden rounded-xl border border-white/10 px-3.5 py-2 text-sm text-fg transition hover:border-white/25 sm:inline-flex"
          >
            GitHub
          </a>
          <a
            href="#install"
            className="rounded-xl bg-gradient-to-b from-violet-light to-violet px-4 py-2 text-sm font-semibold text-white shadow-[0_8px_30px_-8px_rgb(124_92_255/0.8)] transition hover:brightness-110"
          >
            Install free
          </a>
        </div>
      </nav>
    </header>
  );
}
