# Behavioral evaluations

`evals.json` contains five model-driven scenarios. All referenced paths are bundled under
`fixtures/`; all content is synthetic. These scenarios require a live host and are **not run**
by the deterministic unit suite.

Prepare each scenario separately from the repository root:

```sh
python3 skills/lexicon/evals/prepare.py --case 1 --dest /tmp/lexicon-eval-case-1
```

The destination must be new. `case.json` gives its working directory, absolute `lexicon_home`,
vocabulary path, prompt, scripted user answers, and acceptance criteria. Run the host in that
working directory and pass that home to **every** helper invocation (`--home` before the
subcommand, or set `LEXICON_HOME` only for the evaluation process). The prepared config pins the
synthetic tutorial vocabulary, so discovery does not consult a personal registry.

Load the candidate `skills/lexicon/SKILL.md` directly or through an isolated project skill link.
Do not install over real skills. Restrict writes to the disposable evaluation directory and do
not read personal sessions, vocabularies, or memory. Supply the prompt first, then the scripted
answers when the host asks; let it perform its own term selection and drafting. Save the actual
host transcript, before/after files, and an explicit pass/fail assessment against every expected
outcome. Do not call a scenario passed based on fixture preparation or checker tests alone.

Case 4 requires a compatible tutorial-creator (2.1.0 or later) in the isolated host. Point it at
`fixtures/tcproj`, whose `tutorials/` contains the YAML and view. Exercise its real `regen-md`
and undo workflows; if the companion is unavailable, mark that case **skipped**, not passed.
The other four scenarios exercise Lexicon; export-marker structure is tested deterministically,
but actual companion undo remains a separate integration check.

Each case must start from a fresh prepared directory. Never reuse mutated fixtures between cases.
