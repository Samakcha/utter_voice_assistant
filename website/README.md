# Utter website

The one-page site for [Utter](../README.md): what it does, the shortcuts, screenshots,
privacy and the install command. Next.js (App Router) with Tailwind CSS, a few components
from [React Bits](https://reactbits.dev) (Aurora, BlurText, ShinyText, GradientText,
SpotlightCard, StarBorder, CountUp, Magnet: copied into `src/components/reactbits`), and
[lucide](https://lucide.dev) icons. Everything is static: no server code, no analytics.

## Work on it

```bash
cd website
npm install
npm run dev      # http://localhost:3000
npm run build    # what Vercel runs
npm run lint
```

## Deploy on Vercel

1. On [vercel.com](https://vercel.com), choose **Add New… → Project** and import the GitHub
   repository `Samakcha/utter_voice_assistant`.
2. Set **Root Directory** to `website`. Vercel then detects Next.js by itself; leave the
   build settings as they are.
3. Press **Deploy**. Every push to `main` deploys again, and other branches get a preview link.

Social previews (the picture shown when the link is shared) use `public/screens/welcome.png`;
on Vercel the site's address is filled in automatically.

## Where things are

- `src/app/page.tsx`: every section of the page.
- `src/components/site/`: the pen circle and underline, key caps, the copy-the-command box,
  the animated dictation demo, the top bar, and the links (`constants.ts`).
- `src/components/reactbits/`: the React Bits components as published (lint skips them).
- `public/screens/`: screenshots of Utter's windows, rendered from the app itself.

Colours and the handwriting font (Caveat) match Utter's windows (`interface/welcome_style.py`).
