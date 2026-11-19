# Utter privacy policy

*Draft, last updated 19 November 2026.*

Utter is a free, open-source personal project. This page says what it keeps, where, and why.

## What stays on your Mac (never sent anywhere)

- **Everything you say.** Your voice is turned into text on your Mac, by the Whisper model
  running on the Mac's own GPU. Recordings are not kept unless you switch on
  Settings → Privacy → Keep recordings; then they stay in
  `~/Library/Application Support/Utter`.
- **Everything Utter types, and your notes.** Dictated text goes straight into the app you
  are typing in; notes go into Apple Notes.
- **Your settings, word list and app names**, in `~/Library/Application Support/Utter`.
- **Utter's log**, in `~/Library/Logs/Utter`, which helps when something goes wrong. Contact
  and search names are hidden in it by default.

Utter has no analytics, no advertising and no tracking. It never sends audio, text, the apps
you use or anything you type to its author or anyone else.

## What is stored online: your Utter account

To count how many people use Utter, it asks you to create an account. The account is kept by
[Supabase](https://supabase.com) (the service Utter uses for sign-in) and holds:

- your **email address**,
- your **password**, stored by Supabase in a form that can't be read back (a hash),
- **when** you signed up and signed in, and the technical details Supabase records with
  each sign-in (such as the IP address it came from).

Utter uses them only to let you sign in, to send you the 6-digit codes for confirming your
email and resetting your password, and to count users. They are never sold or shared, and
Utter sends you no other email.

On your Mac, Utter keeps your sign-in in the macOS Keychain, so you stay signed in. If Utter
isn't used for 7 days, it asks you to sign in again.

## Signing out and removing Utter

You can sign out at any time: menu bar icon → Sign Out, or Settings → General → Sign Out.
That removes your sign-in from this Mac; your account itself is kept. To remove everything
Utter keeps on your Mac, follow "Uninstall" in the [README](README.md).

## Changes

If this page changes, the new version will be in this repository, with its date at the top.
