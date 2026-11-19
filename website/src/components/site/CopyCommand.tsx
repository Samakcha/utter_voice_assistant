"use client";

import { useState } from "react";
import { INSTALL_COMMAND } from "./constants";

/** The install command in a terminal-style box, with a copy button. */
export function CopyCommand() {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_COMMAND);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-[#0e0d14] text-left shadow-[0_30px_80px_-30px_rgb(124_92_255/0.45)]">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2.5">
        <div className="flex gap-1.5" aria-hidden="true">
          <span className="size-3 rounded-full bg-[#ff5f57]" />
          <span className="size-3 rounded-full bg-[#febc2e]" />
          <span className="size-3 rounded-full bg-[#28c840]" />
        </div>
        <span className="font-mono text-xs text-faint">Terminal</span>
        <button
          type="button"
          onClick={copy}
          className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1 text-xs font-medium text-fg transition hover:border-violet/60 hover:bg-violet/15"
          aria-live="polite"
        >
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
      <pre className="whitespace-pre-wrap break-all px-5 py-5 font-mono text-[13px] leading-relaxed text-fg sm:text-sm">
        <code>
          <span className="select-none text-lavender">$ </span>
          {INSTALL_COMMAND}
        </code>
      </pre>
    </div>
  );
}
