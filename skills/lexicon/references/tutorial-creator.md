# Working with tutorial-creator's vocabulary

tutorial-creator keeps a single `vocabulary.yaml` per tutorial project (its "Schema 2"): terms with
a learning state (`status`: new, reviewing, mastered, or confused, plus `test_history`) that its
`vocab review` and `vocab flashcards` commands use. lexicon keeps subject lists for capture and
reference. The interop moves terms between them without either tool taking over the other's job.

Commands below are written the hand-install way, `/tutorial-creator vocab undo`. If the user installed
tutorial-creator as a plugin, they type `/tutorial-creator:tutorial-creator vocab undo` instead. Use
whichever form they have.

## lexicon or `vocab ingest`?

tutorial-creator's `vocab ingest` also reads a source and adds its terms, straight into the review
deck. Either is fine when the user just wants to study a source's terms. Suggest lexicon when they
want a glossary kept by subject: scopes, duplicate checks across lists, aliases. The two connect both
ways: **Export** sends terms to the review deck, and **Import** brings terms `vocab ingest` added
into a list.

## Finding the file

`lexicon.py status` reports `tutorial_creator_vocab`. It resolves in this order:

1. `config.yaml` → `tutorial_creator_vocab` (set with `lexicon.py config --tc-vocab PATH`)
2. `~/.claude/tutorial-creator/registry.yaml` → the `default` project (or the first one), then
   that project's `.claude/tutorial-config.yaml` → `tutorials_dir` → `vocabulary.yaml`

Any interop command (`export-tc`, `import-tc`, `dupes`) also accepts `--vocab PATH` for one-off use.
**If the user names a tutorial vocabulary file or project, pass it with `--vocab` on every call, or
set it once with `config --tc-vocab`.** Otherwise `status`, and any call that omits the flag,
resolves the registry's default project, which may not be the one they meant. If nothing resolves,
ask the user where their tutorial project is. Don't search the disk for it.

## Field mapping

| lexicon | tutorial-creator | Notes |
|---|---|---|
| `term` | `term` | |
| `type` | `type` | free label in both |
| `definition` | `definition` | |
| `use_case` + `example` | `use_case` | tutorial-creator has no example field. On export, the example is appended to `use_case` as `Example: ...`, the shape its writing-style guide already uses. On import, a trailing `Example: ...` is split back out. |
| `related_terms` | `related_terms` | |
| `first_encountered.source` | `first_encountered.source_file` | only when it is a file path; URLs and "session transcript" go into `notes` |
| `first_encountered.context` | `first_encountered.context` = `external source` | Fixed value. tutorial-creator's `regen-md` files each new term under the view section whose heading matches this field, creating it if needed, so exports land under `## External source`. The original context goes into `notes`. |
| `aliases`, `distinct_from`, `seen_in` | (none) | not carried; tutorial-creator has no such fields |
| (none) | `status: new`, `test_history: []`, `applied_test_history: []` | exported terms start fresh in the review cycle |
| (none) | `notes` | `Exported from lexicon list "<name>" on <date>. First seen: <context>. Source: <url or label>.` |
| `notes` (on import) | `notes` | tutorial-creator's lesson bookkeeping (`First seen: Day N`, `Also seen in:`, the `Per-Day wording` block) is dropped. A source and merge history stay. |

## Export: lexicon → tutorial-creator

```bash
python3 "$S" export-tc <slug> "<term>" "<term>" --dry-run     # or --all
python3 "$S" export-tc <slug> "<term>" "<term>"
```

What the script guarantees, so you can tell the user plainly:

- **Existing entries are untouched.** New entries are appended to the end of the file. The file is
  never re-serialized, because re-dumping would reformat hundreds of hand-curated entries.
  An empty `[]` becomes a block list, preserving surrounding comments. Nonempty flow-style
  lists are refused before writing; let the owning tool convert them to block style first.
- **No duplicates.** Terms that already exist there (same surface-form matching as `dupes`) are
  skipped and listed.
- **Safe failure.** It validates the proposed YAML before writing. Vocabulary, undo-marker and
  source-list changes share a rollback boundary, with recovery copies retained if rollback
  cannot finish or another tool changed a file. See schema.md's recovery procedure. Do not
  edit the vocabulary with another tool during export.
- **Undoable.** It writes tutorial-creator's own 24-hour undo marker
  (`vocabulary.yaml.add-<timestamp>`, one term per line), so `/tutorial-creator vocab undo`
  reverses the whole export as one batch. A timestamp collision is rejected before any write;
  retry in the next second. An earlier marker is never overwritten.
- The lexicon entries get `exported_to_tc: <date>`.

### After an export: `regen-md`

`regen-md` brings VOCABULARY.md in step with vocabulary.yaml, in place. The exported terms get rows,
and so does any other yaml term whose row is missing; each term's row takes the yaml's current
definition, and the Cumulative Count is recomputed. Headings, Source lines, row order and repeat
rows are kept, and nothing is removed without asking. If the view has drifted from the yaml, that
can be many more rows than the exported ones, so say so before the user runs it. If there is no
VOCABULARY.md yet, regen-md builds one.

`export-tc` also reports `view_drift` (on `--dry-run` too): the number of terms the view shows that
the yaml lacks, which tutorial-creator's `vocab review` and flashcards never see. It matches rows to
terms the way regen-md does (ignoring case, backticks and bold), so it is the number regen-md will
ask about.

- `md_only_terms: 0`: tell the user to run `/tutorial-creator vocab regen-md`.
- Above zero: tell them the same, and that regen-md will ask about those terms (show a few from
  `examples`). Recommend keeping them or adding them to the yaml; removing one loses it.

This relies on tutorial-creator's in-place `regen-md` (tutorial-creator 2.1.0 or later). An older one
rebuilds the view from the yaml and erases view-only terms, so with an older tutorial-creator, don't
recommend the regen while `md_only_terms` is above zero. `claude plugin list` shows a plugin's
version. A plugin stays on the old one until updated (`claude plugin marketplace update
tutorial-creator`, then `claude plugin update tutorial-creator@tutorial-creator`); a hand install
needs a `git pull` in its folder.

lexicon does not regenerate the other tool's Markdown view; it changes only the vocabulary and its export undo marker as described above.

### Flashcards of just the exported terms

Every exported term's `notes` start `Exported from lexicon list ...`, and tutorial-creator's
`--source` filter searches notes, so `vocab flashcards --source=lexicon` makes cards from only the
terms sent from lexicon. The date filters (`--date-from`, `--date-to`) use the day each term was
first met, not the day it was sent.

### If the user undoes an export on the tutorial-creator side

`/tutorial-creator vocab undo` removes the terms from their file but cannot see lexicon's
`exported_to_tc` stamps. Clear them so a later export does not look like a repeat:
`update <slug> --entries -` with `[{"term": "<t>", "exported_to_tc": null}, ...]`.

### Reading their file

Judge their data through the parser (`import-tc`, or `yaml.safe_load`), not from the raw text.
YAML escapes look like errors when read raw: inside a single-quoted string, `''` is a single
apostrophe. Before you report a problem in their file, confirm it in the parsed value.

## Import: tutorial-creator → lexicon

```bash
python3 "$S" import-tc --match "actor|isolation" --since 2026-09-01 --limit 40
python3 "$S" import-tc --type concept pattern
```

This writes nothing. It returns entries already in lexicon shape, each with a `needs` list naming
empty required fields. Older tutorial-creator entries usually have neither an `example` nor a
`use_case` (on one real 305-term vocabulary, 09/30/2026: 284 had no example and 236 no use case);
entries from `vocab ingest` usually have a use case. Notes come across with tutorial-creator's lesson
bookkeeping removed (`First seen: Day N`, `Also seen in:`, the `Per-Day wording` block). A source URL
or file and any merge history stay. Then:

1. Route each entry to a list by scope. Leave out ones no list's scope covers, and say so.
2. Fill the `needs` fields following the writing rules in SKILL.md. Tell the user which examples
   you wrote, since they did not come from the source.
3. Reread every entry's `definition`, `use_case` and `notes` against the same writing rules. The
   `needs` list only flags empty fields, and tutorial-creator entries can carry the user's app or
   file names or a person's name. Reword those before storing, and tell the user which entries you
   changed.
4. Run `dupes` and the semantic pass, as in a normal scan. On an import every candidate matches its
   own tutorial-creator entry, so ignore those self-matches and look for other hits.
5. Confirm, then `add`. It strips the working `needs` key, so there is no need to remove it first.

Mastery status stays in tutorial-creator. lexicon does not track learning state.

For a large import (the user's vocabulary may hold hundreds of terms), work in batches of at most
20, the same limit as a scan screen. Show progress, and let the user stop between batches. Most of a
big import is writing missing examples and use cases, so offer a slice first (`--match` or `--since`)
before committing to the whole vocabulary.
