"use client";

import { useEffect, useState } from "react";
import { Keycap } from "./Keycap";

const SAY = [
  "Hey team, the demo is moving to Thursday at 3 pm.",
  "Remind me to send the invoice to Priya tomorrow.",
  "Let's ship the new onboarding this week.",
];

type Phase = "listening" | "typing" | "done";

/** A small animated picture of dictation: the listening pill, then the words appear. */
export function ListeningDemo() {
  const [line, setLine] = useState(0);
  const [shown, setShown] = useState(0);
  const [phase, setPhase] = useState<Phase>("listening");
  const text = SAY[line];

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    if (phase === "listening") timer = setTimeout(() => setPhase("typing"), 1900);
    else if (phase === "typing") {
      if (shown < text.length) timer = setTimeout(() => setShown((n) => n + 1), 22);
      else timer = setTimeout(() => setPhase("done"), 2200);
    } else {
      timer = setTimeout(() => {
        setLine((l) => (l + 1) % SAY.length);
        setShown(0);
        setPhase("listening");
      }, 300);
    }
    return () => clearTimeout(timer);
  }, [phase, shown, text.length]);

  return (
    <div className="relative mx-auto w-full max-w-md">
      <div className="mb-4 flex items-center justify-center gap-2 text-sm text-dim">
        <Keycap size="sm">⌃</Keycap>
        <Keycap size="sm">⌥</Keycap>
        <Keycap size="sm">D</Keycap>
        <span className="ml-1">then just talk</span>
      </div>
      <div
        className={`mx-auto mb-4 flex h-11 w-44 items-center justify-center gap-1 rounded-full border border-white/10 bg-[#1b1a24]/90 shadow-[0_10px_40px_-10px_rgb(124_92_255/0.7)] backdrop-blur transition-opacity duration-500 ${
          phase === "listening" ? "opacity-100" : "opacity-40"
        }`}
        aria-hidden="true"
      >
        {Array.from({ length: 11 }).map((_, i) => (
          <span
            key={i}
            className="h-6 w-1 origin-center rounded-full bg-gradient-to-t from-violet to-lavender animate-bar"
            style={{ animationDelay: `${(i % 6) * 0.11}s`, animationPlayState: phase === "listening" ? "running" : "paused" }}
          />
        ))}
      </div>
      <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 text-left shadow-inner">
        <div className="mb-2 font-mono text-[11px] uppercase tracking-widest text-faint">Message</div>
        <p className="min-h-[3.5rem] text-[17px] leading-relaxed text-fg" aria-live="polite">
          {text.slice(0, shown)}
          <span className="ml-0.5 inline-block h-5 w-[2px] translate-y-1 bg-lavender animate-blink" aria-hidden="true" />
        </p>
      </div>
    </div>
  );
}
