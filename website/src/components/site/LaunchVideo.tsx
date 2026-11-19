"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { motion, useReducedMotion, useScroll, useTransform } from "motion/react";
import { Play, Volume2, VolumeX } from "lucide-react";

// Phones get the vertical cut of the reel; everything wider gets 16:9.
const PHONE = "(max-width: 639px)";
const CUTS = {
  wide: { src: "/video/utter-launch-16x9.mp4", poster: "/video/utter-launch-16x9.jpg" },
  tall: { src: "/video/utter-launch-9x16.mp4", poster: "/video/utter-launch-9x16.jpg" },
};

/** true / false on the client, null while rendering on the server (so no video loads before we know the screen). */
function useMediaQuery(query: string): boolean | null {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => null,
  );
}

export function LaunchVideo() {
  const frame = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const phone = useMediaQuery(PHONE);
  const reduceMotion = useReducedMotion();
  const [muted, setMuted] = useState(true);
  const [paused, setPaused] = useState(true);

  const cut = phone === null ? null : phone ? CUTS.tall : CUTS.wide;

  // The frame tilts back and grows to full size as it scrolls up into view.
  const { scrollYProgress } = useScroll({ target: frame, offset: ["start end", "center center"] });
  const rotateX = useTransform(scrollYProgress, [0, 1], [18, 0]);
  const scale = useTransform(scrollYProgress, [0, 1], [0.9, 1]);

  // Play while at least 40 % is on screen, pause when it scrolls away.
  // With reduced motion nothing starts on its own: the visitor presses play.
  useEffect(() => {
    const el = frame.current;
    const v = video.current;
    if (!el || !v || !cut) return;
    const watcher = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !reduceMotion) v.play().catch(() => {});
        else if (!entry.isIntersecting) v.pause();
      },
      { threshold: 0.4 },
    );
    watcher.observe(el);
    return () => watcher.disconnect();
  }, [cut, reduceMotion]);

  function toggleSound() {
    const v = video.current;
    if (!v) return;
    v.muted = !v.muted;
    setMuted(v.muted);
    if (!v.muted && v.paused) v.play().catch(() => {});
  }

  function togglePlay() {
    const v = video.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }

  return (
    <div className="[perspective:1400px]">
      <motion.div
        ref={frame}
        style={reduceMotion ? undefined : { rotateX, scale, transformOrigin: "50% 100%" }}
        className="relative mx-auto w-full max-w-[400px] rounded-[22px] border border-white/10 bg-white/[0.03] p-1.5 shadow-[0_40px_120px_-40px_rgb(124_92_255/0.55)] sm:max-w-none"
      >
        <div className="relative overflow-hidden rounded-[16px] bg-black">
          <video
            ref={video}
            key={cut?.src ?? "none"}
            src={cut?.src}
            poster={cut?.poster}
            muted
            loop
            playsInline
            preload="metadata"
            aria-label="Utter's 20-second launch video: talk to your Mac and it types for you"
            // A new cut (the screen crossed the phone width) starts muted: carry the visitor's choice over.
            onLoadedMetadata={(e) => {
              e.currentTarget.muted = muted;
            }}
            onPlay={() => setPaused(false)}
            onPause={() => setPaused(true)}
            onClick={togglePlay}
            className="block aspect-[9/16] w-full cursor-pointer object-cover sm:aspect-video"
          />

          {paused && cut && (
            <button
              type="button"
              onClick={togglePlay}
              aria-label="Play the launch video"
              className="absolute inset-0 grid place-items-center bg-black/25 transition hover:bg-black/15"
            >
              <span className="grid size-20 place-items-center rounded-full bg-violet text-white shadow-[0_10px_40px_-8px_rgb(124_92_255/0.8)]">
                <Play className="ml-1 size-8 fill-current" />
              </span>
            </button>
          )}

          <button
            type="button"
            onClick={toggleSound}
            aria-pressed={!muted}
            className="absolute bottom-3 right-3 inline-flex items-center gap-2 rounded-full border border-white/15 bg-black/55 px-3.5 py-2 text-sm font-medium text-fg backdrop-blur transition hover:border-white/35"
          >
            {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
            {muted ? "Sound on" : "Sound off"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
