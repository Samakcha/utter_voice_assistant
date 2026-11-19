export function Keycap({ children, size = "md" }: { children: React.ReactNode; size?: "sm" | "md" | "lg" }) {
  const sizes = { sm: "h-7 min-w-7 px-2 text-sm rounded-md", md: "h-9 min-w-9 px-3 text-base rounded-lg", lg: "h-12 min-w-12 px-4 text-xl rounded-xl" };
  return (
    <kbd
      className={`inline-flex items-center justify-center border border-white/10 bg-key font-sans font-semibold text-fg shadow-[inset_0_-2px_0_rgb(0_0_0/0.35),0_1px_0_rgb(255_255_255/0.06)] ${sizes[size]}`}
    >
      {children}
    </kbd>
  );
}
