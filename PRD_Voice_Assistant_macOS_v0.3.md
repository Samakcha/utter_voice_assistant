# PRD: Voice-Assisted Dictation & Command App for macOS

**Product name:** Utter (chosen 17 November 2026)
**Status:** v0.6.0 (implemented and manually tested; replaces the v0.1 pre-development draft). v0.3.1 switched speech-to-text to Whisper `large-v3-turbo` with a local-only fallback. v0.3.2 stops saving session audio by default and moves personal data out of the project folder. v0.3.3 adds a personal word list for names and terms (section 6.11). v0.3.4 lets fixed commands run about 0.9 s sooner (early finish, 6.2). v0.3.5 adds hold-to-talk dictation (6.4). v0.3.6 adds an accuracy score measured on the user's own voice (10.1). v0.3.7 stops the score counting spelling-only differences as errors, and records the first real results (10.1). v0.3.8 completes Milestone 1: open any installed app by voice (6.12). v0.3.9 builds Milestone 2: dictation cleanup, removing "um"s, accidental repeats and clear self-corrections (6.13). v0.3.10 starts Milestone 3: the app is named Utter and the menu bar shows only a drawn icon that changes with the state (6.1). v0.3.11 moves everything out of the project folder: the data folder becomes `~/Library/Application Support/Utter`, with the user's own settings file and the Whisper models, and a log file is written to `~/Library/Logs/Utter` (6.14). v0.4.0 builds Utter as a real, self-contained Mac app, `Utter.app`, with its own Python inside (6.15). v0.5.0 adds the first-run welcome window (permissions with live ticks, a try-it step) and start at login (6.16). v0.6.0 requires an Utter account: sign in once with email and password, through Supabase, so the number of users can be counted (6.17).
**Last updated:** 19 November 2026
**Platform:** macOS, Apple Silicon (developed on a MacBook Air, M5, 16 GB RAM, 512 GB storage)
**Build approach:** Code written by an AI coding agent (Antigravity), guided by this document and step-by-step prompts. Every step is planned first, approved, built, then tested by hand on the real machine.

> **How to read this document.** Section 6 and onward describe what is actually built. Values (timeouts, defaults) are the ones agreed during development; `config.toml` is the source of truth, so verify against it if anything looks different. Items marked **Parked** were designed or discussed and deliberately not built.

---

## 1. Overview

A menu bar app for macOS that lets the user **dictate text** and **control the laptop by voice**. It is inspired by Wispr Flow, which only does dictation. This product adds spoken **commands that perform actions**: opening apps, taking voice notes into the Notes app, and navigating WhatsApp so the user can dictate a message and send it themselves.

- The app listens **only while it is running**. Quitting it closes the microphone completely.
- Everything runs **locally**: no cloud services, no cost, no audio or transcripts leaving the machine.
- The app **never sends a message to another person**. Sending is always a manual action by the user.

## 2. Problem Statement

Dictation tools turn speech into text but do not act on instructions. A user who wants to say "open Notes, take this down, then open WhatsApp and message someone" has to do every step by hand. There is no simple, private, local tool that combines dictation with voice-driven laptop control.

## 3. Goals and Non-Goals

### Goals (achieved)
- Reliable voice dictation into any focused text field.
- Voice commands for Notes and WhatsApp, with strict whole-sentence matching so ordinary speech never triggers an action.
- Voice note-taking that saves one new note in Apple Notes.
- Fully local speech-to-text.
- A clean separation between engine and interface so the interface can later be rebuilt natively.
- Safety by design: low-risk actions only, no sending, no reading of private content.

### Non-Goals (current version)
- Sending messages to other people, contacts lists, or phone-number handling.
- A settings screen (values live in `config.toml`).
- Cloud models, or distribution to other people (accounts exist since v0.6.0, 6.17; distribution is Milestone 4).
- Windows or Linux support.
- Open-ended "do anything on my laptop" automation.

## 4. Target Users

- **Phase 1 (now):** the developer, testing on their own Mac.
- **Phase 2 (later):** other Mac users who want private, local dictation and voice commands.

## 5. Core Concept: Two Hotkeys, Five Voice Commands

The hotkey tells the app the user's intent, so it never guesses whether speech is dictation or a command.

| Mode | Hotkey | Behavior |
|---|---|---|
| Command mode | `Control + Option + M` | Press once, speak a command, it ends on silence. |
| Dictation mode | `Control + Option + D` | Tap to start and tap again to stop, **or hold while speaking and let go** (hold-to-talk). Text is pasted once at the end. |
| Cancel | `Esc` | Cancels any active session; nothing is pasted or saved. |

All hotkeys are handled by **one single keyboard listener** (see section 11 for why).

**Voice commands (command mode):**

| Command id | Example phrases | Action |
|---|---|---|
| `open_notes` | "open notes", "open the notes app", "can you open notes", "launch notes", "open the note app" | Opens the macOS Notes app. |
| `take_note` | "start taking notes", "take a note", "new note", "create a note", "make a note" | Starts a note session that saves one new note. |
| `open_whatsapp` | "open WhatsApp", "launch whatsapp", "bring up whatsapp" | Launches and brings WhatsApp forward. |
| `whatsapp_search` | "search for `<name>`", "find `<name>`", "search whatsapp for `<name>`" | Types the name into WhatsApp's own search box (WhatsApp must already be in front). |
| `open_app` | "open Safari", "launch Calculator", "switch to Finder", "bring up Activity Monitor", "open settings" | Opens any installed app (6.12). Tried last, so the four commands above keep priority. Unknown or ambiguous names open nothing. |

## 6. Functional Requirements

### 6.1 App shell
- Menu bar app named **Utter**, with Stop Utter / Start Utter / Quit, a "Check Permissions" item, "Edit Word List…" (see 6.11), "Edit Settings…" (the user's `config.toml`, 6.14), "Open Log" (6.14), and "Open Recordings Folder" (the folder only fills when `keep_recordings = true`). The first menu line spells out the status in words.
- **Icon only in the menu bar, no text** (`interface/menu_icon.py`), drawn in code as a macOS template image, so it turns white or black to match the menu bar like other apps' icons. One fixed size (18 × 16 points) in every state, so neighbouring icons never shift:
  - **Ready:** a still five-bar waveform, the Utter mark.
  - **Command mode:** a pulsing orb, slightly larger while you speak.
  - **Dictation and notes:** five bars that follow the live microphone level (a row of short dashes when silent), redrawn 15 times a second by a main-thread timer that runs only while listening.
  - **Loading model / stopped:** the waveform, dimmed.
  - **No "transcribing" icon:** when you stop speaking the icon goes straight back to ready. The pop-up shows nothing after you stop speaking (6.7).
- **App icon:** the same waveform, white on a deep indigo rounded square on Apple's icon grid, drawn by `scripts/make_app_icon.py` into `assets/utter-app-icon.png` (1024 × 1024); used when the app is packaged.
- The microphone is open only during an active session.
- A floating **overlay** shows a moving sound wave while listening, and problems as text (see 6.7).
- Sessions are finalized on a worker thread (never on the audio callback thread; see lessons learned).

### 6.2 Command mode (`Control + Option + M`)
1. Press the hotkey; the app starts listening.
2. Silero VAD detects speech. Recording **auto-stops after trailing silence** (default 1.5 s; see tuning note in 12.4). If no speech starts within 5 s, the session cancels quietly.
   - **Early finish** (`command_early_check_sec = 0.6`): after 0.6 s of silence the speech so far is transcribed in the background. If it is a complete *fixed* command (`open_notes`, `take_note`, `open_whatsapp`) and the user has still not spoken when the result is ready, the session ends and the command runs at once, reusing that transcript (no second transcription). End of speech to action drops from about 1.9 s to about 1.0 s with `large-v3-turbo`.
   - **Open an app early** only when the name surely names one installed app (alias, exact or compact match) and no longer app name starts with it: "open Calculator" runs at the pause, "open Antigravity" waits in case "IDE" follows.
   - **Never early:** WhatsApp search (the name may still be continuing, "Rahul" ... "Sharma"), app names found only by a partial or fuzzy match, anything unrecognized, and any pause the user speaks after. These wait the full 1.5 s and are transcribed in full, as before.
   - **Why not just lower the timeout:** the rules deliberately ignore longer phrases that start like a command ("take a note about groceries", "open WhatsApp and search Rahul": 21 such phrases in the evaluation set). A shorter timeout would cut those off for every command; early finish only acts after about 1 s of real silence on a phrase that is already complete.
3. The audio is transcribed by Whisper on a dedicated worker thread.
4. The transcript goes through the **rule layer** (whole-utterance grammar). No LLM is used.
5. A matching command runs immediately (all current commands are low risk). No match means no action, and the overlay says "Nothing recognized".
6. The overlay shows what was heard and what happened; the terminal log prints a latency breakdown.

### 6.3 Rule layer (`engine/commands.py`)
The whole normalized utterance must match a command's grammar: lowercase, punctuation stripped, whitespace collapsed, then a fully anchored pattern.

- **Verbs for opening:** `open`, `open up`, `launch`, `bring up` (and `switch to` for apps). (`show` and `start` are deliberately excluded: "show notes" is common speech.)
- **Politeness wrappers accepted:** "can you", "could you", "would you", "will you", "please", "just", "kindly", "hey", "hi", "hello", "go ahead and", and suffixes "please", "for me", "thanks", "thank you".
- **Whisper variants accepted:** `note`, `notes`, `node`, `nodes` (Whisper really produced "nodes" and the singular "note").
- **Deliberate rejections:** "open a note" (means a specific note, not the app), "open the notes folder", "I took some notes yesterday", "don't open notes", "open notes and send it to someone", "take notes from the meeting" (extra words are rejected).
- **No overlap:** an automated test checks that no phrase in the evaluation CSV matches more than one command.
- **`open_app` name slot:** 1 to 5 words; never starting with a, an, some, it, this, that; never containing note/notes/node/nodes/whatsapp or connectors (and, then, also, after, before, later, today, tomorrow, now, when, while, because, so). This keeps "open a note", "open notes folder" and "open WhatsApp and search Alex" non-commands, as before. Only "open safari" and "launch terminal" changed from no-command to `open_app` in the evaluation set.
- **`whatsapp_search` name slot:** 1 to 4 words; letters in any script (including Devanagari), apostrophes and hyphens; single-letter middle initials allowed; digits and symbols rejected; names beginning with reserved words (the, a, an, my, notes, note, whatsapp, chat, app) rejected.

### 6.4 Dictation mode (`Control + Option + D`)
1. Press the hotkey to start a **continuous** session. Two ways to end it with the key:
   - **Tap** (shorter than `hold_min_sec`, 0.4 s): dictation keeps going; tap again to end (the original toggle).
   - **Hold** (0.4 s or longer): dictation ends when you let go, after a 0.3 s tail (`hold_release_tail_sec`) so the last word is not clipped. Letting go of D, Control or Option all count as letting go.
   - Recording starts on key-down either way, so nothing is lost while the app decides between tap and hold.
   - **How the keys are read:** one raw keyboard listener (key-down, key-up and modifier changes) matches hotkeys by **physical key code**, because Option changes what "D" types ("∂"). Auto-repeat while held is ignored. The app **swallows its own two hotkeys** (an active event tap), so the focused app never sees them: no beeps, no stray characters, and no Ctrl+D (delete forward) if Option is let go first. Esc is never swallowed.
   - **Safety:** a watchdog checks the keyboard's physical state every 0.25 s while the key is held and releases if a key-up was missed. A delayed release only stops the session that press started (checked under the session lock). Callbacks run on a separate thread, so the event tap never waits on audio or UI work.
   - **Fallback:** swallowing keys needs Accessibility permission. Without it, or with `hold_to_talk = false`, the app uses the previous press-only listener (tap to toggle).
2. **Chunked background transcription:** when the VAD sees a pause of at least `chunk_pause_sec` (0.8 s) after speech, the speech so far is cut (about 300 ms padding each side, silence trimmed, volume normalized) and transcribed in the background while recording continues. Chunks shorter than 250 ms of speech are skipped (they cause Whisper hallucinations).
3. A **chunk joiner** stitches the pieces (Whisper capitalizes and punctuates each chunk, so mid-sentence pauses are repaired).
4. Every chunk carries a **session id**; late results from cancelled or finished sessions are ignored.
5. **Nothing is pasted until the session ends.** Then the final text is pasted once (see 6.6).
6. The session ends on: a **stop phrase**, the dictation hotkey pressed again, letting go after a hold, **8 s of silence**, or Esc (cancel, paste nothing).
7. The LLM is never involved in dictation.
8. The **personal word list** (6.11) is hinted to Whisper for every chunk, and close mishearings of listed names are fixed in the final text before pasting.
9. **Cleanup** (6.13) tidies the final text first: fillers, accidental repeats and clear self-corrections.

### 6.5 Stop phrases
- Defaults: `"stop dictation"`, `"end dictation"` (dictation); the note session also accepts `"stop note"`. The earlier default "end note" was dropped (too common a phrase).
- Matching is **loose** (normalization plus fuzzy tolerance 0.2) so "Stop dictation." or "stop dictating" still match, but **only at the end** of the transcript, so "tell him I'm done with the report" or "stop dictation is a feature" do not end the session.
- Short words must match exactly (so "send note" and "I will send dictation" do not match).
- The phrase is **removed** from the final text, along with dangling connectors and punctuation ("... tomorrow, stop dictation." becomes "... tomorrow.").
- A startup warning is logged for phrases that are very short or common ("okay", "yes", "done").
- Stop phrases apply to dictation and note sessions only, never to command mode.

### 6.6 Text insertion (clipboard paste)
- The app saves **all** pasteboard items and types, writes the dictated text marked as transient/concealed (so clipboard managers skip it), waits for Control and Option to be physically released (up to 500 ms), posts **Cmd+V from a Quartz event on the main thread**, waits `clipboard_restore_delay_ms` (400 ms, configurable), then restores the original clipboard, but only if the pasteboard `changeCount` shows nothing else changed it.
- Esc, empty text and no-speech sessions never touch the clipboard.
- The text lands in whatever app is frontmost when the session ends.
- **Never lose dictation when pasting is blocked:** macOS only lets an app press ⌘V for you with Accessibility; without it the keystroke is silently dropped, and restoring the old clipboard afterwards would erase the dictation. So before pasting, Utter checks Accessibility; if it is missing, the text is left on the clipboard as normal text (not restored), the pop-up says "📋 Copied: press ⌘V to paste (Utter needs Accessibility)", and the log records the word count only (found 18 November 2026: commands worked while Accessibility counted as missing, but dictation could vanish).

### 6.7 Overlay (the pop-up at the top of the screen)
- A small floating, **non-activating** panel at the top center of the screen: it never takes keyboard focus, appears over full-screen apps and all Spaces, and fades out on its own.
- **Compact, at the top:** a small dark pill in the style of Wispr Flow's (only the look; Utter's stays at the top of the screen, 6 points under the menu bar, centred). Always dark with a hairline light edge, in Light and Dark mode. The wave pill is 96 × 26 points (it was 226 × 40); message pills are 30 points tall and as wide as their text (at least 120).
- **While listening** (command, dictation or note) it shows only a **moving sound wave**, no text (`interface/waveform.py`): 14 bold white bars (3 points wide, up to 18 tall), centred on a line and shorter towards both ends. It is deliberately lively (asked for by the user): 24 frames a second, quiet speech boosted (level to the power 0.5), strong differences between neighbouring bars, every bar flickering each frame while you speak, and small twitches even in silence; it reacts at once and calms down within about a third of a second. Each frame pushes in the live microphone level, so the wave scrolls left while you speak. The same microphone level drives the menu bar bars.
- **Afterwards it simply disappears:** no "Transcribing", "Inserted N words", "Heard … → Opening …", "Saved note" or WhatsApp progress messages (chosen by the user on 18 November 2026). The pasted text, the opened app or the new note is the confirmation.
- **Problems are still shown as text**, because otherwise there is no way to know why nothing happened: unknown or ambiguous app names, "Nothing recognized", "Nothing to insert / save", action failures, "Couldn't save note … text copied to clipboard", the WhatsApp warnings and the WhatsApp "pick the chat, click the message box" guidance.
- All AppKit work runs on the main thread only; each new state cancels any pending fade-out, and the wave's timer runs only while the wave is shown.
- Esc hides the overlay immediately.

### 6.8 Voice notes (`take_note`)
1. In command mode say "start taking notes". The command session fully ends first (microphone closed), then a **note session** starts from the main thread.
2. The note session reuses the dictation machinery (chunking, stop phrases, chunk joiner, session ids, cleanup, personal word list).
3. It ends on a stop phrase ("stop note"), either hotkey, the **10 s** silence timeout, or Esc (creates nothing).
4. At the end, **one new note** is created in Apple Notes with a title line (default `Voice note {date} {time}`) and the full text.
5. **Saving:** `osascript` (AppleScript) with the text passed as **arguments** (never spliced into script source, so quotes, backslashes and injection attempts are inert). The body is HTML-escaped (`&`, `<`, `>`), newlines become `<br>`, and Hindi/emoji text is preserved. Runs on a worker thread with a 15 s timeout. Notes is not brought to the front unless `reveal_after_save = true`.
6. **Never lose dictation:** if saving fails for any reason (including Automation permission denied, error -1743), the text is copied to the clipboard as normal text and written to a backup `.txt` in `~/Library/Application Support/Utter/note_backups/` (not pruned), and the overlay explains why.
7. Empty text creates nothing ("Nothing to save").

### 6.9 WhatsApp navigation
Targets the **native WhatsApp desktop app** (Mac Catalyst; bundle id `net.whatsapp.WhatsApp`). The design was decided by the user: no contacts file and no name matching in this app. The user picks the chat on screen (or searches WhatsApp's own search box by voice), clicks the message box, dictates with `Control + Option + D`, and **presses Enter personally**.

**`open_whatsapp`:** runs `open -b net.whatsapp.WhatsApp`, then confirms WhatsApp is frontmost. Only this command may launch or activate WhatsApp.

**`whatsapp_search`:**
1. Acts only if **both** independent checks report WhatsApp as frontmost (`NSWorkspace` and `lsappinfo`). Otherwise: do nothing, overlay says "WhatsApp isn't in front. Say 'open WhatsApp' first."
2. `AXCancel` on the search field (clears any previous search), then `AXPress` on it, then verify the focused element has subrole `AXSearchField`. If not, try Cmd+F once; if still not, do **not** paste ("Couldn't focus WhatsApp search").
3. Paste the name with the clipboard save/restore routine (6.6).
4. Overlay: "Pick the chat, click the message box, then press Control+Option+D to dictate. Press Enter to send."

**Hard safety rules (enforced by tests):**
- The WhatsApp module may post only **Cmd+F (keycode 3)** and **Cmd+V (keycode 9)**. No Return/Enter, Delete, Esc, Cmd+A or arrow keys, anywhere.
- `AXPress` and `AXCancel` are used **only** on the search field.
- Only structural Accessibility attributes (role, subrole, position, size, focus, children) are read. Titles, values, descriptions and any chat or contact content are never read. No screenshots.
- Accessibility calls use a ~1 s messaging timeout so an unresponsive WhatsApp cannot freeze the menu bar.
- If the search field cannot be found (the accessibility tree can go stale after WhatsApp runs for a long time), the overlay says "WhatsApp's UI isn't readable. Try restarting WhatsApp."

### 6.10 Privacy requirements
- **Names spoken in WhatsApp searches are redacted at the source** (`<name: N chars>`) in every output: terminal blocks, log lines and macOS notifications (which persist in Notification Center). The on-screen overlay may show the name.
- **No audio on disk by default** (`keep_recordings = false`): command, dictation and note audio is transcribed from memory and never written as a WAV file. Command mode passes the captured audio to Whisper as an in-memory array; dictation and notes already transcribe in-memory chunks. Setting `keep_recordings = true` saves recent recordings for debugging or for building a benchmark set (at most `max_recordings_kept`, oldest pruned first).
- **Personal data lives outside the project folder:** recordings (when kept) and failed-note backups go to `~/Library/Application Support/Utter/`, not next to the code, where a coding agent could read them.
- `keep_whatsapp_audio = false`: when recordings are kept, the WAV of a WhatsApp command is still deleted right after transcription.
- `keep_audio_when_whatsapp_front = false`: when WhatsApp is in front, dictation and note audio are not kept even if `keep_recordings = true`, and the dictated text is not logged (word count only).
- No contacts file and no WhatsApp data folders are read. All tests use obviously fake names ("Alex Test", "Sam Example").
- **Older data left in place:** `recordings/` and `note_backups/` in the project folder, written by builds before v0.3.2, were not moved or deleted. The user decides what to keep (the recordings are useful for the benchmark in section 15).
- **Test safety net:** `conftest.py` fails any test that writes into `~/Library/Application Support/Utter/`, the old `.../VoiceAssistant/` or `~/Library/Logs/Utter/`, renames them, or moves the project's models; the data paths, the fallback model folder and the folder rumps creates per app all point at a per-test folder; any word-list path in the real folder is redirected to an empty test file, so tests never read the user's real names.
- **Log file:** contains what the logger writes (timings, recognized commands, errors), with the same redaction as before; dictated and note text is never written to it (that only appears in the terminal).
- **Word list:** stored with the other personal data (6.11); logs show only how many words were fixed unless `redact_names_in_logs = false`.
- **Accounts (6.17):** the only data that leaves the Mac is the email address and password typed into the sign-in window, sent over HTTPS to Utter's Supabase project. Voice, dictated text, notes, the word list and settings never do. The password is never stored or logged; the session tokens are kept in the macOS Keychain; logs say only "signed in / signed out" (no email). Tests can never reach the network or the real Keychain (`conftest.py`).

### 6.14 Data folder, settings file and log (`engine/data_folder.py`)
Milestone 3, stage 1: nothing Utter needs lives in the project folder any more, so the packaged app can run on its own.

1. **Data folder:** `~/Library/Application Support/Utter` (word list, accuracy recordings, note backups, settings, models). On the first start of v0.3.11 the old `~/Library/Application Support/VoiceAssistant` is **renamed** to it: nothing is copied or deleted. If both exist with data, nothing is merged and the old one is left as it is. An empty Utter folder (rumps creates one per app name) does not block the rename.
2. **Settings:** the shipped `config.toml` (project folder, later inside the app) holds the defaults and is copied once to `<data folder>/config.toml`; Utter reads that copy from then on, so settings survive updates. Menu: "Edit Settings…" opens it in the text editor; changes apply after a restart. The accuracy and benchmark scripts read the same file.
3. **Models:** moved from the project's `./models` to `<data folder>/models` on first start (a rename on the same disk: instant, no extra space). Each model is moved under a temporary `.moving` name and renamed when complete, so an interrupted move is redone from the intact original; a model already in the data folder is never overwritten. The project's `./models` is still searched if a model is missing in the data folder.
4. **Log file:** `~/Library/Logs/Utter/utter.log`, up to 1 MB, plus 3 older files; menu "Open Log" opens it in Console. The command log line now includes the speech-end-to-result time.
5. **Never blocks start-up:** a step that fails is logged and Utter starts anyway (shipped settings, models found in the old place). The accuracy script runs the same setup first, in case it runs before the app.
6. **Checked:** 16 new automated tests (rename, both folders present, settings copied once and kept, models moved, never overwritten, interrupted move, failure, log file, startup, menu items). Rehearsed on instant clones of the user's real data folder (147 files) and models: everything carried over, the word list was byte-identical, both models moved, the settings file was created, the move took 0.001 s, and a second start changed nothing.

### 6.15 Utter.app (`scripts/build_app.py`, `packaging/launcher.c`)
Milestone 3, stage 2: a double-clickable app in Applications instead of `uv run` in a terminal.

1. **Build:** `uv run python scripts/build_app.py` makes `dist.noindex/Utter.app` (about 15 s); `--install` also copies it to `/Applications` (refused while Utter runs from there). **Nothing is downloaded:** the app is assembled from the project's code, the standalone Python that uv installed, and the packages in `.venv`, cloned on APFS (instant, no extra disk space until changed).
2. **Inside the app** (about 1.4 GB): `Contents/MacOS/Utter` (launcher), `Contents/Resources/python` (a private Python 3.12 with the packages; tests, pip, pytest and tkinter left out), `Contents/Resources/app` (main.py, engine, interface, the shipped config.toml, assets), `Utter.icns` (from the 1024 px icon, 16 to 1024 px). Models, settings and personal data are **not** inside: they stay in `~/Library/Application Support/Utter` (6.14).
3. **Launcher:** a small C program that runs Python inside its own process (embedded), so macOS sees one app called Utter and asks for, and remembers, permissions as "Utter" (not Terminal or Python). Python ignores outside `PYTHON*` settings and user packages, runs in UTF-8 mode (apps get no language setting from macOS), and never writes `.pyc` files into the signed app (they are compiled at build time).
4. **Info.plist:** bundle id `com.utter.mac` (changing it later makes macOS treat Utter as a new app), menu bar only (`LSUIElement`, no Dock icon), Apple Silicon only, macOS 14+, and the texts macOS shows in the Microphone and Automation prompts (without them macOS stops the app when it asks).
5. **Signing:** with a free self-signed certificate named **"Utter Developer"** in the login keychain, every build is signed with it, so macOS keeps Utter's permissions across rebuilds; without one the build is signed ad hoc (it runs, but each rebuild needs the permissions granted again). Create it once in Keychain Access → Certificate Assistant → Create a Certificate… (Name: Utter Developer; Identity Type: Self-Signed Root; Certificate Type: Code Signing). No Apple account or payment.
6. **Asking for permissions:** on the very first start the welcome window does the asking (6.16). On later starts the app asks macOS for each missing permission at start, **each one only once ever** (`ask_missing_once`, remembered in `permissions_asked.json` in the data folder), so macOS's window appears on the first start and not on every start. If a permission is still missing later, the first menu line says so quietly ("⚠️ Needs permission: Accessibility (see Check Permissions)") and the hint clears after Check Permissions finds it granted. Asking (`request_missing_permissions`): the Microphone prompt ("Allow / Don't Allow"), the Accessibility prompt, and the Input Monitoring prompt. Asking is what puts Utter in those lists in System Settings: the Microphone list has no + button, so an app only appears there after it asks. "Check Permissions" → "Open System Settings" asks again before opening the right pane. A permission refused once is not asked again by macOS (it stays in the list, switched off). When Accessibility and Input Monitoring are turned on while the welcome window is open, the hotkeys restart by themselves (otherwise quit and reopen Utter). The terminal version never asks (it uses the terminal's permissions). **Lesson (18 November 2026):** a switch turned on for an older build that was signed differently (ad hoc, before the certificate) stays on in System Settings but does not count for later builds, so macOS kept reporting Accessibility as missing; the fix is to remove Utter from the list with − and turn it on again once. Builds signed with the "Utter Developer" certificate keep the permission across rebuilds.
7. **Helper processes:** libraries such as multiprocessing start helpers by running the app's own executable with Python options (`-B -c ...`); the launcher runs those as plain Python, so they never start a second Utter.
8. **Builds are not in Spotlight:** the build goes to `dist.noindex/` (Spotlight skips `.noindex` folders), so searching "Utter" only finds `/Applications/Utter.app`.
9. **One Utter at a time:** a lock file in the data folder; a second copy (for example the app while the terminal version runs) shows "Utter is already running" and exits.
10. **Self-test:** `Utter.app/Contents/MacOS/Utter --self-test file.wav` loads the voice detector and Whisper and transcribes, without menu bar, hotkeys or microphone; it can never hang (a 45 s watchdog prints every thread's stack and exits). `--check` prints the permission report. Both exit straight away when done (Python's exit clean-up once waited on CoreAudio on the first run of a new build).
11. **Checked:** 16 new automated tests. The built app started from an empty environment (as macOS starts apps), read the user's settings and log file from the Utter folder, found the MacBook microphone, reported its permissions as "Utter.app", and transcribed a spoken sentence with `large-v3-turbo` exactly ("Open Safari and take a note about the meeting tomorrow.", 0.45 s; model ready in 1 to 5 s); 3 fresh builds and 9 runs exited cleanly. Not yet run as the menu bar app or with permissions; that is the user's live test.

### 6.16 First-run welcome and start at login (`interface/setup_flow.py`, `welcome_window.py`, `login_item.py`)
Milestone 3, stage 3: a new user gets through the permissions without help.

1. **When:** the first time Utter.app opens (no `setup.json` in the data folder, or `"done": false`), the welcome window opens instead of macOS's permission prompts. It never opens by itself again, even when closed early; the menu item **Setup…** reopens it. The terminal version never opens it by itself (Setup… still works).
2. **Four steps** (Back / Continue; "Step n of 4"): **Welcome** (what Utter does, the two hotkeys written the Mac way, ⌃⌥D and ⌃⌥M, taken from the settings); **Permissions** (Microphone, Accessibility, Input Monitoring, one row each with a plain reason); **Try it** (press ⌃⌥D and speak into a box in the window); **Finish** ("Start Utter when I log in", **on by default**, then "Start Using Utter").
3. **Live ticks:** the window checks the three permissions twice a second; each row shows ✓ by itself the moment macOS reports it on, and the window comes back to the front from System Settings. The next permission still off is highlighted; the main button reads "Skip for Now" until all three are on, then "Continue".
4. **Row buttons:** "Turn On" the first time asks macOS (its own prompt, and Utter is added to the list in System Settings; remembered in the same `permissions_asked.json` as 6.15, so nothing is asked twice); afterwards "Open Settings" opens the right page. A row still off 12 s after its button shows the fix for a stuck entry: remove Utter with − and press Open Settings again, or run `tccutil reset <list> com.utter.mac` in Terminal (selectable text; the user runs it, Utter never changes security settings itself).
5. **Hotkeys:** when Accessibility and Input Monitoring both turn on while the window is open, the hotkey listener restarts by itself (with hold-to-talk as configured), so no restart is needed. If nothing is typed 20 s into the Try step, a **Restart Utter** button appears (Utter quits, waits until it has exited, opens again and resumes on the Try step).
6. **Try box and ⌘V:** Utter has no Edit menu (menu bar app), so the window handles ⌘V, ⌘C, ⌘X, ⌘A, ⌘Z and ⌘W itself; dictation types into the box with ⌘V like into any app. The box turns into "✓ It works!" as soon as text appears.
7. **Start at login:** macOS's own Login Items (`SMAppService.mainAppService`, ServiceManagement loaded straight from the system, no extra package). Applied when the welcome is finished, and switchable any time with the menu item **Start at Login** (checkmark). If macOS needs approval it opens System Settings > General > Login Items. Only for Utter.app: the terminal version says so.
8. **Checked:** 34 new automated tests (steps, records, ticks, stuck hints, hotkey restart once, try-it, login item with a fake macOS service, the window with fake permissions, the menu items). All four steps were rendered in light and dark mode and the layout checked. The built app (v0.5.0) starts and loads the new parts. The live first run is the user's test.

### 6.17 Utter accounts, required sign-in (`engine/account.py`, `interface/account_manager.py`, `account_form.py`, `keychain.py`)
Decided 19 November 2026: everyone signs in once, so the number of users can be counted. Login is required; the service is Supabase (free plan); built before the settings window.

1. **Where:** a new step 2 of the welcome window, "Create your Utter account" (5 steps now). Continue stays off until signed in; there is no Skip. Later starts, while nobody is signed in, open the same step alone as the **"Sign in to Utter"** window.
2. **Locked until signed in:** both hotkeys do nothing but show "Sign in to use Utter" and open the sign-in window; the first menu line reads "🔒 Sign in to use Utter". The menu has **Sign In…**, or **Sign Out (email)** with a confirmation.
3. **The forms:** *create* (email, password of 8+ characters, "I already have an account"); *check your email* (the 6-digit code from the email; "Send the code again", "Use a different email"); *sign in* ("Forgot password?", "Create an account"); *reset* (code from the email plus a new password); *signed in* ("Use a different account"). Typed values are checked before anything is sent; errors are plain and red ("Wrong email or password.", "An account with this email already exists. Sign in instead.", "Can't reach the internet…"). Calls run on a background thread with a spinner; the window and menu bar never freeze.
4. **Codes instead of links:** confirming an email and resetting a password use the 6-digit code (`{{ .Token }}`) typed into Utter, so no website is needed. Signing in before confirming asks for the code (and sends a new one). An already-registered email is detected although Supabase hides it (empty `identities`).
5. **Staying signed in:** the session (access and refresh tokens, email, user id; never the password) is kept in the macOS Keychain (service `com.utter.mac.account`), read at every start, and renewed in the background at start and every 6 hours. **Offline, Utter keeps working**; only if Supabase says the login is no longer valid (account removed or blocked, token revoked) is the user signed out.
6. **The project:** `SUPABASE_URL` and `SUPABASE_ANON_KEY` in `engine/config.py`, holding the project's **publishable key** (`sb_publishable_…`, Supabase's new name for the anon key; public by design). The **secret key** (`sb_secret_…`, formerly service_role) must never be in the app. The key is sent only as the `apikey` header; `Authorization: Bearer` carries a signed-in user's token, never the project key (new publishable keys are refused there). While they are empty, accounts are off and nobody is locked out (developer copies). `[account] url / anon_key` in `config.toml` can point a copy at another project; signing in cannot be switched off there. Plain HTTPS with Python's own `urllib` and macOS's certificates: no new package.
7. **Supabase setup (done by the owner in the Supabase dashboard):** create the project; Authentication > Email: keep "Confirm email" on; edit the **Confirm signup** and **Reset password** templates to show `{{ .Token }}` (the 6-digit code); before real users, add a **custom SMTP** sender (Supabase's own sends only about 2 emails an hour and only to the project team's addresses). The user count is in Authentication > Users.
8. **Limits:** a determined person could remove the check from their copy (it is a local app); this counts users, it is not copy protection. Once there are real users' emails, a privacy policy is needed (Milestone 4 website).
9. **Checked:** 36 new automated tests against a fake Supabase (sign-up with code, duplicate email, wrong password in both error styles, unconfirmed email, refresh and revoked login, offline, password reset, Keychain round trip, the form's modes, the window's required step and sign-in window, the app's lock and menu). The Keychain bridge was tried for real once (a test item written, read, updated and removed). Not yet tried against the real Supabase project: waiting for its URL and anon key.

### 6.11 Personal word list
Fixes misheard names and unusual terms ("Sanavi" → "Saanvi", "Superbase" → "Supabase") without retraining anything.

1. **The list:** `~/Library/Application Support/Utter/vocabulary.txt`, one name or term per line, spelled the way it should be written; `#` starts a comment. The menu item "Edit Word List…" creates the file with instructions (if missing) and opens it in the default text editor. Edits apply from the next dictation without a restart (the file is re-read when it changes).
2. **Scope:** dictation and notes only. Commands are never hinted or corrected, so the command rules stay predictable.
3. **Hint to Whisper:** each chunk's prompt is `[initial_prompt] Vocabulary: a, b, c. [previous chunk]`. At most 50 terms and about 400 characters, and the previous chunk is trimmed to about 300 characters, because mlx-whisper keeps only the last 223 prompt tokens and long hints can make Whisper invent words. With an empty list the prompts are exactly as before.
4. **Echo guard:** if Whisper repeats the hint back ("Vocabulary: …", or a transcript that is only three or more listed terms), it is removed from that chunk before stop-phrase detection, so an echo can never hide a trailing stop phrase.
5. **Strict post-correction** on the final text (`auto_correct = true`):
   - *Exact fixes* (same letters, different case or spacing): "prd" → "PRD", "anti gravity" / "anti-gravity" → "Antigravity", "git hub" → "GitHub".
   - *Close fixes* ("Rahool" → "Rahul", "Kavia" → "Kavya"): only for listed terms of 5+ letters, same first letter, at most 1 edit (2 for terms of 8+ letters), plus one extra edit for vowel-only slips when the first three letters match.
   - *Never changed:* ordinary English words (lowercase entries of the macOS system dictionary, common modern words and simple inflections), so listing "Will" never turns "will" into "Will"; words inside URLs, emails and file names; contractions; numbers. Terms of 4 letters or fewer ("Neha", "PRD") are only fixed by exact spelling.
   - Without the system dictionary (for example a future Windows build) only exact fixes run.
6. **Settings:** `auto_correct = false` keeps the hint but never rewrites text; `enabled = false` turns off both.

### 6.12 Open any installed app (`engine/apps.py`)
Milestone 1. "Open Safari", "launch Calculator", "switch to Finder": any app on the Mac, with no list to maintain.

1. **App list:** built by listing `/Applications`, `/Applications/Utilities`, `/System/Applications`, `/System/Applications/Utilities`, `~/Applications` (and one plain subfolder level, such as "Python 3.12"), plus Finder. Each app's `Info.plist` is read for its bundle id and other names ("Code" for Visual Studio Code, "Voice Memos" for VoiceMemos). Background-only and hidden apps are skipped; duplicates (same bundle id) are kept once. About 80 apps in 0.03 s on the development Mac. No Spotlight, nothing sent anywhere.
2. **When:** scanned in the background at start, and again when a name is not found (at most every 5 s), so an app installed while the assistant runs is found without a restart.
3. **Matching levels** (the first level with any match decides): your aliases, then built-in aliases ("chrome" → Google Chrome, "settings" / "system preferences" → System Settings, "quicktime", "vs code", Microsoft Office short names; only if the app is installed) → exact name → compact, spaces ignored ("face time" → FaceTime, "chat gpt" → ChatGPT, "x code" → Xcode) → partial, every spoken word a whole word of exactly one app ("activity" → Activity Monitor; generic words such as "app", "pro", "player" never count) → fuzzy, one slip in a name of 5 to 8 letters or two in 9+ ("spotifi" → Spotify), only if one app is clearly closest. "the", "my" and a trailing "app" are ignored.
4. **Refusals:** an unknown name opens nothing and the overlay says `No app called "door"`; a name matching two or more apps at the same level opens nothing and lists them ("Which app: Google Chrome or Google Drive? Say the full name."). These show as "Nothing opened", not as a failure.
5. **Launch:** `open <path of the .app>` with an argument list (no shell), only for a real `.app` folder found by the scan.
6. **Settings** (`[apps]`): `enabled = true`; `[apps.aliases]` maps what you say to an app's Finder name, for names Whisper hears differently (for example `cloud = "Claude"`).
7. **Checked:** 74 automated tests; every app sentence in the accuracy set resolves correctly against this Mac's 82 apps; 16 phrases spoken by the Mac's synthetic voice and transcribed by turbo all did the right thing (14 opened the right app, "open the door" and "open Safari and search for flights" opened nothing). **User's voice (14 November 2026):** the 10 app commands scored 10 of 10 (32 of 32 commands overall, MacBook Air microphone), and the live test in the running assistant opened every app correctly.

### 6.13 Dictation cleanup (`engine/cleanup.py`)
Milestone 2. The text you meant, not every sound you made. Rules only: offline, no model, well under a millisecond. Applied to dictation and notes after the stop phrase is removed and before the word list (6.11); never to commands. **When a rule is not sure, the text stays as spoken.**

1. **Fillers:** sounds only ("um", "uh", "er", "erm", "ah", "hmm", "mm", in any length such as "umm"). Real words are never removed: "like", "you know", "so", "well", "hum", and names such as "Umar" or "Ahmed". Capitalised codes ("ERR 404") are kept. Commas around a filler go with it ("We need, uh, three" → "We need three"); a sentence that was only "Um." disappears.
2. **Accidental repeats** of one to three words, also with a comma between ("the the", "I, I", "can you, can you"). Kept on purpose: doubles people say deliberately or that are correct grammar ("very very", "no no no", "bye bye", "ha ha", "hello hello", "that that", "had had", "is is"), numbers ("5 5 7 2"), and repeats across a full stop ("Go. Go.").
3. **Self-corrections**, only after a clear phrase: "no wait", "wait no", "no sorry", "sorry I mean", "I mean", "actually no", "or rather", and plain "no" only between two numbers or two days ("three, no, four"). The phrase must be set off by a pause (a comma or full stop around it). What is replaced:
   - a number, time, price, day or month by the next one of the same kind ("Monday. No wait. Tuesday." → "Tuesday"; "at 6 tonight, I mean 7" → "at 7 tonight");
   - or a short phrase that restarts from the same small word ("the red one, I mean the blue one" → "the blue one"; at most 4 new words, never across a full sentence).
   - Left alone: "I mean it", "It's okay. I mean, it's not bad.", "there was no wait at the desk", "I'm sorry, Tuesday doesn't work", "I have 2 kids. I mean 2 is enough."
4. **"Scratch that" / "strike that"** on its own (followed by a pause or the end) removes the sentence before it ("Call him tomorrow. Scratch that. Email him." → "Email him."). "Scratch that itch" is a normal sentence and is kept.
5. **Settings** (`[cleanup]`): `enabled`, and `fillers`, `repeats`, `corrections` to switch each rule off. With `redact_names_in_logs = true` the log only counts changes ("Cleanup: 1 filler, 1 correction."); otherwise it shows them.
6. **Checked:** 100 new automated tests (each rule, everything that must stay unchanged, chunked dictation, notes, the log). On the user's 65 recorded dictation sentences cleanup changed **nothing** (score 99.1% with and without). 12 cluttered sentences spoken by the Mac's synthetic voice: 9 came out clean; the misses were "scratch that" said without a pause (Whisper wrote no punctuation, so it is left alone) and "hmm" heard as the word "Hum". The recorder has 13 cleanup sentences (e01 to e13) for the user's voice. Not yet tested live by the user.
7. **Not included:** spoken punctuation ("comma", "new line"); offered to the user and not chosen.

## 7. Technical Approach

### 7.1 Stack
- **Language:** Python 3.12 (3.15 was tried and replaced because ML audio packages lag behind new Python releases), native arm64, virtual environment managed with `uv`.
- **Architecture rule:** `engine/` (audio, VAD, transcription, matching, actions, notes writing) imports nothing from `interface/` (menu bar, hotkeys, overlay, clipboard, WhatsApp control). An automated test enforces this, so a Swift interface can replace the Python one later.

### 7.2 Components

| Component | Choice | Notes |
|---|---|---|
| Speech-to-text | **mlx-whisper**, model `mlx-community/whisper-large-v3-turbo` (about 1.5 GB, stored in `models/whisper-large-v3-turbo-mlx`). Fallback: `whisper-small-mlx` (about 459 MB). | Runs on the Apple GPU. Loaded and run on **one dedicated worker thread**, warmed up at startup (about 1.5 s for turbo). Language "en"; `initial_prompt` empty by default; dictation and note chunks also get the personal word list hint (6.11). Weights are only ever loaded from `models/`: if the configured model is missing, the app uses `small` and logs a warning; if neither is present, transcription reports a clear error. Audio peaking below −60 dBFS is never sent to Whisper (see 11). |
| Voice activity detection | **Silero VAD** (PyTorch/JIT model) | 512-sample chunks at 16 kHz; speech threshold **0.35** (lowered from 0.5 to catch soft speech); requires 2 consecutive speech chunks. |
| Audio capture | `sounddevice` | 16 kHz mono; stream opens only inside a session. |
| Audio normalization | in-memory, before Whisper | Target peak -3 dBFS, max gain 20 dB. The saved WAV is untouched. Fixed poor accuracy on quiet speech. |
| Rule layer | anchored regex grammar | Instant; no model. |
| Menu bar and overlay | `rumps` + PyObjC (AppKit) | |
| Hotkeys | `pynput`, one listener | Listen-only (never suppresses keys). |
| Key events | Quartz `CGEventPost` on the main thread | Never pynput's Controller (TIS/TSM crash). |
| Notes control | `osascript` with arguments | |
| WhatsApp control | Accessibility API (`AXPress`, `AXCancel`) + Cmd+F / Cmd+V | |
| Tests | `pytest`, 901 tests | `conftest.py` safety net blocks real key events, Accessibility actions, `open`/`lsappinfo` subprocesses and the real clipboard, fails any test that writes into the real user data folder, and keeps tests away from the user's real word list. |

### 7.3 Architecture principles
- **Rules first, LLM never (for now):** the LLM fallback (original Step 4) was deliberately skipped; the rules cover every tested phrasing, and an LLM adds false-trigger risk.
- **Whole-utterance matching:** a sentence that merely contains "notes" or "WhatsApp" never triggers anything.
- **Low-risk actions run immediately; sending never happens.**
- **Swappable models** behind a `Transcriber` interface.
- **Offline at runtime:** `main.py` sets `HF_HUB_OFFLINE=1`, and the transcriber resolves only local weights, so the running app never downloads a model. Models are downloaded deliberately, with confirmation, by `scripts/benchmark_whisper.py`.
- **Never block the UI thread or the audio thread.**
- **Redact at the source, delete audio early.**

### 7.4 Resource budget (16 GB RAM)
Whisper `large-v3-turbo` peaks at about 2.2 GB of GPU memory (process RSS about 1.6 GB), against about 1.2 GB for `small`. That leaves ample headroom on a 16 GB Mac. The weights stay loaded while the app runs; the GPU does work only while audio is being transcribed. On the 20 local recordings, turbo averaged 0.55 s per recording (0.36 to 1.05 s), against 0.28 s for `small`. A lighter `small`-only setup remains the option for 8 GB Macs.

### 7.5 Permissions
macOS attaches these to the **app that launches Python** (Terminal, iTerm or Antigravity), not to Python itself:
- **Microphone**
- **Accessibility** (also needed for the WhatsApp controls)
- **Input Monitoring** (global hotkeys via pynput)
- **Automation** (controlling Notes; prompted the first time a note is saved)

The app checks the first three at startup and prints the exact System Settings path for any missing one.

### 7.6 Project layout
```
voice-assistant/
  main.py                      entry point (startup is a function, so tests can run it)
  config.toml                  all tunable values, every key commented
  engine/                      audio_capture, vad, session, transcriber, audio_utils, commands,
                               account, actions, apps, stop_phrases, chunk_joiner, cleanup, insertion, notes_writer, vocabulary,
                               config, models
  interface/                   app, menu_icon, waveform, hotkeys, key_events, permissions, overlay, clipboard,
                               setup_flow, welcome_window, login_item, account_form, account_manager, keychain,
                               whatsapp_ax, whatsapp_control
  assets/                      utter-app-icon.png (drawn by scripts/make_app_icon.py)
  packaging/                   launcher.c (Utter.app's main executable; compiled by scripts/build_app.py)
  dist.noindex/                Utter.app after a build (gitignored; hidden from Spotlight)
  scripts/                     accuracy (+ accuracy_prompts.csv), benchmark_whisper, build_app, eval_rules, make_app_icon, test_notes_write,
                               whatsapp_probe / whatsapp_experiment / whatsapp_layout_probe / whatsapp_geometry
  tests/                       901 tests; tests/data/eval_rules.csv (rule evaluation set)
  recordings/  note_backups/   output of builds before v0.3.2 (gitignored, left in place)
  models/                      empty after v0.3.11 (models moved to the data folder; still searched)

~/Library/Application Support/Utter/     (was .../VoiceAssistant/, renamed on first start of v0.3.11)
  config.toml                  your settings (copied once from the shipped config.toml)
  setup.json                   the welcome was shown; start-at-login choice
  permissions_asked.json       which permissions macOS was already asked for
  models/                      Whisper weights (large-v3-turbo, small)
  recordings/                  only when keep_recordings = true
  note_backups/                failed-note text backups (never pruned)
  vocabulary.txt               personal word list (created from the menu)
  accuracy/, accuracy-macmic/  accuracy recordings and reports

~/Library/Logs/Utter/utter.log           log file (1 MB, 3 older files kept)
```

## 8. Configuration reference (`config.toml`)

Utter reads `~/Library/Application Support/Utter/config.toml` (copied once from the shipped file; see 6.14). The defaults below are the shipped values.

| Section | Key settings (defaults) |
|---|---|
| hotkeys / timeouts | command `<ctrl>+<alt>+m`; dictation `<ctrl>+<alt>+d`; cancel `<esc>`; `hold_to_talk = true`, `hold_min_sec = 0.4`, `hold_release_tail_sec = 0.3`; command silence 1.5 s; command early check 0.6 s (0 turns early finish off); command no-speech 5.0 s; dictation silence 8.0 s |
| `[vad]` | `speech_threshold = 0.35`, `min_speech_chunks = 2` |
| `[normalization]` | `enabled = true`, `target_peak_dbfs = -3.0`, `max_gain_db = 20.0` |
| `[whisper]` | `model_size = "large-v3-turbo"` (falls back to `"small"` if not downloaded), `language = "en"`, `initial_prompt = ""`, `models_dir = "~/Library/Application Support/Utter/models"` |
| `[dictation]` | `chunk_pause_sec = 0.8`, `min_speech_ms = 250`, `stop_phrases = ["stop dictation", "end dictation"]`, `fuzzy_tolerance = 0.2`, `context_prompt = true`, `clipboard_restore_delay_ms = 400`, `modifier_release_timeout_ms = 500`, `trailing_space = false` |
| `[notes]` | `stop_phrases = ["stop note", "stop dictation", "end dictation"]`, `silence_sec = 10.0`, `title_template = "Voice note {date} {time}"`, `folder = ""`, `reveal_after_save = false`, optional ready sound |
| `[storage]` | `recordings_dir = "~/Library/Application Support/Utter/recordings"`, `max_recordings_kept = 20`, `note_backups_dir = "~/Library/Application Support/Utter/note_backups"` |
| `[privacy]` | `redact_names_in_logs = true`, `keep_whatsapp_audio = false`, `keep_audio_when_whatsapp_front = false`, `keep_recordings = false` |
| `[vocabulary]` | `enabled = true`, `file = "~/Library/Application Support/Utter/vocabulary.txt"`, `max_prompt_terms = 50`, `auto_correct = true` |
| `[apps]` | `enabled = true`; `[apps.aliases]` spoken name = app name |
| `[cleanup]` | `enabled = true`, `fillers = true`, `repeats = true`, `corrections = true` |

## 9. Measured behavior (from manual testing)

| Measure | Observed |
|---|---|
| Whisper warm-up at startup | `large-v3-turbo`: about 1.5 s. `small`: about 0.4 to 0.7 s |
| Transcription latency, `large-v3-turbo` | about 0.36 to 0.45 s for 3 to 8 s of audio; about 1.0 s for 36 s; average 0.55 s over 20 recordings (benchmark, 6 Nov 2026) |
| Transcription latency, `small` | about 0.13 to 0.24 s for 4 to 6 s of audio (one slower first call of 0.79 s); average 0.28 s over the same 20 recordings |
| Peak GPU memory | `large-v3-turbo` about 2.2 GB; `small` about 1.2 GB |
| Launching Notes (`open -a`) | about 0.06 to 0.09 s |
| Command silence wait | about 1.50 s; about 1.0 s for fixed commands with early finish (0.6 s check plus about 0.4 s transcription, overlapped) |
| **End of speech to Notes launched** | **about 1.8 to 1.9 s** before early finish (measured with `small`). With early finish and turbo, estimated **about 1.0 s**: turbo takes 0.37 to 0.39 s for "open notes" / "start taking notes" plus 0.6 s of silence (synthesized speech, 6 Nov 2026). Not yet measured live with the user's voice |
| Model comparison on the same recordings | Turbo fixed mishearings that `small` made: "Open nodes" → "Open notes", "route section" → "About section", "There is a manual to make" → "There is no need to make". No word error rate yet, because the recordings have no reference transcripts |
| Input levels | peaks about -13 to -26 dBFS; normalization often at the 20 dB cap |
| Rule evaluation set | 100% accuracy on the project's own evaluation CSV (written by the agent; real-voice behavior is the true test) |
| Personal word list (synthesized speech, `large-v3-turbo`) | Without the list: "Sanavi", "Ishan", "anti-gravity", "Samak Chaya", "Superbase", "Whisper". With it, all six spelled as listed; an ordinary sentence was unchanged; no extra latency (about 0.5 s either way); the hint was not echoed on noise. Not yet tested live with the user's voice |
| Word-list false fixes | 0 ordinary words changed across about 95,000 words of English prose (man pages, this PRD, the command evaluation set) with a 49-name test list |
| Automated tests | 901 passing |

No formal 30 to 50 phrase benchmark was run (the user chose to test by hand). The numeric success criteria from the v0.1 draft (95% accuracy, zero false triggers, under 1.5 s) are therefore not formally measured.

## 10. Test plan and practices

- **Automated:** grammar tests (positives and negatives for every command), a no-overlap test over the whole evaluation CSV, stop-phrase tests, chunk-joiner tests, clipboard tests, note-writer tests (HTML escaping, injection, error mapping, Hindi), WhatsApp guard and never-send tests, privacy tests (no names in output, no forbidden Accessibility attributes, no Return keycode), architecture test, startup smoke test.
- **Safety net:** `conftest.py` blocks any test from posting real key events, performing real Accessibility actions, launching apps, touching the real clipboard, writing into the real data folder or reading the real word list.
- **Manual acceptance checks** are written per step and run by the user on the real Mac, because the coding agent's shell usually lacks Microphone, Accessibility and Input Monitoring permission and cannot see the user's apps.
- **Working agreement with the coding agent:** plan first and wait for approval; never report a pass for anything it could not actually run; never drive or inspect the user's WhatsApp from its own shell.

### 10.1 Accuracy score on the user's own voice (`scripts/accuracy.py`)
- **Record** (`uv run python scripts/accuracy.py record`): shows one sentence at a time and saves the audio together with the exact sentence, so no transcripts are typed by hand. 78 fixed sentences in five groups (22 commands including 4 that must do nothing, 26 short and 14 long dictation sentences, 6 with numbers, 10 with tech words) plus one sentence per word-list term (up to 12). v0.3.8 adds 10 app commands (c23 to c32: 8 apps that ship with macOS, plus "open the door" and "open Safari and search for flights", which must open nothing); app commands are scored by which installed app would open. v0.3.9 adds 13 cleanup sentences (e01 to e13), said with "um"s, repeats and corrections on purpose; each has the tidy text it should become, and they are scored on their own so they never lower the dictation score. About 20 minutes; skip, redo and quit at any point, and it resumes by sentence text. Takes that are under 0.6 s or quieter than −45 dBFS prompt a redo. The microphone opens only while recording and is always closed on quit or Ctrl+C.
- **Score** (`uv run python scripts/accuracy.py score`): runs every recording through the app's own steps (the configured local model, normalization and silence guard; the word-list hint, echo strip and correction for dictation; no word list and the command rules for commands). Reports % of words right (word-weighted) per group, how many commands would have done the right thing (right action, right name, or nothing), how many word-list terms were spelled exactly, and the same dictation scored without the word list and without cleanup (with how many normal sentences cleanup changed), plus how many cleanup sentences came out exactly right with and without cleanup. It also lists the worst mistakes. Offline only.
- **What is not an error:** both sentences are rewritten into one standard form before words are compared: punctuation and capitals dropped, contractions expanded ("I'm" = "I am", "can't" = "cannot"; "it's" = "it is" and "I'd" = "I would" after common pronouns only), joined and split spellings unified ("every day" = "everyday", "OK" = "okay"), hyphens read as spaces, number words as digits ("two" = "2", "twenty five" = "25"), and "p.m." = "pm". Real mishearings still count ("notes" / "nodes", "its" / "it's").
- **Storage:** recordings, the manifest and the reports go to `~/Library/Application Support/Utter/accuracy/`, never the project folder; deleting that folder removes everything.
- **Limits:** long sentences are scored whole, while the app splits dictation at pauses; ordinals ("15th" vs "fifteenth") and accents ("MADRÉ" vs "Madre") still count as errors; the score is for the microphone used while recording.
- **Checked on synthesized speech** (9 sentences, turbo): 98.1% of dictation words right; 3 of 4 commands right (the Mac voice's "WhatsApp" was heard as "Vharts app", a synthetic-voice quirk seen before). - **First real results (7 November 2026, turbo, the user's voice):**
  - *Dictation, CMF Buds 2 Plus earbuds:* 99.1% of words right (7 errors in 787 words; 96.9% before the spelling-only fix, when 17 of the 24 errors were "I'm" / "I am" and similar). Short 99.6%, long 99.7%, numbers 93.3%, tech words 100%, word-list sentences 97.0%.
  - *Word list:* 7 of 9 terms spelled exactly with the list, 1 of 9 without it.
  - *Commands, earbuds:* 19 of 22; the 4 do-nothing phrases all did nothing. *Commands, MacBook Air microphone:* **22 of 22**.
  - *Cause of the earbud misses:* the earbuds mute the signal to exact digital silence between sounds and cut the soft start of a word (4 of 22 command takes started mid-word; none with the built-in mic). "open notes" lost "open" and came out "Totes". Tested and rejected as fixes, on all 87 recordings plus 20 older ones: retrying with a hint of the command phrases (3 wordings), trimming silence; none changed the result. Adding the word list (with the names) as a hint for commands fixed the "Rahul" → "Lahun" miss with no false triggers, but is not built.

## 11. Lessons learned (bugs found and fixed)

| Problem | Cause | Fix |
|---|---|---|
| Session never finished; no WAV saved; icon stuck on "Listening" | `PortAudio` stop was called from inside the audio callback thread (self-deadlock) | Finalization runs on a worker thread. |
| App aborted at startup ("TIS/TSM called from two threads") | Two `pynput` listeners started while the menu bar app initialized | One single listener for all hotkeys; `NSApplication` initialized on the main thread first. |
| Startup crash `NameError: AudioCapture` | Missing import after a late edit; tests never ran the startup path | Added a startup smoke test. |
| "Nothing happens" after a code change | Old process still running | Always quit (Ctrl+C) and restart after changes. |
| Poor transcription of normal speech | Input too quiet, not model size | Audio normalization plus VAD threshold 0.35; raise macOS input volume. |
| "Open the Note App." rejected | Grammar only allowed "notes"/"nodes" | Singular note/node accepted; "open a note" rejected on purpose. |
| Python 3.15 dependency trouble | ML packages lag new Python | Rebuilt on Python 3.12 with `uv`. |
| WhatsApp search field "not found" | Accessibility tree went stale after a long WhatsApp session; sidebar collapsed in narrow windows | Restart WhatsApp; handle with an overlay message. |
| Experiment scripts could not bring WhatsApp forward | `NSWorkspace` reads stale in a script without a run loop | Use `open -b` plus an independent `lsappinfo` check; require both to agree before any key press. |
| Risk of a silent model download at runtime | If the configured model was not in `models/`, the transcriber passed a Hugging Face repo ID to mlx-whisper, which would fetch weights from the network | Resolve local weights only; fall back to `small`; `HF_HUB_OFFLINE=1` in `main.py`; tests prove a repo ID is never returned. |
| Turbo wrote "Thank you." for a silent clip | `large-v3-turbo` hallucinates on digital silence, and its `no_speech_prob` is always 0, so Whisper's own check cannot catch it | Audio peaking below −60 dBFS is skipped before Whisper (real speech here peaks at −13 to −26 dBFS). |
| A test wrote a fake note backup into the real `~/Library/Application Support/` folder | A test fixture relied on the new default backup location | Fixture points at a temporary folder; `conftest.py` now fails any test that writes into the real data folder. |
| Accessibility stayed "missing" although switched on for Utter (18 November 2026) | The switch belonged to an older, ad-hoc-signed build; macOS does not count it for builds signed with the certificate | The user ran `tccutil reset Accessibility com.utter.mac` and turned it on again; the welcome window now shows this fix when a row stays off (6.16). |
| Early word-list correction changed "certs" → "Curtis", "Korn" → "Karan" and "github" inside a URL (found by a prose sweep before release) | Vowel-slip matching was too loose, and URLs were treated as normal words | Vowel slips need the first three letters to match and lengths within one; words inside URLs, emails and identifiers are skipped. |

## 12. Known limitations and tuning notes

1. **Names:** Whisper mishears some names and unusual phrases (`small` misheard "Rahul"; turbo still turned one phrase into "Professional Hair Short Meal"). The personal word list (6.11) fixes listed names in dictation and notes; it is deliberately not used for commands, so WhatsApp search names are still as heard. The design avoids name matching: the user sees WhatsApp's own search results and can retry.
2. **WhatsApp row selection:** pressing "the Nth chat" worked once, but row detection was unreliable (the structure found 3 rows with uneven spacing), and keyboard focus after opening a chat is not in the message box. **Not built** (see Parked).
3. **After opening a chat** the user must click the message box once before dictating.
4. **Command latency:** the 1.5 s command silence wait was the dominant delay. Early finish (6.2) removes most of it for fixed commands. WhatsApp search still waits 1.5 s on purpose; lowering `command_silence_sec` would speed it up but risks cutting off a name, so test any change live.
5. **WhatsApp accessibility** depends on WhatsApp's internal UI structure and may break after an update.
6. **Hotkeys:** matched by physical key position on a US (QWERTY) layout, so on other layouts the letter may sit elsewhere. A hold shorter than 0.4 s counts as a tap, so a very short held phrase keeps dictating until the next press. Hold-to-talk needs Accessibility permission (otherwise tap-to-toggle only). Not yet tested live with real key presses.
7. **VoiceOver** also uses Control + Option; hotkeys are configurable in `config.toml`.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A stray Return sends a WhatsApp message | The module cannot post Return at all; key whitelist enforced by tests; the user presses Enter. |
| Keys land in the wrong app | Dual frontmost check (`NSWorkspace` and `lsappinfo`) before every action and every key event; refuse if they disagree. |
| Whisper mishears a command | Whole-utterance rules, known variants accepted, ambiguous speech does nothing. |
| Early finish acts on half a sentence | Only fixed commands with no name; refused if the user speaks again before the transcript is ready (pause id check under the session lock); WhatsApp search and unrecognized speech always wait the full timeout; `command_early_check_sec = 0` turns it off. |
| Hold-to-talk sticks or eats keys | Only the app's own two hotkeys are swallowed (exact modifiers), Esc never; the intercept never raises and passes events on any error; a watchdog releases on missed key-ups; releases stop only the session that press started; `hold_to_talk = false` restores the old listener. |
| Stop phrase appears in normal speech | End-of-transcript only, fuzzy but strict on short words, distinctive defaults, stripped from output. |
| Clipboard overwritten | Full snapshot, restore only if unchanged, transient flags. |
| Dictation lost on a failed note save | Clipboard copy plus a permanent backup `.txt`. |
| Personal data in the project folder | No audio written by default; data folders moved to `~/Library/Application Support/Utter/`; redaction; no contact files. Older files in the project folder are left for the user to delete. |
| WhatsApp UI changes | Structure-only reads, clear failure messages, parked row selection. |
| Word list rewrites ordinary words or invents text | Strict rules (6.11): dictionary words, URLs and contractions never changed; short terms exact-only; hint capped in size; echoed hints removed; tests over ordinary sentences; `auto_correct = false` as an off switch. |
| Python limits native polish and distribution | Strict engine/interface separation allows a Swift shell later. |

## 14. Parked ideas (designed or discussed, not built)

1. **"Open the Nth WhatsApp chat":** needs a more reliable row-detection test.
2. **LLM fallback** for flexible phrasing (original Step 4), benchmarked against the rules, off by default.
3. **Native Swift interface** (packaging, settings and onboarding are Milestone 3).
4. **Read-back of notes, sending a message by voice** (would need a confirmation overlay with Enter/Esc, and is high risk).
5. **Model options** for lower-memory Macs, optional cloud models. (Rule-based dictation cleanup is built, 6.13.)
6. **Choosing between ambiguous apps by voice** ("the first one") instead of saying the full name.
7. **Welcome window redesign** (the user's request, 19 November 2026): the steps and logic stay (`setup_flow.py`, `account_form.py`); only the drawing in `welcome_window.py` changes.
8. **Sign in with Apple / Google** as quicker alternatives to email and password.

## 15. Next steps: three milestones

Agreed on 14 November 2026. Each milestone is built on its own branch, measured where possible, and tested by hand by the user before the next one starts.

### Milestone 1: More commands (open any app by voice) — done (v0.3.8, tested live by the user on 14 November 2026)
- "Open Safari", "launch Calculator", "switch to Finder": any app installed on the Mac, with no list to maintain (6.12).
- Safe by design: unknown names and names that could mean two apps do nothing and say why; Notes and WhatsApp commands keep priority; apps launch only from a path found on disk.
- Accuracy sentences for app commands added to the recorder, so the result can be scored on the user's voice.
- Optional, small: WhatsApp search names from the word list (decision pending, see 12.1).

### Milestone 2: Nicer dictation (cleanup) — done (v0.3.9, tested live by the user on 17 November 2026)
- Remove filler words ("um", "uh"), accidental repeats ("the the") and simple self-corrections, with rules only (offline, no LLM) (6.13).
- Measured before and after on the user's recorded sentences; nothing ships that lowers the score. Result: no change on the 65 normal dictation sentences (99.1% with and without); 10 of 13 cleanup sentences exactly right on the user's voice (8 without cleanup). The 3 misses were Whisper mishearing the correction words themselves ("no wait" as "9.80", "four chairs" as "Poachers", "Email" as "and mail"); with the user's real voice Whisper already drops most "um"s and repeats, so the correction rules do most of the work.

### Milestone 3: A real Mac app (v1.0) — in progress
Decisions (17 November 2026): the name is **Utter**; built for the user's own Mac first (a free personal signing certificate keeps permissions across rebuilds; Apple's paid developer account only if it is shared later); a **full** settings window.
- **Done in v0.3.10 (tested live by the user):** the name Utter; icon-only menu bar with ready / command orb / voice bars / dimmed states and no transcribing state; the app icon (6.1).
- **Stage 1 done in v0.3.11 (ran on the user's Mac: data folder renamed, settings copied, models moved):** data folder renamed to Utter, settings file and models in it, log file (6.14). The user chose to move (not copy) the models and to rename the data folder.
- **Stage 2 done in v0.4.0 (tested live by the user on 18 November 2026: commands, dictation, pop-up and permissions work):** Utter.app, self-contained, signed with a free personal certificate when present (6.15).
- **Stage 3 done in v0.5.0 (tested live by the user on 19 November 2026; its look will be redesigned later):** first-run welcome window with live permission ticks, a try-it step, and start at login, on by default (6.16).
- **Accounts built in v0.6.0 (brought forward from Milestone 4 at the user's request), waiting for the Supabase project:** required sign-in with email and password (6.17).
- Next stages: (4) the full settings window; (5) final checks (all tests, unchanged accuracy score, live test of the installed app). Running from the terminal keeps working throughout.
- Try running the voice detector without PyTorch to shrink the app; ships only if accuracy and tests are unchanged.

### Ongoing checks
- Re-measure end of speech to action live with `large-v3-turbo` (fixed commands, app launch, WhatsApp search).
- Use the MacBook microphone for commands; the CMF Buds cut the start of short words (10.1).
- Decide what to do with the older `recordings/` and `note_backups/` in the project folder.

## 16. Open questions

1. ~~Product name.~~ Utter (decided 17 November 2026).
2. Whether to enable optional debug logging by default (the terminal log proved essential for diagnosis, so logging currently stays on).
3. Final numeric success criteria, if formal measurement is done.
4. Which parked feature comes first.

## 17. Summary of decisions

- macOS only, local-first, menu bar app; microphone active only while a session runs.
- Two hotkeys (`Control + Option + M`, `Control + Option + D`); Esc cancels; one keyboard listener handles all three, matched by key code.
- Dictation hotkey: tap to toggle or hold to talk (0.4 s threshold, 0.3 s release tail); the app swallows its own hotkeys; falls back to tap-only without Accessibility.
- Fixed commands finish early once recognized during a 0.6 s pause; anything with a name waits the full 1.5 s.
- Python 3.12; MLX Whisper `large-v3-turbo` with a local `small` fallback, offline at runtime; Silero VAD (threshold 0.35); audio normalization; no LLM.
- Commands: open Notes, take a note, open WhatsApp, search WhatsApp, open any app. Whole-utterance rules; "open a note" deliberately not a command.
- Dictation: chunked background transcription, one paste at the end, stop phrases at the end of speech only, clipboard save and restore.
- Notes saved through AppleScript with arguments; failures never lose text.
- WhatsApp: no contacts file; dual frontmost guard; only Cmd+F and Cmd+V; the user sends messages manually.
- Privacy by design: no session audio written to disk by default, personal data in `~/Library/Application Support/Utter/`, names redacted at the source, audio deleted early for WhatsApp, fake names in all tests.
- Personal word list for dictation and notes: hinted to Whisper, strict post-correction, stored with personal data, never used for commands.
- Open any installed app by voice (Milestone 1): strict matching levels, refusal on unknown or ambiguous names, launch by path only, Notes and WhatsApp keep priority.
- Dictation cleanup (Milestone 2): rules only, dictation and notes only, narrow rules that leave the text as spoken when unsure; each rule can be switched off.
- WhatsApp Nth-chat and the LLM fallback are parked.
