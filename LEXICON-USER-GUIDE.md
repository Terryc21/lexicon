# lexicon user guide

lexicon is a Claude Code skill that turns what you read and work on into subject glossaries. You
point it at a source, it picks out the terms worth learning, and you choose which to keep. Each
saved term gets a **definition** that stands on its own, a short **example**, and a **use case**.
**Nothing is saved until you confirm it.**

Install steps and how the skill is built are in the [README](README.md). This guide covers using it.

## Starting

Type `/lexicon`, or just say what you want:

| You say | What happens |
|---|---|
| "Start a Swift vocab list from this article" (with a URL or file) | Sets up a list, then scans the article |
| "What terms from this session should I learn?" | Scans the current conversation |
| "Go through my last session" | Finds an older session and scans it |
| "Add 'actor reentrancy' to my Swift list" | Skips the scan and drafts just that term |
| "Show my Swift list" | Lists the terms and points you to the readable `.md` file |

## The first time: a short interview

The first time you start a list, lexicon asks four things: the **subject**, your **level**, the
**kinds of terms** to include, and what to **leave out**. It uses your answers to decide what is
worth proposing. An intermediate Swift list skips `var` and keeps `nonisolated(unsafe)`, and terms
you say you already know are never suggested again.

If lexicon finds your tutorial-creator vocabulary, it mentions that it can seed the new list from
it. It imports nothing unless you ask.

## A normal scan

1. lexicon tells you what it read: which session or pages, and how much.
2. It shows one screen of candidates, grouped as **New**, **Already in your lists**, **Possible
   duplicates**, and **Also in tutorial-creator**.
3. You answer `all`, `1,3,5` or `none`, and settle any possible duplicates (`merge`, `keep both`
   or `skip`).
4. It shows the drafted entries together. You accept, edit, or drop them.
5. It saves them, then reports the new count and where the readable `.md` file is.

## What you can point it at

- **Sessions:** the current one or an older one. Very long sessions are cut off at 400,000
  characters, and lexicon says so.
- **Files and folders:** for a folder, it states how many files match before it reads them.
- **PDF, Word and other documents:** PDFs are read a range of pages at a time, and lexicon tells
  you which pages it covered.
- **A web page:** pages behind a paywall or a login, or built entirely with JavaScript, can't be
  read. lexicon says so instead of guessing.
- **A whole website:** lexicon asks first. The default limit is 10 pages, and it won't go past 50
  without an explicit yes.
- **Pasted text.**

## Managing your lists

Ask in plain words.

- **Edit:** "change the definition of X" or "add an alias for X".
- **Move:** "move these terms to my AI list".
- **Find duplicates:** "check my lists for duplicates". lexicon catches spelling variants itself,
  but it never merges two terms that mean the same thing in different words without asking you.
- **Undo:** "undo that". lexicon shows what would change first, and can step back through the last
  20 versions of a list.

## Working with tutorial-creator

- **Send terms** for flashcards or review: "send my Swift terms to tutorial-creator". lexicon shows
  what it would add, then only appends. Existing entries are never touched, and terms
  tutorial-creator already has are skipped.
- **After a send, run `/tutorial-creator vocab regen-md`.** It brings VOCABULARY.md in step with the
  vocabulary file. It may ask about terms that exist only in the view: keep them or add them to the
  vocabulary file, and don't remove them.
- **Undo a send** within 24 hours with `/tutorial-creator vocab undo`.
- **Import** the other way: "start a Testing list from my tutorial-creator terms". lexicon rewords
  entries that name your app, files or people, and tells you which ones it changed.

## Where things live

Lists are in `~/.claude/lexicon/lists/`, one `.yaml` file per list with a readable `.md` file beside
it. The folder is created the first time you use lexicon. Set `LEXICON_HOME` to keep your lists
somewhere else. Don't edit the `.yaml` files by hand; ask Claude, so every change is validated and
backed up first.

## Good to know

- If a term was only named in the source and never explained, lexicon says so and asks whether to
  draft it from general knowledge.
- Your **level** matters most. If proposals feel too basic or too advanced, say so and lexicon can
  change the list's scope.
- Terms from an older session are dated with that session's date, not today's.
