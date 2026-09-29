# lexicon

You read an article, work through a long coding session, or skim a new framework's docs, and a
dozen unfamiliar terms go past. A week later you remember two of them. lexicon is a Claude Code
skill that catches those terms as they appear and keeps them in glossaries you can come back to.

Point it at something (this session, an old session, a file, a PDF, a web page, or a whole site)
and it proposes the terms worth learning. You pick which to keep. Each one is saved with:

- a **definition** that makes sense on its own, months later, without the context it came from
- a **simple example**: a one-line code snippet, or a sentence showing the term in use
- a **use case**: when you would actually reach for it

## Several lists, each with a scope

Keep a list per subject: Swift, AI terms, statistics, whatever you are learning. The first time
you start a list, lexicon asks a few questions (subject, your level, what kinds of terms to
include, what to leave out) and uses the answers to decide what is worth proposing. An
intermediate Swift list skips `var` and keeps `nonisolated(unsafe)`. Terms you already know are
recorded so they are never suggested again.

When a source covers more than one subject, each term is filed in the list whose scope fits it.

## Duplicates

A glossary that defines the same thing twice, in slightly different words, hides which definition
is the good one. lexicon checks before anything is added:

- **Spelling variants** (`@MainActor`, `MainActor`, `main actors`) are caught mechanically.
- **Same idea, different words** (an acronym and its expansion, two names for one concept) are
  flagged for you to decide. lexicon never merges these on its own.
- **The same term in two lists** is often fine ("actor" in Swift and in film), so it is pointed
  out, not treated as an error.

A cleanup pass (`audit`) finds duplicates already sitting in your lists, and merging keeps the
better definition while remembering the other spelling as an alias.

## Works with tutorial-creator

If you use [tutorial-creator](https://github.com/Terryc21/tutorial-creator) for spaced-repetition
review, lexicon can send terms to its vocabulary file and seed a list from it. Exports only append
to that file, skip terms it already has, and can be undone with tutorial-creator's own
`vocab undo`.

## Install

```bash
git clone https://github.com/Terryc21/lexicon.git
ln -s "$PWD/lexicon/skills/lexicon" ~/.claude/skills/lexicon
pip3 install pyyaml   # the helper script's only dependency
```

Then ask Claude something like "start a Swift vocab list from this article" or "what terms from
this session should I learn?"

For a walkthrough of scans, lists, duplicates and the tutorial-creator link, see the
[user guide](LEXICON-USER-GUIDE.md).

## Where things are stored

Lists live in `~/.claude/lexicon/lists/`, one YAML file per list, with a generated Markdown file
beside each one for reading. Every change is backed up first, and `undo` steps back through the
last 20 versions of a list. Set `LEXICON_HOME` to keep them somewhere else.

## How it is built

`skills/lexicon/SKILL.md` holds the workflow and the judgment calls: what counts as worth
learning, and how to write an entry a stranger can use. `skills/lexicon/scripts/lexicon.py`
handles the parts that must be exact every time: storage, validation, duplicate matching,
backups, and the tutorial-creator file format.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
