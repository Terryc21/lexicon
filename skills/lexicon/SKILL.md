---
name: lexicon
description: Build and maintain subject-specific vocabulary lists (glossaries) by scanning a source (the current or a past Claude Code session, a document or local file, an HTML page, or a whole website) and pulling out the words and phrases worth learning, each saved with a definition, a simple example, and a use case. Keeps several named lists (for example Swift, AI terms, Coding), interviews the user the first time to set each list's scope, and detects and resolves duplicates within and across lists. Use this whenever the user wants to capture, collect, harvest, or extract terms, jargon, acronyms, or vocabulary from something they read, wrote, or worked through; build, grow, clean up, or dedupe a glossary, term list, word list, or lexicon; asks which terms from a session, article, or doc they should learn; or wants to move terms to or from tutorial-creator's vocabulary. Prefer this over tutorial-creator for capturing and organizing terms by subject; tutorial-creator remains the tool for spaced-repetition review, flashcards, and tutorials.
---

# lexicon

Turns what the user reads and does into subject glossaries they can keep. A **list** is a named
glossary with a **scope** (subject, level, what to include and exclude). An **entry** is one term
with four required fields: `definition`, `example`, `use_case`, plus provenance.

The work splits cleanly in two:

- **You** decide what is worth adding, which list a term belongs to, how to define it, and whether
  two differently-worded terms are really the same concept.
- **`scripts/lexicon.py`** does everything that must be exact: storage, validation, surface-form
  duplicate matching, backups and undo, the Markdown view, the tutorial-creator mapping, and text
  extraction from transcripts and HTML. Every command prints JSON.

Never hand-edit the YAML list files with Write or Edit. Go through the script, so every change is
validated, backed up, and re-rendered.

## Setup for every invocation

```bash
S="$HOME/.claude/skills/lexicon/scripts/lexicon.py"   # or <this skill's dir>/scripts/lexicon.py
python3 "$S" status
```

`status` reports the home directory, whether it is initialized, the lists with entry counts, the
default list, and the detected tutorial-creator vocabulary file.

- Lists live in `~/.claude/lexicon/lists/<slug>.yaml`, and each has a generated `<slug>.md`
  beside it.
- `LEXICON_HOME` (or `--home DIR` on any command) overrides the location. If the user or task names
  a different home, pass it on every call. Tests and demos should always use a scratch home.
- If `first_run` is true (no lists exist yet, even if a config file does), do **First run** below
  before anything else.

## Routing

Map the user's words to a workflow. They will rarely use these exact phrases.

| The user wants to... | Do |
|---|---|
| Use lexicon for the first time, or has no lists yet | **First run** |
| Start a glossary for a new subject | **Scope interview**, then `new-list` |
| Pull terms from a session, file, doc, page, site, or pasted text | **Scan** |
| Add a specific term they name | **Scan** with the term as the only candidate (skip extraction) |
| See their lists, or a term | `lists`, `show <slug> [term]`; point them at the `.md` view |
| Change a definition, rename a term, add an alias | `update` (see references/schema.md for patch shape) |
| Find or fix duplicates | **Duplicates** |
| Move terms between lists, change a list's scope | `move`, `set-scope` |
| Send terms to tutorial-creator, or seed a list from it | **tutorial-creator interop** |
| Undo the last change to a list | `undo <slug> --dry-run`, confirm, then `undo <slug>` |

## First run

1. `python3 "$S" init`. This creates the home, `config.yaml`, and `lists/`, and reports whether a
   tutorial-creator vocabulary was detected.
2. Explain lexicon in two or three sentences: what a list is, and that each term gets a definition,
   an example, and a use case. Then run the **Scope interview** for the first list.
3. If a tutorial-creator vocabulary was detected, mention that it can seed the new list (see
   **tutorial-creator interop**), but do not import anything unless asked.

## Scope interview

The scope is what lets you judge whether a term is "worth adding." Without it, every scan either
floods the list with words the user already knows or misses the ones they came for. So interview
properly, but briefly. Use AskUserQuestion when it is available (one call, up to four questions);
otherwise ask in plain text.

Ask about:

1. **Subject.** Offer the common choices (Coding in general, a specific language such as Swift,
   AI and LLM terms, and so on). "Other" lets them name anything. The answer becomes the list name.
2. **Level.** beginner, intermediate, or advanced. This sets the floor: an intermediate Swift list
   should skip `var` and `if` but keep `nonisolated(unsafe)`. Explain it in those terms.
3. **What counts.** Multi-select from the kinds of terms that fit the subject. For code subjects,
   that means keywords and attributes, APIs and types, concepts, design patterns, acronyms, and
   multi-word phrases. For other subjects, pick equivalents.
4. **What to leave out.** For example: project-specific names (identifiers from the user's own
   code), product and company names, general English words, and terms they already know. If they
   list known terms, store them in `known_terms`, and those are never proposed again.

Then create the list:

```bash
echo '{"subject":"Swift and SwiftUI development","level":"intermediate",
       "include":["keywords and attributes","concurrency concepts","SwiftUI APIs","multi-word phrases"],
       "exclude":["identifiers from the user's own projects","general English"],
       "known_terms":[],"notes":""}' | python3 "$S" new-list --name "Swift" --scope -
```

The first list becomes the default. Scope can be changed later with `set-scope <slug> --scope -`
(a partial JSON patch).

## Scan

The core workflow. Read `references/sources.md` for how to get text out of each source type:
this session, a past session, a file, a PDF or Word document, a web page, a whole site, or pasted text.

**1. Pick the target list(s).** If the user named one, use it. If there is one list, use it. If
there are several and the source clearly spans subjects, you will route per term in step 4. If
nothing fits, offer to create a list (Scope interview) rather than forcing terms into a wrong one.
Load each target's scope with `show <slug>`.

**2. Get the text.** Follow `references/sources.md`. Tell the user what you are reading: which
session, which pages, how many characters. If a source was truncated or a page failed to load,
say so. A silent partial scan reads as a complete one.

**3. Extract candidates.** Read the text looking for terms that a reader at the list's level
would need defined. Weigh:

- Terms the source *explains* ("X is...", "X means...", a worked explanation) are the strongest
  candidates, because you can define them from the source itself.
- Multi-word phrases that act as one unit of jargon ("actor reentrancy", "confidence floor") count
  as terms.
- Acronyms: capture the expanded form as the term and the acronym as an alias, or the reverse if
  the acronym is what people actually say (`API`).
- Skip anything in the scope's `exclude` or `known_terms`, anything below the level floor, and
  one-off names that only mean something inside the user's own project, unless the scope includes them.
- A session transcript mixes signal with chatter. Terms the user *asked about* or had explained
  to them rank highest.

Keep a one-line note of where each candidate appeared. You need it for provenance and for
drafting definitions from the source.

**4. Route and dedupe.** Assign each candidate to a list by its scope. Then run the surface-form
duplicate check against everything, including tutorial-creator:

```bash
echo '[{"term":"actor reentrancy","aliases":[]},{"term":"MAD","aliases":["median absolute deviation"]}]' \
  | python3 "$S" dupes --candidates - --all --with-tc
```

That catches case, sigils, camelCase vs spaced, separators, plurals, parentheticals, and aliases.
It cannot catch synonyms or an acronym against its expansion when no alias links them, so also do
a **semantic pass**: compare each candidate against the existing terms in its target list (use
`show <slug>` for the term list) and flag likely same-concept pairs. See `references/duplicates.md`
for how to present and resolve each kind.

**5. Show the candidates and let the user choose.** Keep this to one screen:

```
Scanned <source> (<size>). Found N candidates for <list>.

Already in your lists (won't re-add):
  - reentrancy          matches "Actor reentrancy" in swift (normalized)
Possible duplicates (need your call):
  - isolation domain    may be the same concept as "actor isolation" in swift
New:
  1. actor reentrancy     explained in the session, when two calls interleaved
  2. nonisolated(unsafe)  used in a fix; not explained
  ...
Add which? [all / 1,3,5 / none]   Resolve the possible duplicates: [merge / keep both / skip]
```

Show at most 20 new candidates. If there are more, say how many were held back and offer the next
batch. Never drop them silently.

**6. Draft entries.** For each selected term, write the four fields following **Writing entries**
below. Present all drafts together for one confirmation (accept all / edit N / drop N), not one
prompt per term.

**7. Write.** Put the accepted entries in a JSON array and add them in one call per list. Pipe the
JSON on stdin (`--entries -`), or write it to a temporary directory (`mktemp -d`). Never put
scratch files beside the user's own files.

```bash
python3 "$S" add swift --entries /path/to/entries.json     # or --entries - with a heredoc
```

`add` rejects the whole batch if any entry is invalid or is a surface duplicate of something in
that list. Fix the entry, or, if the user confirmed the two terms are distinct, add the other term
to `distinct_from` and retry. Use `--partial` only when the user wants the valid ones saved anyway.
When a term the user kept was "already in your lists," record the new sighting on the existing
entry instead: `update <slug> --append-seen` with `{"term": ..., "seen_in": [{source, context, date}]}`.

**8. Report.** Say what was added to which list, the new count, and where the Markdown view is
(`~/.claude/lexicon/lists/<slug>.md`). Mention `undo` in one line.

## Writing entries

An entry has to make sense to a stranger who looks it up months later, with no memory of the
session it came from. Often that stranger is the user. So:

- **`definition`** stands alone. It defines the term in general terms, in one to three sentences.
  It never names the user's app, file, ticket, teammate, or incident. Pitch it at the list's level:
  a beginner definition explains the building blocks, and an advanced one can assume them.
- **`example`** is simple and concrete: a one-line code snippet for code terms, or one plain
  sentence showing the term in use for everything else. It illustrates the definition; it is not a
  second definition. Keep it short enough to take in at a glance, and strip internal identifiers.
- **`use_case`** says when or why someone reaches for this, starting with the general situation
  ("Use it whenever..."). Expand any jargon on first use.
- **`first_encountered`** is the only place for specifics: `source` (file path, URL, or
  "session transcript"), `context` (project, what was happening), `date`.

The check: hide `first_encountered` and reread the entry. If it stops making sense, or names
something a stranger could not know, rewrite it.

**Honesty.** Draft the definition from how the source used the term. If the source only named the
term without explaining it, say so for that term and ask whether to draft it from general
knowledge. When you do, the definition must still be correct in general, not just plausible.
Never invent an example that implies a real API, flag, or behavior you are not sure exists. For
code examples, prefer what appeared in the source.

Other fields: `type` (concept, api, keyword, attribute, pattern, idiom, acronym, phrase, or any
subject-appropriate label), `aliases` (other spellings and forms; they drive duplicate matching),
and `related_terms` (other terms **in the same list**). The full schema is in
`references/schema.md`.

## Duplicates

Three levels, each handled differently. Details and presentation formats are in
`references/duplicates.md`.

- **Same term, surface variants** (`@MainActor` / `MainActor` / `main actors`): the script finds
  these. Within one list they are almost always true duplicates, so offer a **merge**.
- **Same concept, different words** (an acronym and its expansion, synonyms, an old and a new name):
  only you can see these. Always ask; never merge on your own judgment. Once the user says two
  terms are different, record `distinct_from` on both entries so they are never flagged again.
- **Same term in two lists**: this is often legitimate, because "actor" means different things in
  Swift and in film. Point it out, and offer to copy (`move --copy`), cross-reference in `notes`,
  or leave both. Do not treat it as an error.

Cleanup of lists that already exist: `audit <slug>` or `audit --all`, then the semantic pass, then
resolve pairs one at a time with `merge <slug> KEEP DROP [--override fields.json]`. Merge keeps
KEEP's text unless you override it (offer the better definition of the two). It folds DROP's
term and aliases into KEEP's aliases, unions related terms and sightings, and rewrites other
entries' `related_terms` references. Show the merged result before you run it.

## tutorial-creator interop

lexicon stores terms by subject; tutorial-creator's `vocabulary.yaml` is where terms get studied
(review sessions, mastery status, flashcards). Moving terms between the two is explicit and
user-confirmed. Read `references/tutorial-creator.md` before either direction.

- **Export** (lexicon to tutorial-creator): run `export-tc <slug> <terms...> --dry-run` first and
  show what would be added and what would be skipped as already there. Then run it without
  `--dry-run`. The script appends to the end of their file without rewriting anything already in
  it. It writes tutorial-creator's own undo marker and rolls back if the result does not parse.
  Afterwards, relay the script's `next` message. Normally that says to run
  `/tutorial-creator vocab regen-md`. But when `view_drift.md_only_terms` is above zero, their
  VOCABULARY.md shows terms their yaml lacks, and a regen would erase those. In that case, say so
  and do not recommend the regen. Also mention that `/tutorial-creator vocab undo` reverses the
  export within 24 hours.
- **Import** (tutorial-creator to lexicon): `import-tc [--match REGEX] [--type T...] [--since DATE]`
  returns mapped entries without writing anything. Route them to lists by scope, fill any field
  listed in `needs` (usually `example`), run the duplicate steps, confirm, then `add`. Learning
  state (status, test history) stays in tutorial-creator.

## Things to avoid

- Writing list files directly, or writing to tutorial-creator's file any way other than `export-tc`.
- Adding terms without showing the user the list first. A scan is always propose, then confirm.
- Scanning a whole website without stating the page cap and getting a yes (see sources.md).
- Treating a clean `dupes` result as proof there are no duplicates. It only checks surface forms.
