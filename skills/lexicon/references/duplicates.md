# Duplicates: finding them and resolving them

A glossary with duplicates teaches the same thing twice with slightly different words, which is
worse than teaching it once. Worse still, it hides which definition is the good one. So check at
two moments: before adding anything, and in a periodic cleanup.

## What the script catches (surface forms)

`dupes` (before adding) and `audit` (after the fact) compare a **normalization key**:

| Variation | Example | Same key? |
|---|---|---|
| Case, surrounding quotes or backticks | `` `Actor` `` / actor | yes |
| Leading sigils | `@MainActor` / MainActor | yes |
| camelCase vs spaced | ViewThatFits / view that fits | yes |
| Separators | launch-argument fixture / launch_argument fixture | yes |
| Simple plurals | negative controls / negative control | yes |
| Parenthetical qualifier | Unit testing (isolation sense) / unit testing | yes, flagged as normalized |
| Aliases | term matches another entry's alias | yes, tier `alias` |

Tiers in the output: `exact` (same text apart from case and backticks), `normalized` (same key),
`alias` (matched through an alias). Run `python3 "$S" norm "<a>" "<b>"` to show the user why two
terms matched.

Note that a parenthetical qualifier often marks a *deliberate* distinction ("Unit testing
(isolation sense)" versus plain "unit testing"). A `normalized` match is a prompt to look, not a
verdict.

## What only you can catch (semantic)

The key cannot see:

- an acronym and its expansion with no alias linking them (MAD / median absolute deviation)
- synonyms (retain cycle / reference cycle)
- a renamed API and its old name (`@Observable` / Observable macro, `ObservableObject` era terms)
- one concept at two levels of generality: usually NOT duplicates; suggest `related_terms` instead

For each candidate, compare it against the target list's terms and definitions. Flag a pair only
when you would bet on it. Every flag costs the user a decision.

## Resolving

Present each flagged pair with both definitions side by side, and a recommendation:

```
Possible duplicate in swift:
  A: retain cycle        (added 2026-09-01) "Two objects holding strong references to each other..."
  B: reference cycle     (new candidate)     "A loop of strong references that ARC can't free..."
  Recommend: merge into "retain cycle" (the common term), keep B as an alias,
             keep A's definition (clearer).   [merge / keep both / skip new / use B's definition]
```

| Choice | Action |
|---|---|
| **merge** (both already stored) | `merge SLUG KEEP DROP [--override fields.json]` |
| **merge** (new candidate into an existing entry) | don't add; `update` the existing entry with its full `aliases` list, the old aliases plus the candidate (`update` replaces a list you send, so leaving the old ones out deletes them), and append a `seen_in` sighting with `--append-seen` |
| **keep both** | add or update with `distinct_from: [<other term>]` on the new entry, and `update` the other entry's `distinct_from` too, so neither is flagged again |
| **skip new** | drop the candidate; optionally record a sighting on the existing entry |
| **replace** | `update` the existing entry's fields with the better text |

Show the merged entry before running `merge`. It is destructive to the dropped entry, although
`undo` can restore it.

## Across lists

The same term in two lists is usually fine: "actor" in a Swift list and in a film list are
different words. For each cross-list hit, say which list already has it and offer:

- **leave both**: the default when the meanings differ; optionally mention the other in `notes`
- **copy**: `move SLUG TERM --to OTHER --copy` when the same meaning belongs in both
- **move**: `move ... --to OTHER` when it was filed in the wrong list

## Against tutorial-creator

`dupes --with-tc` also checks tutorial-creator's vocabulary (`--vocab PATH` checks a file the user
named). A hit there does not block adding the
term to a lexicon list, because the two stores serve different purposes. It does mean the term
should not be exported again, and `export-tc` skips it automatically.
