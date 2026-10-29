# PRD: Voice-Assisted Dictation & Command App for macOS

**Working title:** TBD
**Status:** Draft v0.1 (pre-development)
**Platform:** macOS (Apple Silicon)
**Primary developer machine:** MacBook with M5 chip, 16 GB RAM, 512 GB storage
**Build approach:** Code written with AI coding assistants (Claude Code / Codex), guided by this document

---

## 1. Overview

A menu bar app for macOS that lets the user **dictate text** and **control the laptop by voice**. It is inspired by Wispr Flow, which focuses on dictation only. This product adds the missing half: spoken **commands that perform actions** (open apps, take notes, send messages).

The app listens **only while it is running**. Quitting the app stops the microphone completely.

Everything runs **locally** in v1: no cloud services, no cost, and no audio or transcripts leaving the machine.

## 2. Problem Statement

Existing voice dictation tools convert speech to text but do not act on instructions. A user who wants to say "open Notes, take this down, and send it to Rahul on WhatsApp" has to do each step manually. There is no simple, private, local tool that combines dictation and voice-driven laptop control.

## 3. Goals and Non-Goals

### Goals (v1)
- Reliable voice dictation into any focused text field.
- One working voice command, **"open Notes"**, to validate the full pipeline end to end.
- Fully local operation (speech-to-text and command interpretation).
- A clean separation between engine and interface so the interface can later be rebuilt natively.
- A measurable testing process so model choices are based on data.

### Non-Goals (v1)
- Note-taking commands, WhatsApp sending, contacts, or any command beyond "open Notes".
- A settings screen (values are configured in a config file for now).
- Cloud models, multi-user accounts, or distribution to other users.
- Windows or Linux support.
- Open-ended "do anything on my laptop" automation.

## 4. Target Users

- **Phase 1:** the developer (self-testing).
- **Phase 2 (later):** other Mac users who want private, local voice dictation plus voice commands.

## 5. Core Concept: Two Modes, Two Hotkeys

The hotkey tells the app the user's intent, so the app never has to guess whether speech is dictation or a command.

| Mode | Hotkey (default) | Purpose |
|---|---|---|
| Command mode | `Control + Option + M` | Speak a command; the app interprets and executes it. |
| Dictation mode | `Control + Option + D` | Speak freely; text is inserted into the focused field. |

Hotkeys should be changeable later via settings (the config file in v1).

## 6. Functional Requirements

### 6.1 App Shell
- Runs as a **menu bar app** with Start / Stop controls.
- Microphone is active **only** while a mode is listening, and the app itself must be running.
- A clear visual indicator (menu bar icon state and/or small overlay) shows when the mic is listening.
- **Esc** cancels the current listening session at any time.

### 6.2 Command Mode (`Control + Option + M`)
1. User presses the hotkey; the app starts listening.
2. Recording **auto-stops after about 1.5 seconds of silence**, detected by VAD.
3. If no speech is detected within about 5 seconds, the session cancels quietly (handles accidental presses).
4. Audio is transcribed by Whisper.
5. The transcript is passed to the **rule layer**; if no rule matches, it goes to the **local LLM fallback**.
6. A small overlay shows what was heard and what action was taken (for example: *Heard: "open notes" → Opening Notes*).
7. **Low-risk actions** (such as opening an app) run immediately without confirmation.
8. **Risky actions** (anything that sends something to another person; future versions) require explicit confirmation (Enter to confirm, Esc to cancel) after the overlay shows the exact transcript, the interpreted action, and any resolved contact name and number.

**v1 command list**

| Command | Example phrasings | Action |
|---|---|---|
| Open Notes | "open notes", "open the notes app", "launch notes" | Opens the macOS Notes app |

Unrecognized speech or non-commands (for example, "I took some notes yesterday") must result in **no action**, with the overlay indicating nothing was recognized.

### 6.3 Dictation Mode (`Control + Option + D`)
1. User presses the hotkey; the app starts a **continuous** listening session.
2. Speech is transcribed by Whisper; the LLM is **not** used.
3. Text is inserted into the currently focused text field via **clipboard paste with save and restore** (the app saves the current clipboard, pastes the dictated text, then restores the original clipboard).
4. The session ends on any of the following:
   - A **custom stop phrase** spoken at the end of the transcript.
   - Pressing the dictation hotkey again.
   - A long silence safety net (about **8 to 10 seconds**).
   - Esc (cancel).
5. The stop phrase is **removed** from the final inserted text.
6. Optional cleanup (removing filler words, punctuation fixes) can be added later and is not part of v1.

### 6.4 Stop Phrases (Dictation Mode)
- The user can define **several** stop phrases (for example: "stop dictation", "end note", "I'm done").
- Matching is **loose**: normalize the text (lowercase, strip punctuation) and allow small fuzzy-match tolerance to handle transcription variations and accents.
- A phrase counts as a stop signal **only when it appears at the end of the transcript** (ideally after a short pause), so sentences like "tell him I'm done with the report" do not end the session.
- The app should warn when a user picks a very common phrase (for example "okay", "yes").
- Stop phrases apply to dictation mode only; command mode ends on silence.

### 6.5 Read-Back and Transparency
- **Dictation:** text appears in the target field, so the user can verify it directly.
- **Command:** the overlay always shows the exact transcript and the interpreted action, so the user can see whether the app understood correctly. Names are never guessed silently.

## 7. Technical Approach

### 7.1 Stack
- **Language:** Python for v1 (fastest to build and test; strongest ecosystem for Whisper, VAD, and local LLM tooling).
- **Architecture rule:** keep the **engine** (audio capture, VAD, transcription, command matching, actions) fully separate from the **interface** (menu bar, hotkeys, overlay). This allows a native Swift interface to replace the Python one later without rewriting the logic.

### 7.2 Components

| Component | Choice | Notes |
|---|---|---|
| Speech-to-text | **Whisper** (open source) via whisper.cpp or an MLX-based port | Start with a mid-sized model; tune size by measuring accuracy and speed. |
| Voice activity detection | **Silero VAD** | More robust to noise and keyboard clicks than a loudness threshold. |
| Command interpretation (fallback) | Small local LLM (about 3B to 8B parameters, quantized) via Ollama or MLX | Used only when rules do not match. Constrained JSON output limited to a fixed action list. |
| Rule layer | Normalized text matching for known commands | Instant, no model needed; handles the v1 command. |
| App control | AppleScript / `open` command; Accessibility automation later | Opening Notes in v1. |
| Text insertion | Clipboard paste with save and restore | Fast and reliable. |
| Hotkeys and menu bar | Python macOS libraries (for example `rumps` and a global hotkey library) | To be replaced by a Swift shell later if needed. |

### 7.3 Architecture Principles
- **Swappable models:** Whisper and the LLM sit behind simple interfaces, so changing size or model does not affect the rest of the app.
- **Rules first, LLM second:** common commands never wait on the LLM.
- **Dictation bypasses the LLM** entirely.
- **Fixed action list:** the LLM can only return a valid action from a defined list, never free-form instructions.

### 7.4 Resource Budget (16 GB RAM)
- Whisper and a quantized LLM of 8B parameters or below should fit comfortably alongside normal apps.
- Model choices for other users' machines (for example 8 GB Macs) will need smaller options; this is a later concern.

### 7.5 Permissions
macOS will request, on first run:
- **Microphone**
- **Accessibility** (global hotkeys, text insertion, UI automation)
- **Automation** (controlling other apps such as Notes)

These are handled as a one-time first-run setup.

## 8. Test Plan (v1)

Model selection is driven by measurement, not reputation.

### 8.1 Test Set
Record **30 to 50 phrases** before choosing models, including:
- Valid variations: "open notes", "open the notes app", "can you open notes", "launch notes".
- Likely mishearings: "open nodes".
- **Negative cases** that must not trigger: "I took some notes yesterday", random conversation.
- Different conditions: quiet room, background noise, fast speech, soft speech.
- Names (for later contact matching): for example "Rahul", "Priya".

### 8.2 What to Measure
- **Rule layer accuracy:** how often the Whisper transcript matches the rules correctly.
- **LLM layer accuracy:** given the same transcripts, whether the small model maps to "open Notes" correctly and refuses non-commands.
- **Latency:** time from end of speech to the Notes app opening.
- **Silence auto-stop behavior:** whether the 1.5 s timeout cuts the user off or waits too long.
- **Dictation quality:** transcription accuracy, clipboard restore reliability, stop-phrase detection (including false triggers).

### 8.3 Success Criteria (proposed, to be finalized)
- Command accuracy on the test set: **target to be agreed** (suggested starting point: 95% or higher on valid phrases).
- False triggers on negative cases: **target to be agreed** (suggested starting point: zero).
- End-to-end latency for "open Notes": **target to be agreed** (suggested starting point: under about 1.5 seconds after speech ends).
- Stop phrase false-trigger rate in normal dictation: **target to be agreed**.

## 9. Build Order

1. Hotkey handling and mic capture with VAD-based silence auto-stop.
2. Whisper transcription, with the transcript displayed on screen.
3. Rule matching and the "open Notes" action, plus the overlay.
4. Local LLM fallback, benchmarked on the test set.
5. Dictation mode with clipboard insertion and stop phrases.
6. Config handling for hotkeys, timeouts, and stop phrases (settings screen later).

## 10. Roadmap (Post-v1)

- Note-taking commands ("start taking notes", create and read back notes).
- WhatsApp sending via the desktop app's link scheme (`whatsapp://send?phone=...&text=...`), plus a **contacts list** (name to number) with spoken-name matching and a confirmation step before sending.
- Settings screen (hotkeys, silence timeouts, stop phrases, contacts).
- Optional dictation cleanup (filler word removal, punctuation).
- Native Swift interface (menu bar, hotkeys, overlay) with the Python engine behind it.
- Packaging, code signing, and permission onboarding for other users.
- Model options for lower-memory Macs; optional cloud model support.

## 11. Risks and Considerations

| Risk | Mitigation |
|---|---|
| Whisper mishears names and short commands | Rule layer with variant phrasing; contact matching against a known list; confirmation overlay for sends. |
| Users pause mid-thought in dictation | Dictation uses long silence and stop phrases instead of a short timeout. |
| Stop phrase appears in normal speech | Distinctive default phrases; match only at transcript end; strip from output; warn on common phrases. |
| Clipboard paste overwrites user's clipboard | Save and restore around every paste. |
| LLM returns an invalid or unintended action | Constrained JSON output, fixed action list, rules-first design. |
| WhatsApp has no official personal-account API | Use desktop link scheme and UI automation; treat as fragile and test often. |
| Python limits native polish and distribution | Keep engine/interface separation so a Swift shell can replace the interface. |
| Memory use of two local models | Keep LLM at 8B or below; make models swappable. |
| VoiceOver also uses Control + Option | Make hotkeys configurable. |

## 12. Open Questions

1. **Debug logging:** the current decision is no logging, since the on-screen text shows what was heard. Consider an optional debug switch (off by default) that saves transcript, matched action, and timing to a file for benchmarking, since latency and accuracy across 30 to 50 test phrases are hard to judge by eye. To be decided.
2. **Final success-criteria numbers** (Section 8.3).
3. **Product name.**
4. **Specific Whisper size and LLM model**, to be chosen after benchmarking.

## 13. Summary of Decisions

- macOS only, local-first, menu bar app; mic active only while the app runs.
- Two hotkeys: `Control + Option + M` (commands) and `Control + Option + D` (dictation).
- Command mode stops on about 1.5 s of silence; dictation is continuous and ends on a custom stop phrase, hotkey, or long silence.
- Stop phrases are customizable, multiple, loosely matched, and only count at the end of the transcript.
- Read-back overlay for commands; confirmation required for anything sent to another person.
- v1 supports one command only: **"open Notes"**.
- Whisper (open source) for speech-to-text, Silero VAD, and a small local LLM as a fallback behind a rules-first layer.
- Python first, with a strict engine/interface separation.
- Clipboard paste with save and restore for dictated text.
- Notes, WhatsApp, contacts, settings screen, and packaging are deferred.
