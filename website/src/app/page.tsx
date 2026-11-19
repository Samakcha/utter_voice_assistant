import Image from "next/image";
import { AudioLines, BookA, Keyboard, NotebookPen, WandSparkles, Zap } from "lucide-react";
import Aurora from "@/components/reactbits/Aurora";
import BlurText from "@/components/reactbits/BlurText";
import CountUp from "@/components/reactbits/CountUp";
import GradientText from "@/components/reactbits/GradientText";
import Magnet from "@/components/reactbits/Magnet";
import ShinyText from "@/components/reactbits/ShinyText";
import SpotlightCard from "@/components/reactbits/SpotlightCard";
import StarBorder from "@/components/reactbits/StarBorder";
import { CopyCommand } from "@/components/site/CopyCommand";
import { GITHUB, LICENSE, PRIVACY, README } from "@/components/site/constants";
import { HandCircle, HandUnderline } from "@/components/site/HandCircle";
import { Keycap } from "@/components/site/Keycap";
import { LaunchVideo } from "@/components/site/LaunchVideo";
import { ListeningDemo } from "@/components/site/ListeningDemo";
import { Nav } from "@/components/site/Nav";

const VIOLET = "rgba(124, 92, 255, 0.28)";
const CARD_STYLE = { "--spotlight-card-surface": "#121119" } as React.CSSProperties;

const FEATURES = [
  {
    icon: Keyboard,
    title: "Type with your voice, anywhere",
    text: "Mail, Slack, Notes, a browser tab, your code editor. If there's a text box, Utter can type into it.",
  },
  {
    icon: Zap,
    title: "Commands that just happen",
    text: "“Open Safari”, “take a note”, “search WhatsApp for Alex”. Short commands run as soon as you stop talking.",
  },
  {
    icon: WandSparkles,
    title: "Tidy text, automatically",
    text: "Drops the “um”s, accidental repeats and quick corrections. “Monday, no wait, Tuesday” becomes “Tuesday”.",
  },
  {
    icon: NotebookPen,
    title: "Voice notes into Apple Notes",
    text: "Say “take a note”, talk, and a new note is saved with the date and time. Stop with “stop note”.",
  },
  {
    icon: BookA,
    title: "Your words, spelled your way",
    text: "Teach Utter names and terms once. It hints them to the speech model and fixes close mishearings.",
  },
  {
    icon: AudioLines,
    title: "Hold to talk, or tap",
    text: "Hold the shortcut while you speak and let go to paste, or tap once and Utter stops when you do.",
  },
];

const STEPS = [
  { keys: ["⌃", "⌥", "D"], title: "Press the shortcut", text: "Control + Option + D, in any app. Or hold it while you speak." },
  { keys: ["🎙️"], title: "Just talk", text: "A small wave shows Utter is listening. Pause, and it knows you're done." },
  { keys: ["↵"], title: "It's typed for you", text: "Your words appear where your cursor is, cleaned up and ready to send." },
];

const FAQ = [
  [
    "Is Utter really free?",
    "Yes. Utter is free and open source under the MIT licence. There's no paid plan and no trial.",
  ],
  [
    "Does my voice leave my Mac?",
    "No. Utter turns speech into text with OpenAI's Whisper model running on your Mac's own GPU. No audio and no text is sent anywhere, and recordings aren't kept unless you switch that on.",
  ],
  [
    "Why do I install it from Terminal?",
    "Apps downloaded from the internet have to be signed through Apple's paid developer programme, or macOS blocks them. An app built on your own Mac doesn't, so one command builds Utter for you. It takes about 10 to 15 minutes, mostly downloads.",
  ],
  [
    "Which Macs does it work on?",
    "Macs with Apple silicon (M1 or newer) on macOS 14 Sonoma or later, with about 3 GB free. 16 GB of memory is ideal; on 8 GB Macs the installer suggests a smaller, faster model.",
  ],
  [
    "Why does it ask me to sign up?",
    "So we can count how many people use Utter. Only your email address and sign-in times are stored, never what you say or type.",
  ],
  [
    "Which languages does it understand?",
    "English for now.",
  ],
  [
    "How do I update or remove it?",
    "Run ./install.sh again in the same folder to update; your settings and words are kept. The README explains how to uninstall.",
  ],
];

function SectionTitle({ kicker, title, children }: { kicker: string; title: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="mx-auto mb-14 max-w-2xl text-center">
      <div className="mb-2 font-hand text-[32px] font-bold leading-none text-lavender">{kicker}</div>
      <h2 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">{title}</h2>
      {children && <p className="mt-5 text-pretty text-lg text-dim">{children}</p>}
    </div>
  );
}

function WindowShot({ src, alt, width, height, priority = false }: { src: string; alt: string; width: number; height: number; priority?: boolean }) {
  return (
    <div className="relative rounded-[22px] border border-white/10 bg-white/[0.03] p-1.5 shadow-[0_40px_120px_-40px_rgb(124_92_255/0.55)]">
      <Image src={src} alt={alt} width={width} height={height} priority={priority} className="h-auto w-full rounded-[16px]" sizes="(max-width: 768px) 100vw, 900px" />
    </div>
  );
}

export default function Home() {
  return (
    <div id="top" className="relative overflow-x-clip">
      <Nav />

      {/* ---- Hero ---- */}
      <section className="relative isolate px-5 pb-20 pt-36 sm:pt-44">
        <div className="absolute inset-x-0 top-0 -z-10 h-[760px] [mask-image:linear-gradient(to_bottom,black_50%,transparent)]">
          <Aurora colorStops={["#4c22d6", "#9276ff", "#3a1aa8"]} amplitude={1.1} blend={0.6} speed={0.6} brightness={0.95} glow={0.75} height={0.7} />
        </div>
        <div className="mx-auto max-w-5xl text-center">
          <a
            href={GITHUB}
            className="mb-8 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-4 py-1.5 text-sm backdrop-blur transition hover:border-violet/50"
          >
            <span className="size-1.5 rounded-full bg-[#4ade80] shadow-[0_0_10px_#4ade80]" />
            <ShinyText text="Free · Open source · Runs on your Mac" speed={3} color="#aaa8b8" shineColor="#ffffff" />
            <span className="text-faint">→</span>
          </a>
          <h1 className="sr-only">Utter: talk to your Mac. It types for you.</h1>
          {/* BlurText draws a <p>, which can't sit inside a heading: the heading above is for screen readers */}
          <div aria-hidden="true" className="text-balance text-5xl font-semibold leading-[1.05] tracking-tight sm:text-7xl">
            <BlurText text="Talk to your Mac." animateBy="words" direction="top" delay={90} className="justify-center" />
            <span className="mt-2 block">
              <GradientText colors={["#a892ff", "#ffffff", "#7c5cff", "#a892ff"]} animationSpeed={7} className="!inline-block">
                It types for you.
              </GradientText>
            </span>
          </div>
          <p className="mx-auto mt-7 max-w-2xl text-pretty text-lg text-dim sm:text-xl">
            Press a shortcut, speak, and{" "}
            <span className="font-hand text-2xl font-bold text-fg sm:text-3xl">
              <HandCircle marks={false}>Utter</HandCircle>
            </span>{" "}
            types it into any app or does what you asked. Speech becomes text on your Mac, so your voice never leaves it.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Magnet padding={60} magnetStrength={6}>
              <StarBorder as="a" href="#install" color="#a892ff" backgroundColor="#7c5cff" textColor="#ffffff" radius={14} className="px-7 text-base font-semibold">
                Install Utter for free →
              </StarBorder>
            </Magnet>
            <a href={GITHUB} className="rounded-[14px] border border-white/12 bg-white/[0.04] px-7 py-3.5 text-base font-medium text-fg backdrop-blur transition hover:border-white/30">
              View on GitHub
            </a>
          </div>
          <p className="mt-5 text-sm text-faint">macOS 14+ · Apple silicon (M1 or newer) · English</p>
        </div>

        <div id="watch" className="relative mx-auto mt-16 max-w-5xl scroll-mt-24">
          <p className="mb-5 text-center font-hand text-2xl text-lavender sm:text-3xl">Utter in 20 seconds ↓</p>
          <LaunchVideo />
        </div>
      </section>

      {/* ---- Works everywhere ---- */}
      <section className="border-y border-line bg-white/[0.015] py-6" aria-label="Works in your apps">
        <div className="relative overflow-hidden [mask-image:linear-gradient(to_right,transparent,black_12%,black_88%,transparent)]">
          <div className="flex w-max animate-marquee gap-10 whitespace-nowrap text-lg text-faint">
            {[...Array(2)].flatMap((_, copy) =>
              ["Mail", "Notes", "Safari", "Slack", "Messages", "WhatsApp", "Pages", "VS Code", "Chrome", "Notion", "Xcode", "Discord"].map((app) => (
                <span key={`${copy}-${app}`} className="flex items-center gap-10">
                  {app}
                  <span className="text-violet/60">✦</span>
                </span>
              )),
            )}
          </div>
        </div>
      </section>

      {/* ---- Features ---- */}
      <section id="features" className="px-5 py-28">
        <SectionTitle kicker="what it does" title={<>Your voice, in every app</>}>
          One shortcut for typing, one for commands. No window to open, no cloud to trust.
        </SectionTitle>
        <div className="mx-auto grid max-w-6xl gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <SpotlightCard key={f.title} spotlightColor={VIOLET} className="border-0 !p-7" style={CARD_STYLE}>
              <div className="mb-5 flex size-11 items-center justify-center rounded-xl border border-violet/30 bg-violet/10 text-lavender">
                <f.icon className="size-5" strokeWidth={1.8} />
              </div>
              <h3 className="mb-2 text-lg font-semibold">{f.title}</h3>
              <p className="text-[15px] leading-relaxed text-dim">{f.text}</p>
            </SpotlightCard>
          ))}
        </div>
      </section>

      {/* ---- How it works ---- */}
      <section id="how" className="px-5 py-28">
        <SectionTitle kicker="how it works" title="Three steps. No typing." />
        <div className="mx-auto grid max-w-6xl items-center gap-14 lg:grid-cols-2">
          <ol className="space-y-5">
            {STEPS.map((s, i) => (
              <li key={s.title} className="flex gap-5 rounded-2xl border border-line bg-white/[0.02] p-6">
                <div className="font-hand text-4xl font-bold text-lavender">{i + 1}</div>
                <div>
                  <div className="mb-3 flex gap-1.5">
                    {s.keys.map((k) => (
                      <Keycap key={k} size="sm">
                        {k}
                      </Keycap>
                    ))}
                  </div>
                  <h3 className="text-lg font-semibold">{s.title}</h3>
                  <p className="mt-1 text-dim">{s.text}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="relative rounded-3xl border border-line bg-gradient-to-b from-violet/[0.12] to-transparent p-8 sm:p-12">
            <ListeningDemo />
          </div>
        </div>

        <div className="mx-auto mt-16 flex max-w-3xl flex-wrap items-center justify-center gap-x-8 gap-y-4 text-dim">
          <span className="flex items-center gap-2">
            <Keycap size="sm">⌃</Keycap>
            <Keycap size="sm">⌥</Keycap>
            <Keycap size="sm">D</Keycap> Dictation
          </span>
          <span className="flex items-center gap-2">
            <Keycap size="sm">⌃</Keycap>
            <Keycap size="sm">⌥</Keycap>
            <Keycap size="sm">M</Keycap> Commands
          </span>
          <span className="flex items-center gap-2">
            <Keycap size="sm">esc</Keycap> Stop
          </span>
          <span className="text-faint">Change them any time in Settings.</span>
        </div>
      </section>

      {/* ---- Numbers ---- */}
      <section className="px-5 py-16">
        <div className="mx-auto grid max-w-6xl grid-cols-2 gap-px overflow-hidden rounded-3xl border border-line bg-line lg:grid-cols-4">
          {[
            { value: <CountUp from={100} to={0} direction="up" duration={1.6} />, suffix: " bytes", text: "of your voice sent anywhere" },
            { value: <CountUp to={0.5} decimals={1} duration={1.4} />, suffix: " s", text: "to turn a sentence into text" },
            { value: <CountUp to={99} duration={1.6} />, suffix: "%", text: "of words right in our accuracy tests" },
            { value: <CountUp to={1} duration={1} />, suffix: "", text: "command to install it on your Mac" },
          ].map((stat) => (
            <div key={stat.text} className="bg-ink p-8 text-center sm:p-10">
              <div className="whitespace-nowrap text-4xl font-semibold tracking-tight sm:text-5xl">
                {stat.value}
                <span className="text-3xl text-lavender sm:text-4xl">{stat.suffix}</span>
              </div>
              <p className="mt-2 text-sm text-dim">{stat.text}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---- Screens ---- */}
      <section className="px-5 py-28">
        <SectionTitle kicker="made for macOS" title="Set up in a minute. Tweak everything.">
          A friendly welcome walks you through permissions and a first try. Settings keeps every option in one place, and changes apply instantly.
        </SectionTitle>
        <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-2">
          <div>
            <WindowShot src="/screens/permissions.png" alt="Utter's welcome window asking for three permissions, each ticked as it is switched on" width={1520} height={1000} />
            <p className="mt-4 text-center text-dim">Permissions tick themselves off as you switch them on.</p>
          </div>
          <div>
            <WindowShot src="/screens/settings-shortcuts.png" alt="Utter's Settings window showing the shortcuts page with key caps" width={1520} height={1080} />
            <p className="mt-4 text-center text-dim">Record your own shortcuts, tidy-up rules and words.</p>
          </div>
        </div>
      </section>

      {/* ---- Privacy ---- */}
      <section id="privacy" className="px-5 py-28">
        <div className="mx-auto grid max-w-6xl items-center gap-12 rounded-[32px] border border-line bg-gradient-to-br from-violet/[0.14] via-transparent to-transparent p-8 sm:p-14 lg:grid-cols-[1.1fr_1fr]">
          <div>
            <div className="mb-2 font-hand text-[32px] font-bold leading-none text-lavender">private by design</div>
            <h2 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
              What you say{" "}
              <span className="font-hand text-5xl sm:text-6xl">
                <HandCircle marks={false}>stays</HandCircle>
              </span>{" "}
              on your Mac.
            </h2>
            <p className="mt-6 text-lg text-dim">
              Utter runs the Whisper speech model on your Mac&apos;s own chip. There&apos;s no server listening, nothing to upload, and no analytics.
            </p>
            <a href={PRIVACY} className="mt-8 inline-flex text-lavender underline-offset-4 hover:underline">
              Read the privacy policy →
            </a>
          </div>
          <ul className="space-y-3">
            {[
              ["On-device speech to text", "Whisper large-v3-turbo on the Apple GPU. Works offline."],
              ["No recordings kept", "Audio lives in memory and is gone after each sentence, unless you choose otherwise."],
              ["Only your email online", "Your account is just an email address, used to sign in and count users."],
              ["Open source", "Every line is on GitHub, under the MIT licence. Read it, change it, build it."],
            ].map(([title, text]) => (
              <li key={title} className="flex gap-4 rounded-2xl border border-line bg-ink/60 p-5">
                <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-[#4ade80] text-sm font-bold text-ink">✓</span>
                <div>
                  <div className="font-semibold">{title}</div>
                  <div className="text-sm text-dim">{text}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ---- Install ---- */}
      <section id="install" className="relative isolate px-5 py-28">
        <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_center,rgb(124_92_255/0.16),transparent_60%)]" />
        <SectionTitle kicker="get it" title="Install in one command">
          Open Terminal (Applications → Utilities), paste this and press Return. The installer explains each step and asks before every download.
        </SectionTitle>
        <div className="mx-auto max-w-3xl">
          <CopyCommand />
          <div className="mt-8 grid gap-4 text-sm sm:grid-cols-3">
            {[
              ["1", "Checks your Mac", "Apple silicon, macOS 14+, free space"],
              ["2", "Sets everything up", "Python, the packages and the 1.6 GB speech model"],
              ["3", "Builds and opens Utter", "Then the welcome window takes you from there"],
            ].map(([n, title, text]) => (
              <div key={n} className="rounded-2xl border border-line bg-white/[0.02] p-5">
                <div className="font-hand text-3xl font-bold text-lavender">{n}</div>
                <div className="mt-1 font-semibold">{title}</div>
                <div className="text-dim">{text}</div>
              </div>
            ))}
          </div>
          <p className="mt-8 text-center text-sm text-faint">
            To update later, run <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-dim">./install.sh</code> again.{" "}
            <a href={README} className="text-lavender hover:underline">
              Full instructions
            </a>
          </p>
        </div>
      </section>

      {/* ---- FAQ ---- */}
      <section id="faq" className="px-5 py-28">
        <SectionTitle kicker="questions" title="Good to know" />
        <div className="mx-auto max-w-3xl divide-y divide-line rounded-3xl border border-line bg-white/[0.02]">
          {FAQ.map(([q, a]) => (
            <details key={q} className="group px-6 py-5 [&_summary::-webkit-details-marker]:hidden">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 text-lg font-medium">
                {q}
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full border border-white/10 text-dim transition group-open:rotate-45 group-open:border-violet/60 group-open:text-lavender">
                  +
                </span>
              </summary>
              <p className="mt-3 pr-10 leading-relaxed text-dim">{a}</p>
            </details>
          ))}
        </div>
      </section>

      {/* ---- Final call ---- */}
      <section className="px-5 pb-28">
        <div className="relative isolate mx-auto max-w-6xl overflow-hidden rounded-[36px] border border-line px-6 py-20 text-center sm:px-16">
          <div className="absolute inset-0 -z-10">
            <Aurora colorStops={["#4c22d6", "#9276ff", "#3a1aa8"]} amplitude={1} speed={0.5} brightness={1.2} glow={0.9} height={0.75} />
          </div>
          <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_bottom,rgb(124_92_255/0.25),transparent_65%)]" />
          <h2 className="text-balance text-4xl font-semibold tracking-tight sm:text-6xl">
            Stop typing.{" "}
            <span className="font-hand text-5xl sm:text-7xl">
              Start <HandCircle>talking</HandCircle>.
            </span>
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-lg text-dim">Free, private, and yours to keep. Set it up in about ten minutes.</p>
          <div className="mt-10 flex justify-center">
            <StarBorder as="a" href="#install" color="#ffffff" backgroundColor="#7c5cff" textColor="#ffffff" radius={14} className="px-8 text-base font-semibold">
              Install Utter →
            </StarBorder>
          </div>
        </div>
      </section>

      {/* ---- Footer ---- */}
      <footer className="border-t border-line px-5 py-10">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 text-sm text-faint sm:flex-row">
          <div className="flex items-center gap-2.5">
            <Image src="/utter-icon.png" alt="" width={24} height={24} className="size-6 rounded-md" />
            <span className="font-hand text-2xl font-bold text-fg">Utter</span>
            <span>· Talk to your Mac</span>
          </div>
          <div className="flex gap-6">
            <a href={GITHUB} className="hover:text-fg">GitHub</a>
            <a href={PRIVACY} className="hover:text-fg">Privacy</a>
            <a href={LICENSE} className="hover:text-fg">MIT licence</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
