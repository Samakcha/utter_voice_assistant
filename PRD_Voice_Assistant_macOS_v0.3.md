# PRD: Voice-Assisted Dictation & Command App for macOS

**Working title:** TBD
**Status:** v0.3.3 (implemented and manually tested; replaces the v0.1 pre-development draft). v0.3.1 switched speech-to-text to Whisper `large-v3-turbo` with a local-only fallback. v0.3.2 stops saving session audio by default and moves personal data out of the project folder. v0.3.3 adds a personal word list for names and terms (section 6.11).
**Last updated:** 6 November 2026
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
- Cloud models, multi-user accounts, packaging or distribution.
- Windows or Linux support.
- Open-ended "do anything on my laptop" automation.

## 4. Target Users

- **Phase 1 (now):** the developer, testing on their own Mac.
- **Phase 2 (later):** other Mac users who want private, local dictation and voice commands.

## 5. Core Concept: Two Hotkeys, Four Voice Commands

The hotkey tells the app the user's intent, so it never guesses whether speech is dictation or a command.

| Mode | Hotkey | Behavior |
|---|---|---|
| Command mode | `Control + Option + M` | Press once, speak a command, it ends on silence. |
| Dictation mode | `Control + Option + D` | Press once to start, continuous session, text is pasted once at the end. |
| Cancel | `Esc` | Cancels any active session; nothing is pasted or saved. |

All hotkeys are handled by **one single keyboard listener** (see section 11 for why).

**Voice commands (command mode):**

| Command id | Example phrases | Action |
|---|---|---|
| `open_notes` | "open notes", "open the notes app", "can you open notes", "launch notes", "open the note app" | Opens the macOS Notes app. |
| `take_note` | "start taking notes", "take a note", "new note", "create a note", "make a note" | Starts a note session that saves one new note. |
| `open_whatsapp` | "open WhatsApp", "launch whatsapp", "bring up whatsapp" | Launches and brings WhatsApp forward. |
| `whatsapp_search` | "search for `<name>`", "find `<name>`", "search whatsapp for `<name>`" | Types the name into WhatsApp's own search box (WhatsApp must already be in front). |

## 6. Functional Requirements

### 6.1 App shell
- Menu bar app with Start / Stop / Quit, a "Check Permissions" item, "Edit Word List…" (see 6.11), and "Open Recordings Folder" (the folder only fills when `keep_recordings = true`).
- The microphone is open only during an active session; the menu bar title shows the state (idle, listening, transcribing, loading model).
- A floating **overlay** shows the state and the result of every command (see 6.7).
- Sessions are finalized on a worker thread (never on the audio callback thread; see lessons learned).

### 6.2 Command mode (`Control + Option + M`)
1. Press the hotkey; the app starts listening.
2. Silero VAD detects speech. Recording **auto-stops after trailing silence** (default 1.5 s; see tuning note in 12.4). If no speech starts within 5 s, the session cancels quietly.
3. The audio is transcribed by Whisper on a dedicated worker thread.
4. The transcript goes through the **rule layer** (whole-utterance grammar). No LLM is used.
5. A matching command runs immediately (all current commands are low risk). No match means no action, and the overlay says "Nothing recognized".
6. The overlay shows what was heard and what happened; the terminal log prints a latency breakdown.

### 6.3 Rule layer (`engine/commands.py`)
The whole normalized utterance must match a command's grammar: lowercase, punctuation stripped, whitespace collapsed, then a fully anchored pattern.

- **Verbs for opening:** `open`, `open up`, `launch`, `bring up`. (`show` and `start` are deliberately excluded: "show notes" is common speech.)
- **Politeness wrappers accepted:** "can you", "could you", "would you", "will you", "please", "just", "kindly", "hey", "hi", "hello", "go ahead and", and suffixes "please", "for me", "thanks", "thank you".
- **Whisper variants accepted:** `note`, `notes`, `node`, `nodes` (Whisper really produced "nodes" and the singular "note").
- **Deliberate rejections:** "open a note" (means a specific note, not the app), "open the notes folder", "I took some notes yesterday", "don't open notes", "open notes and send it to someone", "take notes from the meeting" (extra words are rejected).
- **No overlap:** an automated test checks that no phrase in the evaluation CSV matches more than one command.
- **`whatsapp_search` name slot:** 1 to 4 words; letters in any script (including Devanagari), apostrophes and hyphens; single-letter middle initials allowed; digits and symbols rejected; names beginning with reserved words (the, a, an, my, notes, note, whatsapp, chat, app) rejected.

### 6.4 Dictation mode (`Control + Option + D`)
1. Press the hotkey to start a **continuous** session. Press it again to end.
2. **Chunked background transcription:** when the VAD sees a pause of at least `chunk_pause_sec` (0.8 s) after speech, the speech so far is cut (about 300 ms padding each side, silence trimmed, volume normalized) and transcribed in the background while recording continues. Chunks shorter than 250 ms of speech are skipped (they cause Whisper hallucinations).
3. A **chunk joiner** stitches the pieces (Whisper capitalizes and punctuates each chunk, so mid-sentence pauses are repaired).
4. Every chunk carries a **session id**; late results from cancelled or finished sessions are ignored.
5. **Nothing is pasted until the session ends.** Then the final text is pasted once (see 6.6).
6. The session ends on: a **stop phrase**, the dictation hotkey pressed again, **8 s of silence**, or Esc (cancel, paste nothing).
7. The LLM is never involved in dictation.
8. The **personal word list** (6.11) is hinted to Whisper for every chunk, and close mishearings of listed names are fixed in the final text before pasting.

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

### 6.7 Overlay
- A small floating, **non-activating** panel near the top center of the screen: it never takes keyboard focus, appears over full-screen apps and all Spaces, and fades out on its own.
- All AppKit work runs on the main thread only; each new state cancels any pending fade-out, so back-to-back commands are never hidden by an old timer.
- States include: Listening (Command / Dictation / Note), Transcribing, Heard "..." with the action taken, Nothing recognized, Inserted N words, Saved note, WhatsApp states and error messages. The macOS notification and the menu bar title remain as fallbacks.
- Esc hides the overlay immediately.

### 6.8 Voice notes (`take_note`)
1. In command mode say "start taking notes". The command session fully ends first (microphone closed), then a **note session** starts from the main thread.
2. The note session reuses the dictation machinery (chunking, stop phrases, chunk joiner, session ids, personal word list).
3. It ends on a stop phrase ("stop note"), either hotkey, the **10 s** silence timeout, or Esc (creates nothing).
4. At the end, **one new note** is created in Apple Notes with a title line (default `Voice note {date} {time}`) and the full text.
5. **Saving:** `osascript` (AppleScript) with the text passed as **arguments** (never spliced into script source, so quotes, backslashes and injection attempts are inert). The body is HTML-escaped (`&`, `<`, `>`), newlines become `<br>`, and Hindi/emoji text is preserved. Runs on a worker thread with a 15 s timeout. Notes is not brought to the front unless `reveal_after_save = true`.
6. **Never lose dictation:** if saving fails for any reason (including Automation permission denied, error -1743), the text is copied to the clipboard as normal text and written to a backup `.txt` in `~/Library/Application Support/VoiceAssistant/note_backups/` (not pruned), and the overlay explains why.
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
- **Personal data lives outside the project folder:** recordings (when kept) and failed-note backups go to `~/Library/Application Support/VoiceAssistant/`, not next to the code, where a coding agent could read them.
- `keep_whatsapp_audio = false`: when recordings are kept, the WAV of a WhatsApp command is still deleted right after transcription.
- `keep_audio_when_whatsapp_front = false`: when WhatsApp is in front, dictation and note audio are not kept even if `keep_recordings = true`, and the dictated text is not logged (word count only).
- No contacts file and no WhatsApp data folders are read. All tests use obviously fake names ("Alex Test", "Sam Example").
- **Older data left in place:** `recordings/` and `note_backups/` in the project folder, written by builds before v0.3.2, were not moved or deleted. The user decides what to keep (the recordings are useful for the benchmark in section 15).
- **Test safety net:** `conftest.py` fails any test that writes into `~/Library/Application Support/VoiceAssistant/`, and redirects any word-list path there to an empty test file, so tests never read the user's real names.
- **Word list:** stored with the other personal data (6.11); logs show only how many words were fixed unless `redact_names_in_logs = false`.

### 6.11 Personal word list
Fixes misheard names and unusual terms ("Sanavi" → "Saanvi", "Superbase" → "Supabase") without retraining anything.

1. **The list:** `~/Library/Application Support/VoiceAssistant/vocabulary.txt`, one name or term per line, spelled the way it should be written; `#` starts a comment. The menu item "Edit Word List…" creates the file with instructions (if missing) and opens it in the default text editor. Edits apply from the next dictation without a restart (the file is re-read when it changes).
2. **Scope:** dictation and notes only. Commands are never hinted or corrected, so the command rules stay predictable.
3. **Hint to Whisper:** each chunk's prompt is `[initial_prompt] Vocabulary: a, b, c. [previous chunk]`. At most 50 terms and about 400 characters, and the previous chunk is trimmed to about 300 characters, because mlx-whisper keeps only the last 223 prompt tokens and long hints can make Whisper invent words. With an empty list the prompts are exactly as before.
4. **Echo guard:** if Whisper repeats the hint back ("Vocabulary: …", or a transcript that is only three or more listed terms), it is removed from that chunk before stop-phrase detection, so an echo can never hide a trailing stop phrase.
5. **Strict post-correction** on the final text (`auto_correct = true`):
   - *Exact fixes* (same letters, different case or spacing): "prd" → "PRD", "anti gravity" / "anti-gravity" → "Antigravity", "git hub" → "GitHub".
   - *Close fixes* ("Rahool" → "Rahul", "Kavia" → "Kavya"): only for listed terms of 5+ letters, same first letter, at most 1 edit (2 for terms of 8+ letters), plus one extra edit for vowel-only slips when the first three letters match.
   - *Never changed:* ordinary English words (lowercase entries of the macOS system dictionary, common modern words and simple inflections), so listing "Will" never turns "will" into "Will"; words inside URLs, emails and file names; contractions; numbers. Terms of 4 letters or fewer ("Neha", "PRD") are only fixed by exact spelling.
   - Without the system dictionary (for example a future Windows build) only exact fixes run.
6. **Settings:** `auto_correct = false` keeps the hint but never rewrites text; `enabled = false` turns off both.

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
| Tests | `pytest`, 500 tests | `conftest.py` safety net blocks real key events, Accessibility actions, `open`/`lsappinfo` subprocesses and the real clipboard, fails any test that writes into the real user data folder, and keeps tests away from the user's real word list. |

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
                               actions, stop_phrases, chunk_joiner, insertion, notes_writer, vocabulary,
                               config, models
  interface/                   app, hotkeys, permissions, overlay, clipboard, whatsapp_ax, whatsapp_control
  scripts/                     benchmark_whisper, eval_rules, test_notes_write,
                               whatsapp_probe / whatsapp_experiment / whatsapp_layout_probe / whatsapp_geometry
  tests/                       500 tests; tests/data/eval_rules.csv (rule evaluation set)
  recordings/  note_backups/   output of builds before v0.3.2 (gitignored, left in place)

~/Library/Application Support/VoiceAssistant/
  recordings/                  only when keep_recordings = true
  note_backups/                failed-note text backups (never pruned)
  vocabulary.txt               personal word list (created from the menu)
```

## 8. Configuration reference (`config.toml`)

| Section | Key settings (defaults) |
|---|---|
| hotkeys / timeouts | command `<ctrl>+<alt>+m`; dictation `<ctrl>+<alt>+d`; cancel `<esc>`; command silence 1.5 s; command no-speech 5.0 s; dictation silence 8.0 s |
| `[vad]` | `speech_threshold = 0.35`, `min_speech_chunks = 2` |
| `[normalization]` | `enabled = true`, `target_peak_dbfs = -3.0`, `max_gain_db = 20.0` |
| `[whisper]` | `model_size = "large-v3-turbo"` (falls back to `"small"` if not downloaded), `language = "en"`, `initial_prompt = ""`, `models_dir = "./models"` |
| `[dictation]` | `chunk_pause_sec = 0.8`, `min_speech_ms = 250`, `stop_phrases = ["stop dictation", "end dictation"]`, `fuzzy_tolerance = 0.2`, `context_prompt = true`, `clipboard_restore_delay_ms = 400`, `modifier_release_timeout_ms = 500`, `trailing_space = false` |
| `[notes]` | `stop_phrases = ["stop note", "stop dictation", "end dictation"]`, `silence_sec = 10.0`, `title_template = "Voice note {date} {time}"`, `folder = ""`, `reveal_after_save = false`, optional ready sound |
| `[storage]` | `recordings_dir = "~/Library/Application Support/VoiceAssistant/recordings"`, `max_recordings_kept = 20`, `note_backups_dir = "~/Library/Application Support/VoiceAssistant/note_backups"` |
| `[privacy]` | `redact_names_in_logs = true`, `keep_whatsapp_audio = false`, `keep_audio_when_whatsapp_front = false`, `keep_recordings = false` |
| `[vocabulary]` | `enabled = true`, `file = "~/Library/Application Support/VoiceAssistant/vocabulary.txt"`, `max_prompt_terms = 50`, `auto_correct = true` |

## 9. Measured behavior (from manual testing)

| Measure | Observed |
|---|---|
| Whisper warm-up at startup | `large-v3-turbo`: about 1.5 s. `small`: about 0.4 to 0.7 s |
| Transcription latency, `large-v3-turbo` | about 0.36 to 0.45 s for 3 to 8 s of audio; about 1.0 s for 36 s; average 0.55 s over 20 recordings (benchmark, 6 Nov 2026) |
| Transcription latency, `small` | about 0.13 to 0.24 s for 4 to 6 s of audio (one slower first call of 0.79 s); average 0.28 s over the same 20 recordings |
| Peak GPU memory | `large-v3-turbo` about 2.2 GB; `small` about 1.2 GB |
| Launching Notes (`open -a`) | about 0.06 to 0.09 s |
| Command silence wait | about 1.50 s |
| **End of speech to Notes launched** | **about 1.8 to 1.9 s** with `small`, dominated by the silence wait. Not yet re-measured live with turbo; expected about 0.25 s longer |
| Model comparison on the same recordings | Turbo fixed mishearings that `small` made: "Open nodes" → "Open notes", "route section" → "About section", "There is a manual to make" → "There is no need to make". No word error rate yet, because the recordings have no reference transcripts |
| Input levels | peaks about -13 to -26 dBFS; normalization often at the 20 dB cap |
| Rule evaluation set | 100% accuracy on the project's own evaluation CSV (written by the agent; real-voice behavior is the true test) |
| Personal word list (synthesized speech, `large-v3-turbo`) | Without the list: "Sanavi", "Ishan", "anti-gravity", "Samak Chaya", "Superbase", "Whisper". With it, all six spelled as listed; an ordinary sentence was unchanged; no extra latency (about 0.5 s either way); the hint was not echoed on noise. Not yet tested live with the user's voice |
| Word-list false fixes | 0 ordinary words changed across about 95,000 words of English prose (man pages, this PRD, the command evaluation set) with a 49-name test list |
| Automated tests | 500 passing |

No formal 30 to 50 phrase benchmark was run (the user chose to test by hand). The numeric success criteria from the v0.1 draft (95% accuracy, zero false triggers, under 1.5 s) are therefore not formally measured.

## 10. Test plan and practices

- **Automated:** grammar tests (positives and negatives for every command), a no-overlap test over the whole evaluation CSV, stop-phrase tests, chunk-joiner tests, clipboard tests, note-writer tests (HTML escaping, injection, error mapping, Hindi), WhatsApp guard and never-send tests, privacy tests (no names in output, no forbidden Accessibility attributes, no Return keycode), architecture test, startup smoke test.
- **Safety net:** `conftest.py` blocks any test from posting real key events, performing real Accessibility actions, launching apps, touching the real clipboard, writing into the real data folder or reading the real word list.
- **Manual acceptance checks** are written per step and run by the user on the real Mac, because the coding agent's shell usually lacks Microphone, Accessibility and Input Monitoring permission and cannot see the user's apps.
- **Working agreement with the coding agent:** plan first and wait for approval; never report a pass for anything it could not actually run; never drive or inspect the user's WhatsApp from its own shell.

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
| Early word-list correction changed "certs" → "Curtis", "Korn" → "Karan" and "github" inside a URL (found by a prose sweep before release) | Vowel-slip matching was too loose, and URLs were treated as normal words | Vowel slips need the first three letters to match and lengths within one; words inside URLs, emails and identifiers are skipped. |

## 12. Known limitations and tuning notes

1. **Names:** Whisper mishears some names and unusual phrases (`small` misheard "Rahul"; turbo still turned one phrase into "Professional Hair Short Meal"). The personal word list (6.11) fixes listed names in dictation and notes; it is deliberately not used for commands, so WhatsApp search names are still as heard. The design avoids name matching: the user sees WhatsApp's own search results and can retry.
2. **WhatsApp row selection:** pressing "the Nth chat" worked once, but row detection was unreliable (the structure found 3 rows with uneven spacing), and keyboard focus after opening a chat is not in the message box. **Not built** (see Parked).
3. **After opening a chat** the user must click the message box once before dictating.
4. **Command latency:** the 1.5 s command silence wait is the dominant delay. Lowering `command_silence_sec` to about 1.0 to 1.2 is recommended; test that it does not cut off mid-sentence.
5. **WhatsApp accessibility** depends on WhatsApp's internal UI structure and may break after an update.
6. **Hotkey dependency:** dictation currently toggles (press to start, press to end); hold-to-talk is parked.
7. **VoiceOver** also uses Control + Option; hotkeys are configurable in `config.toml`.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A stray Return sends a WhatsApp message | The module cannot post Return at all; key whitelist enforced by tests; the user presses Enter. |
| Keys land in the wrong app | Dual frontmost check (`NSWorkspace` and `lsappinfo`) before every action and every key event; refuse if they disagree. |
| Whisper mishears a command | Whole-utterance rules, known variants accepted, ambiguous speech does nothing. |
| Stop phrase appears in normal speech | End-of-transcript only, fuzzy but strict on short words, distinctive defaults, stripped from output. |
| Clipboard overwritten | Full snapshot, restore only if unchanged, transient flags. |
| Dictation lost on a failed note save | Clipboard copy plus a permanent backup `.txt`. |
| Personal data in the project folder | No audio written by default; data folders moved to `~/Library/Application Support/VoiceAssistant/`; redaction; no contact files. Older files in the project folder are left for the user to delete. |
| WhatsApp UI changes | Structure-only reads, clear failure messages, parked row selection. |
| Word list rewrites ordinary words or invents text | Strict rules (6.11): dictionary words, URLs and contractions never changed; short terms exact-only; hint capped in size; echoed hints removed; tests over ordinary sentences; `auto_correct = false` as an off switch. |
| Python limits native polish and distribution | Strict engine/interface separation allows a Swift shell later. |

## 14. Parked ideas (designed or discussed, not built)

1. **Hold-to-talk dictation:** hold `Control + Option + D` to record, release to paste. Requires one listener with key-down and key-up tracking, matching by key code (Option changes what "D" types), ignoring auto-repeat, a state machine, release tail, minimum hold time, a watchdog for missed releases, and checking whether the held keys cause beeps. A full prompt exists.
2. **Open any installed app by voice:** an in-memory app inventory with matching levels (alias, exact, compact form such as "x code" for Xcode, unique partial name, strict fuzzy), refusal on ambiguity or unknown names, launching only from a resolved bundle path with an argument list. Notes and WhatsApp commands keep priority. A full prompt exists.
3. **"Open the Nth WhatsApp chat":** needs a more reliable row-detection test.
4. **LLM fallback** for flexible phrasing (original Step 4), benchmarked against the rules, off by default.
5. **Settings screen, native Swift interface, packaging, code signing, permission onboarding.**
6. **Read-back of notes, sending a message by voice** (would need a confirmation overlay with Enter/Esc, and is high risk).
7. **Dictation cleanup** (filler words, punctuation), model options for lower-memory Macs, optional cloud models.

## 15. Next steps

1. Tune the command silence timeout.
2. Choose the next feature from section 14.
3. Run a formal benchmark (the 30 to 50 phrase test set with `scripts/benchmark_whisper.py` and `scripts/eval_rules.py`) if numeric success criteria are wanted. First step: write reference transcripts for 20 to 30 existing recordings so the benchmark can report word error rate. New recordings need `keep_recordings = true` while collecting them.
4. Re-measure end of speech to action live with `large-v3-turbo`.
5. Fill the personal word list with real names and terms and test it live; note any word it changed that it should not have.
6. Decide whether WhatsApp search (command mode) should also use the word list for contact names.

## 16. Open questions

1. Product name.
2. Whether to enable optional debug logging by default (the terminal log proved essential for diagnosis, so logging currently stays on).
3. Final numeric success criteria, if formal measurement is done.
4. Which parked feature comes first.

## 17. Summary of decisions

- macOS only, local-first, menu bar app; microphone active only while a session runs.
- Two hotkeys (`Control + Option + M`, `Control + Option + D`); Esc cancels; one keyboard listener handles all three.
- Python 3.12; MLX Whisper `large-v3-turbo` with a local `small` fallback, offline at runtime; Silero VAD (threshold 0.35); audio normalization; no LLM.
- Commands: open Notes, take a note, open WhatsApp, search WhatsApp. Whole-utterance rules; "open a note" deliberately not a command.
- Dictation: chunked background transcription, one paste at the end, stop phrases at the end of speech only, clipboard save and restore.
- Notes saved through AppleScript with arguments; failures never lose text.
- WhatsApp: no contacts file; dual frontmost guard; only Cmd+F and Cmd+V; the user sends messages manually.
- Privacy by design: no session audio written to disk by default, personal data in `~/Library/Application Support/VoiceAssistant/`, names redacted at the source, audio deleted early for WhatsApp, fake names in all tests.
- Personal word list for dictation and notes: hinted to Whisper, strict post-correction, stored with personal data, never used for commands.
- Hold-to-talk, open-any-app, WhatsApp Nth-chat and the LLM fallback are parked.
