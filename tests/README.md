# Deterministic tests

Requires Python 3.9+ and PyYAML. From the repository root:

```sh
python3 -B -m unittest discover -s tests -v
```

All data is synthetic and created in temporary directories, including paths containing spaces.
Tests pin the tutorial vocabulary explicitly and never read personal lists, registries or
transcripts. They cover rejection without mutation, complete-list validation, identifier/path
containment, future schema refusal, identity collisions and confirmed distinctions, relationship
maintenance, exact-byte undo, backup retention, export initialization, undo-marker collisions,
write failures, rollback failures, external-change preservation, locking, and source extraction.

Failure-injection tests mock filesystem writes at named transaction phases. They test ordinary
error recovery, not physical power loss or every possible race with a non-cooperating tool.
Lexicon locks coordinate its own writers; other tools must not edit the vocabulary concurrently.

Fixture tests verify all five behavioral scenarios can be prepared independently, list fixtures
validate, and the documented first-run command actually executes in an isolated home. For real
model behavior and companion-tool integration, follow `skills/lexicon/evals/README.md`; those runs
are tracked separately and are not implied by a green unit suite.
