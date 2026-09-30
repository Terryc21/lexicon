# lexicon data schema (schema_version 1)

## Layout

```
$LEXICON_HOME/                 default ~/.claude/lexicon
├── config.yaml                default_list, tutorial_creator_vocab
├── lists/
│   ├── swift.yaml             source of truth, one file per list
│   └── swift.md               generated view; rewritten on every save
└── .backups/
    └── swift--<timestamp-with-microseconds>-<unique-id>.yaml   previous version, last 20 kept per list
```

List changes go through `lexicon.py`. It validates the complete proposed list, including field
types, term/alias identities, and same-list relationships, then prepares the sorted YAML and
Markdown before writing. Rejected changes leave both files intact. Existing lists get backups;
the latest 20 are kept. `validate` returns JSON and exits nonzero when issues are found.

A schema version other than 1 is refused before modification. Use a compatible Lexicon version;
do not manually relabel a newer file as version 1. List identifiers must be lowercase letters
or digits separated by single hyphens, and resolved storage paths must remain inside the home.

### Multi-file writes and recovery

Lexicon serializes its own writers with a `.write-lock` directory in the home; exports also use
`vocabulary.yaml.lexicon-lock` beside the destination. Other tools do not honor these locks:
avoid editing the same vocabulary concurrently. Byte checks catch changes between phases,
but cannot eliminate every race with another tool.

Before replacing files, an operation records its original and planned bytes in
`.recovery/<id>/`, with a `manifest.json` mapping numbered `before` and `after` files to paths.
Ordinary write failures roll back completed replacements. If restoration fails or an external
change is detected, originals are retained and the error identifies the recovery directory.
Further writes are blocked while recovery data remains. A process interruption can also leave
a lock and recovery data; this is recoverable storage, not a claim of power-loss atomicity.

For recovery: stop writers, inspect the manifest and compare current files with the recorded
before/after bytes, then restore only the affected paths after accounting for any external edits.
Do not blindly copy over another tool's changes. Once the intended state is verified, remove
that recovery directory and any stale lock directories. Read-only inspection remains available.

### Relationships during rename, move, and copy

Renaming rewrites `related_terms` and `distinct_from` references and keeps the old name as an
alias. Conflicting canonical names or aliases are rejected. Confirmed normalized distinctions
use `distinct_from`; ambiguous lookup requires an exact stored name.

Moves and copies process each source entry once, even when selected through multiple aliases.
Relationships whose targets exist in the resulting list are kept; other relationships are
removed from the affected entries and reported in `removed_relationships`. Explain this policy
when proposing a move or copy, and report the removed links afterward. Source and destination
changes in a move share one rollback boundary. `undo` still operates on one list at a time;
restoring a move requires reviewing both lists' backups.

## config.yaml

```yaml
schema_version: 1
default_list: swift              # used when the user doesn't name a list
tutorial_creator_vocab: null     # null = auto-detect from ~/.claude/tutorial-creator/registry.yaml
```

Set with `lexicon.py config --default-list SLUG` or `--tc-vocab PATH`. Pass an empty string to clear.

## List file

```yaml
schema_version: 1
name: Swift                      # display name (free text)
slug: swift                      # file name; lowercase-hyphenated; never changes
created: '2026-09-28'
scope:
  subject: Swift and SwiftUI development
  level: intermediate            # beginner | intermediate | advanced
  include: [keywords and attributes, concurrency concepts, SwiftUI APIs]
  exclude: [identifiers from the user's own projects, general English]
  known_terms: [var, let, optional]   # never propose these
  notes: ''
entries: [...]
```

## Entry

```yaml
- term: Actor reentrancy               # required; the display form
  aliases: [reentrancy]                # other spellings; matched by the dupe checker
  type: concept                        # free label; default concept
  definition: |                        # required; stands alone (no app/file/person names)
    The property that an actor can start handling another call while one of its methods is
    suspended at an await, so its state may change across that await.
  example: |                           # required; one short snippet or sentence
    let before = count; await save(); count = before + 1  // count may have changed during save()
  use_case: |                          # required; general situation first
    Consider it whenever an actor reads state before an await and relies on it afterwards.
  related_terms: [actor isolation]     # terms in THIS list
  distinct_from: []                    # terms the user confirmed are NOT duplicates of this one
  first_encountered:                   # provenance; the only place for specifics
    source: session transcript         # file path, URL, "session transcript", "pasted text"
    context: Stuffolio review, background sync fix
    date: '2026-09-28'
  seen_in:                             # later sightings, appended by update --append-seen
  - {source: 'https://example.com/post', context: blog post, date: '2026-10-02'}
  added: '2026-09-28'
  updated: '2026-09-28'
  notes: ''                            # transferable remarks; still app-neutral
  exported_to_tc: null                 # date of last export to tutorial-creator
```

`add` fills defaults for everything except the four required text fields.

## Command shapes that take JSON

All accept a file path or `-` for stdin.

| Command | JSON |
|---|---|
| `new-list --scope` | `{subject, level, include, exclude, known_terms, notes}` (all optional) |
| `set-scope SLUG --scope` | partial scope object; merged into the existing scope |
| `dupes --candidates` | `[{"term": "...", "aliases": ["..."]}, ...]` |
| `add SLUG --entries` | array of entries (or one object). Optional `"confirmed_distinct": true` bypasses the surface-dupe refusal for that entry. |
| `update SLUG --entries` | `[{"term": "<existing term or alias>", <fields to replace>, "rename_to": "<optional new term>"}]`. With `--append-seen`, a `seen_in` list is appended instead of replacing. A rename keeps the old term as an alias. |
| `merge SLUG KEEP DROP --override` | `{<fields to set on KEEP before merging>}` (e.g. a better definition) |

## Other commands

`status`, `init`, `lists`, `show SLUG [TERM]`, `audit [SLUG|--all]`, `remove SLUG TERM...`,
`move SLUG TERM... --to SLUG [--copy]`, `undo SLUG [--dry-run]`, `validate [SLUG|--all]`,
`render SLUG [--stdout]`, `norm TERM...` (shows the duplicate-matching key; handy when explaining a
match), plus the source helpers in sources.md and the interop commands in tutorial-creator.md.

`undo` restores the most recent backup and consumes it, so repeated undos step further back.
It restores the whole list file, so it also reverts any other change made after that backup.
Always `--dry-run` first and say what will be lost.
