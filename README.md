# Utter

Private voice typing and voice commands for your Mac. Press a shortcut, speak, and Utter
types what you said into any app, or does what you asked ("open Safari", "take a note").
Speech is turned into text **on your Mac** with OpenAI's Whisper model running on the Apple
GPU: no audio and no text ever leaves your computer.

Utter lives in the menu bar. It's a personal project, free and open source (MIT licence).

**Website and 20-second demo:** [getutter-app.vercel.app](https://getutter-app.vercel.app)

## What it does

| Shortcut | What happens |
|---|---|
| **⌃⌥D** (Control + Option + D) | **Dictation.** Tap, speak, tap again, or hold while you speak and let go. The text is typed where your cursor is, with "um"s, repeated words and quick corrections ("Monday, no wait, Tuesday") tidied up. |
| **⌃⌥M** (Control + Option + M) | **Commands.** Say one of the commands below; Utter stops listening when you stop talking. |
| **Esc** | Stop listening at once. Nothing is typed or run. |

Commands:
- "open notes", "take a note" (Utter writes down what you say next as a new note in Notes)
- "open Safari", "launch Calculator", "switch to Finder": any app on your Mac
- "open WhatsApp", "search WhatsApp for Alex"

Everything else is in **Settings…** in the menu bar: your own shortcuts, a word list for
names Whisper should spell your way, your own names for apps ("browser = Safari"), and more.

## What you need

- A Mac with Apple silicon (M1 or newer). It doesn't run on Intel Macs.
- macOS 14 Sonoma or newer.
- About 3 GB of free space (the speech model is 1.6 GB).
- 16 GB of memory is best. With 8 GB, the installer suggests the smaller model.
- Internet for installing only. Utter works offline after that.

## Install

Open **Terminal** (in Applications → Utilities) and paste this:

```bash
git clone https://github.com/Samakcha/utter_voice_assistant.git && cd utter_voice_assistant && ./install.sh
```

If macOS says Git needs Apple's command line tools, click **Install**, wait for it to finish,
then paste the command again.

The installer explains each step and asks before every download:

1. Checks your Mac.
2. Installs [uv](https://docs.astral.sh/uv/), which sets up a private copy of Python for Utter
   (nothing changes in your own Python, if you have one).
3. Installs the packages Utter needs, into this folder.
4. Downloads the speech model once from Hugging Face (1.6 GB; it carries on if your
   connection drops).
5. Offers to make a free signing certificate called "Utter Developer" in your keychain. With
   it, macOS remembers Utter's permissions when you update; without it, you allow them again
   after each update. macOS may ask once whether `codesign` may use it: click **Always Allow**.
6. Builds Utter.app on your Mac, puts it in Applications and opens it.

It takes about 10 to 15 minutes, mostly the downloads.

Then Utter's welcome window takes you through the rest: allowing the microphone,
Accessibility and Input Monitoring (Utter needs them to hear you, type for you and notice
its shortcuts), creating your Utter account, and a first try.

**Why not a normal download?** Apps downloaded from the internet must be signed through
Apple's paid developer programme, or macOS blocks them. An app you build on your own Mac
doesn't need that, so the installer builds Utter for you.

## Update

In Terminal, in the folder you cloned:

```bash
cd utter_voice_assistant && ./install.sh
```

It gets the newest version, quits Utter, rebuilds it and opens it again. Your settings, your
word list and the speech model are kept.

## Your Utter account

Utter asks you to create an account (email and password, confirmed with a 6-digit code) so
its author can count how many people use it. Only your email address and when you sign in
are stored, on Supabase. What you say and type never leaves your Mac. See
[PRIVACY.md](PRIVACY.md).

## Uninstall

1. Quit Utter (menu bar icon → Quit).
2. Delete **Utter** from Applications.
3. To also remove your settings, word list and the speech model, delete the folder
   `~/Library/Application Support/Utter` (in Finder: Go → Go to Folder…). Its log is in
   `~/Library/Logs/Utter`.
4. Delete the folder you cloned (`utter_voice_assistant`).
5. If you made the certificate: Keychain Access → login → My Certificates → Utter
   Developer → Delete.
6. In System Settings → Privacy & Security, you can remove Utter from Microphone,
   Accessibility and Input Monitoring.

## If something doesn't work

- **The shortcuts do nothing:** menu bar icon → Check Permissions. Accessibility and Input
  Monitoring must both be on for Utter.
- **"No speech model":** run `./install.sh` again in the folder you cloned.
- **Anything else:** menu bar icon → Settings… → Privacy → Open Log, and open an issue on
  GitHub with what you did and the last lines of the log (Utter hides names in it by
  default).

## For developers

```bash
uv venv --python 3.12 && uv pip install -r requirements-dev.txt
uv run python main.py                        # run from the terminal (uses Terminal's permissions)
uv run pytest -q                             # the tests
uv run python scripts/build_app.py --install # build Utter.app and put it in Applications
```

`PRD_Voice_Assistant_macOS_v0.3.md` describes how everything works and why.

The website is in `website/` (Next.js, deployed on Vercel): see `website/README.md`.

## Credits

- Speech to text: [Whisper](https://github.com/openai/whisper) by OpenAI (MIT), run with
  [MLX](https://github.com/ml-explore/mlx) and mlx-whisper by Apple (MIT), using the models
  converted by [mlx-community](https://huggingface.co/mlx-community).
- Handwriting in the welcome window: [Caveat](https://github.com/googlefonts/caveat) (SIL Open Font
  Licence, see `assets/fonts/Caveat-OFL.txt`).
- Hearing when you stop talking: [Silero VAD](https://github.com/snakers4/silero-vad) (MIT,
  see `assets/silero_vad_LICENSE.txt`).
- Menu bar: [rumps](https://github.com/jaredks/rumps); keys:
  [pynput](https://github.com/moses-palmer/pynput); macOS access:
  [PyObjC](https://github.com/ronaldoussoren/pyobjc).

## Licence

MIT: see [LICENSE](LICENSE).
