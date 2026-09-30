#!/usr/bin/env python3
"""lexicon.py: deterministic helper for the lexicon skill.

The skill (SKILL.md) owns judgment: which terms are worth adding, how to
define them, whether two terms are "the same concept". This script owns the
mechanical parts that must be exact every time: file layout, schema
validation, safe writes with backups, surface-form duplicate matching, the
tutorial-creator field mapping, and text extraction from transcripts/HTML.

Every command prints JSON (or Markdown for `render --stdout`) so the skill
can read results without parsing prose.

Home directory: $LEXICON_HOME if set, else ~/.claude/lexicon. `--home DIR`
overrides both (used by tests and evals so they never touch real lists).
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import tempfile
import uuid
from pathlib import Path
import datetime as dt
import glob
import html as htmllib
import html.parser
import json
import os
import re
import shutil
import sys
import unicodedata

try:
    import yaml
except ImportError:  # pragma: no cover
    print(json.dumps({"ok": False, "error": "PyYAML is required: pip3 install pyyaml"}))
    sys.exit(2)

SCHEMA_VERSION = 1
BACKUPS_KEPT = 20
ENTRY_KEYS = [
    "term", "aliases", "type", "definition", "example", "use_case",
    "related_terms", "distinct_from", "first_encountered", "seen_in",
    "added", "updated", "notes", "exported_to_tc",
]
REQUIRED_TEXT = ["term", "definition", "example", "use_case"]
TRANSIENT_KEYS = {"needs", "confirmed_distinct"}   # working keys the skill may pass; never stored
LEVELS = {"beginner", "intermediate", "advanced"}


# ---------------------------------------------------------------- utilities

def today() -> str:
    return dt.date.today().isoformat()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def out(obj, code: int = 0):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    sys.exit(code)


def fail(msg: str, **extra):
    out({"ok": False, "error": msg, **extra}, 1)


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "list"


class _Dumper(yaml.SafeDumper):
    pass


def _str_rep(dumper, data):
    # Multi-line text reads far better as a block scalar in a hand-browsable file.
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_rep)


def ydump(obj) -> str:
    return yaml.dump(obj, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def load_json_arg(path: str):
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(raw)


# ------------------------------------------------------------ normalization

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _singular(w: str) -> str:
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if re.search(r"(ss|x|z|ch|sh)es$", w):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def norm(term: str) -> str:
    """Surface-form key: two terms with the same key are PROBABLY the same term.

    Handles case, backticks/quotes, sigils (@MainActor), camelCase vs spaced
    (ViewThatFits / view that fits), separators (-, _, /, .), parenthetical
    qualifiers, and simple plurals. It deliberately does NOT try synonyms or
    acronyms; that is a semantic call the skill makes, not a string rule.
    """
    t = unicodedata.normalize("NFKC", str(term)).strip()
    t = re.sub(r"\(.*?\)", " ", t)                 # "Unit testing (isolation sense)"
    t = t.strip("`'\" ")
    t = re.sub(r"^[@#$.]+", "", t)                  # @MainActor, #Predicate, .task
    t = _CAMEL.sub(" ", t)
    t = t.lower()
    t = re.sub(r"[\s_\-/.:]+", " ", t)
    t = re.sub(r"[^\w\s+#]", "", t)
    words = [_singular(w) for w in t.split()]
    return " ".join(words).strip()


def exact(term: str) -> str:
    return unicodedata.normalize("NFKC", str(term)).strip().strip("`").casefold()


def sort_key(term: str) -> str:
    """Alphabetical order that ignores leading sigils, so @MainActor files under M."""
    return re.sub(r"^[^\w]+", "", exact(term))


# ------------------------------------------------------------------ storage

def home_dir(args) -> str:
    h = args.home or os.environ.get("LEXICON_HOME") or os.path.expanduser("~/.claude/lexicon")
    return os.path.abspath(os.path.expanduser(h))


def lists_dir(h: str) -> str:
    return os.path.join(h, "lists")


def checked_slug(slug: str) -> str:
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        fail("List identifiers must contain lowercase letters, digits, and single hyphens.")
    return slug


def storage_path(h: str, folder: str, name: str) -> str:
    root = os.path.realpath(h)
    base = os.path.realpath(os.path.join(root, folder))
    path = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([root, base]) != root or os.path.commonpath([base, path]) != base:
        fail("Storage path escapes the selected Lexicon home.")
    return path


def list_path(h: str, slug: str) -> str:
    return storage_path(h, "lists", f"{checked_slug(slug)}.yaml")


def config_path(h: str) -> str:
    return storage_path(h, "", "config.yaml")


def read_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_config(h: str) -> dict:
    p = config_path(h)
    if not os.path.exists(p):
        return {}
    data = read_yaml(p)
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        fail("Unsupported config schema; use a compatible Lexicon version. Nothing changed.")
    return data


def file_bytes(path):
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError(f"Expected a regular file: {path}")
        with open(path, "rb") as f:
            return f.read()
    return None


def write_bytes_atomic(path, content, *, exclusive=False):
    fd, tmp = tempfile.mkstemp(prefix=".lexicon-", dir=os.path.dirname(path))
    try:
        if os.path.exists(path):
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            # Atomic no-clobber publication: even an external marker racing this write survives.
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_atomic(path: str, text: str):
    write_bytes_atomic(path, text.encode("utf-8"))


@contextlib.contextmanager
def writer_lock(path):
    """Cooperating writers serialize; a crash leaves an explicit, inspectable lock."""
    try:
        os.mkdir(path)
    except FileExistsError:
        fail(f"Another write or an interrupted write holds {path}. "
             "If no Lexicon process is running, inspect .recovery in the home before removing the lock.")
    try:
        yield
    finally:
        os.rmdir(path)


def commit_files(h, changes, expected=None):
    """Prepare everything first; retain original and planned bytes if rollback cannot finish.

    Locks cover Lexicon writers. Byte comparisons detect other writers between phases;
    they are not a cross-tool lock. Never overwrite a detected external change on rollback.
    """
    originals = {p: file_bytes(p) for p in changes}
    for path, before in (expected or {}).items():
        if file_bytes(path) != before:
            raise ValueError(f"File changed while preparing the operation: {path}; retry.")
    recovery = storage_path(h, ".recovery", uuid.uuid4().hex)
    os.makedirs(recovery)
    applied = []
    try:
        manifest = []
        for i, (path, planned) in enumerate(changes.items()):
            old = originals[path]
            for label, content in (("before", old), ("after", planned)):
                if content is not None:
                    write_bytes_atomic(os.path.join(recovery, f"{i}.{label}"), content)
            manifest.append({"path": path, "before": old is not None, "after": planned is not None})
        write_atomic(os.path.join(recovery, "manifest.json"), json.dumps(manifest, indent=2))
        for path, planned in changes.items():
            if file_bytes(path) != originals[path]:
                raise ValueError(f"Concurrent change detected: {path}")
            if planned is None:
                if originals[path] is not None:
                    os.remove(path)
            else:
                write_bytes_atomic(path, planned, exclusive=originals[path] is None)
            applied.append(path)
        # A non-cooperating writer may have touched an earlier file during later phases.
        if any(file_bytes(p) != content for p, content in changes.items()):
            raise ValueError("Concurrent change detected after writing.")
    except BaseException as error:
        failures = []
        for path in reversed(applied):
            try:
                if file_bytes(path) != changes[path]:
                    raise ValueError("external change; preserved")
                if originals[path] is None:
                    os.remove(path)
                else:
                    write_bytes_atomic(path, originals[path])
            except (OSError, ValueError) as rollback_error:
                failures.append(f"{path}: {rollback_error}")
        if failures:
            raise RuntimeError(f"Operation failed ({error}); recovery copies retained at {recovery}. "
                               + "; ".join(failures)) from error
        shutil.rmtree(recovery)
        raise
    shutil.rmtree(recovery)


def load_list(h: str, slug: str) -> dict:
    p = list_path(h, slug)
    if not os.path.exists(p):
        fail(f"No list named '{slug}'.", available=[l["slug"] for l in all_lists(h)])
    data = read_yaml(p)
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        fail("Unsupported list schema; use a compatible Lexicon version. Nothing changed.")
    if data.get("slug") != slug or not isinstance(data.get("entries"), list):
        fail("List structure or stored identifier does not match its file. Nothing changed.")
    return data


def prepared_list(h, data, with_backup=True):
    issues = list_problems(data)
    if issues:
        fail("Nothing written: proposed list is invalid.", issues=issues)
    data["entries"] = sorted(data["entries"], key=lambda e: sort_key(e["term"]))
    slug = checked_slug(data["slug"])
    path = list_path(h, slug)
    changes = {path: (header_comment(data) + ydump(data)).encode("utf-8"),
               storage_path(h, "lists", f"{slug}.md"): render_md(data).encode("utf-8"),
               storage_path(h, "lists", f"{slug}.html"): render_html(data).encode("utf-8")}
    original = file_bytes(path)
    if with_backup and original is not None:
        backup_path = storage_path(h, ".backups", f"{slug}--{dt.datetime.now().strftime('%Y-%m-%dT%H-%M-%S.%f')}-{uuid.uuid4().hex}.yaml")
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        changes[backup_path] = original
    return changes


def prune_backups(h, slug):
    pattern = storage_path(h, ".backups", f"{checked_slug(slug)}--*.yaml")
    for path in sorted(glob.glob(pattern))[:-BACKUPS_KEPT]:
        try:
            os.remove(path)
        except OSError:
            pass  # Retaining extra backups is safer than failing a committed operation.


def save_list(h: str, data: dict):
    commit_files(h, prepared_list(h, data))
    prune_backups(h, data["slug"])


def header_comment(data: dict) -> str:
    return (f"# lexicon list: {data.get('name')}\n"
            f"# Source of truth. {data.get('slug')}.md beside it is generated; do not edit that one.\n")


def all_lists(h: str) -> list[dict]:
    res = []
    for p in sorted(glob.glob(os.path.join(lists_dir(h), "*.yaml"))):
        try:
            d = read_yaml(p) or {}
        except yaml.YAMLError as e:
            res.append({"slug": os.path.basename(p)[:-5], "error": str(e)})
            continue
        res.append({
            "slug": os.path.basename(p)[:-5],
            "name": d.get("name"),
            "subject": (d.get("scope") or {}).get("subject"),
            "level": (d.get("scope") or {}).get("level"),
            "entries": len(d.get("entries") or []),
            "path": p,
        })
    return res


def clean_entry(e: dict) -> dict:
    """Fill defaults and order keys so every stored entry has the same shape."""
    e = {k: v for k, v in e.items() if k not in TRANSIENT_KEYS}
    e.setdefault("aliases", [])
    e.setdefault("type", "concept")
    e.setdefault("related_terms", [])
    e.setdefault("distinct_from", [])
    fe = e.get("first_encountered") or {}
    e["first_encountered"] = {
        "source": fe.get("source", ""),
        "context": fe.get("context", ""),
        "date": str(fe.get("date") or today()),
    }
    e.setdefault("seen_in", [])
    e.setdefault("added", today())
    e["updated"] = today()
    e.setdefault("notes", "")
    e.setdefault("exported_to_tc", None)
    for k in ("definition", "example", "use_case", "notes"):
        if isinstance(e.get(k), str):
            e[k] = e[k].strip()
    ordered = {k: e[k] for k in ENTRY_KEYS if k in e}
    ordered.update({k: v for k, v in e.items() if k not in ordered})
    return ordered


def entry_problems(e: dict) -> list[str]:
    if not isinstance(e, dict):
        return ["entry must be an object"]
    probs = []
    for k in REQUIRED_TEXT:
        v = e.get(k)
        if not isinstance(v, str) or not v.strip():
            probs.append(f"'{k}' is missing or empty")
    if isinstance(e.get("term"), str) and any(c in e["term"] for c in "\r\n"):
        probs.append("term must fit on one line (including in undo markers)")
    for k in ("aliases", "related_terms", "distinct_from"):
        if k in e and (not isinstance(e[k], list) or any(not isinstance(x, str) or not x.strip() for x in e[k])):
            probs.append(f"'{k}' must be a list of nonempty strings")
    for k in ("type", "notes"):
        if k in e and not isinstance(e[k], str):
            probs.append(f"'{k}' must be text")
    sightings = e.get("seen_in", [])
    if not isinstance(sightings, list):
        probs.append("'seen_in' must be a list")
        sightings = []
    for item in ([e["first_encountered"]] if "first_encountered" in e else []) + sightings:
        if not isinstance(item, dict):
            probs.append("provenance must be an object")
            continue
        for k in ("source", "context"):
            if k in item and not isinstance(item[k], str):
                probs.append(f"provenance.{k} must be text")
        if "date" in item and item["date"] is not None and not isinstance(item["date"], (str, dt.date)):
            probs.append("provenance.date must be a date or text")
    return probs


def list_problems(data):
    if not isinstance(data, dict):
        return ["list must be an object"]
    issues = []
    if data.get("schema_version") != SCHEMA_VERSION:
        issues.append("unsupported schema_version")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        issues.append("name must be nonempty text")
    slug = data.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        issues.append("invalid slug")
    sc = data.get("scope")
    if not isinstance(sc, dict):
        issues.append("scope must be an object")
    else:
        if sc.get("level") not in LEVELS:
            issues.append("scope.level missing or invalid")
        for key in ("include", "exclude", "known_terms"):
            if not isinstance(sc.get(key, []), list) or any(not isinstance(x, str) for x in sc.get(key, [])):
                issues.append(f"scope.{key} must be a list of strings")
        for key in ("subject", "notes"):
            if key in sc and not isinstance(sc[key], str):
                issues.append(f"scope.{key} must be text")
    entries = data.get("entries")
    if not isinstance(entries, list):
        return issues + ["entries must be a list"]
    for i, e in enumerate(entries):
        issues.extend(f"entry {i + 1}: {p}" for p in entry_problems(e))
    if issues:
        return issues
    terms = {exact(e["term"]) for e in entries}
    for i, e in enumerate(entries):
        for r in e.get("related_terms", []):
            if exact(r) not in terms:
                issues.append(f"{e['term']}: related term '{r}' is not in this list (dangling)")
        forms = [e["term"]] + e.get("aliases", [])
        for other in entries[:i]:
            other_forms = [other["term"]] + other.get("aliases", [])
            if {exact(x) for x in forms} & {exact(x) for x in other_forms}:
                issues.append(f"Ambiguous term or alias: {e['term']} / {other['term']}")
            elif match(forms, index_terms([other], data["slug"])):
                distinct = (exact(other["term"]) in {exact(x) for x in e.get("distinct_from", [])}
                            or exact(e["term"]) in {exact(x) for x in other.get("distinct_from", [])})
                if not distinct:
                    issues.append(f"Unresolved duplicate: {e['term']} / {other['term']}; merge or record distinct_from")
    return issues


# --------------------------------------------------------------- dup engine

def index_terms(entries: list[dict], where: str) -> list[dict]:
    idx = []
    for e in entries:
        t = e.get("term", "")
        idx.append({"where": where, "term": t, "form": t, "via": "term"})
        for a in e.get("aliases") or []:
            idx.append({"where": where, "term": t, "form": a, "via": "alias"})
    return idx


def match(cand_forms: list[str], idx: list[dict], exclude_terms: set[str] = frozenset()) -> list[dict]:
    hits, seen = [], set()
    ce = {exact(f) for f in cand_forms}
    cn = {norm(f) for f in cand_forms if norm(f)}
    for i in idx:
        if exact(i["term"]) in exclude_terms:
            continue
        tier = None
        if exact(i["form"]) in ce:
            tier = "exact" if i["via"] == "term" else "alias"
        elif norm(i["form"]) in cn:
            tier = "normalized" if i["via"] == "term" else "alias"
        if tier:
            key = (i["where"], exact(i["term"]))
            if key in seen:
                continue
            seen.add(key)
            hits.append({"where": i["where"], "term": i["term"], "tier": tier, "matched_form": i["form"]})
    return hits


def tc_entries(path: str) -> list[dict]:
    data = read_yaml(path)
    if data is None:
        data = []
    if not isinstance(data, list) or any(not isinstance(e, dict) or not isinstance(e.get("term"), str) for e in data):
        fail(f"{path} is not a tutorial-creator vocabulary.yaml (expected a top-level list).")
    return data


def default_tc_vocab(h: str) -> str | None:
    cfg = read_config(h)
    if cfg.get("tutorial_creator_vocab"):
        return cfg["tutorial_creator_vocab"]
    reg = os.path.expanduser("~/.claude/tutorial-creator/registry.yaml")
    if not os.path.exists(reg):
        return None
    r = read_yaml(reg) or {}
    root = r.get("default") or ((r.get("projects") or [{}])[0].get("path"))
    if not root:
        return None
    tdir = "."
    tconf = os.path.join(root, ".claude", "tutorial-config.yaml")
    if os.path.exists(tconf):
        tdir = (read_yaml(tconf) or {}).get("tutorials_dir") or "."
    p = os.path.normpath(os.path.join(root, tdir, "vocabulary.yaml"))
    return p if os.path.exists(p) else None


# ----------------------------------------------------------------- commands

def cmd_init(args):
    h = home_dir(args)
    os.makedirs(lists_dir(h), exist_ok=True)
    created = False
    if not os.path.exists(config_path(h)):
        cfg = {"schema_version": SCHEMA_VERSION, "default_list": None, "tutorial_creator_vocab": None}
        write_atomic(config_path(h), ydump(cfg))
        created = True
    out({"ok": True, "home": h, "created_config": created, "lists": all_lists(h),
         "tutorial_creator_vocab_detected": default_tc_vocab(h)})


def cmd_status(args):
    h = home_dir(args)
    cfg = read_config(h)
    lists = all_lists(h) if os.path.isdir(lists_dir(h)) else []
    out({"ok": True, "home": h, "initialized": os.path.exists(config_path(h)),
         "first_run": not lists,   # no lists yet: run First run (orientation + scope interview)
         "default_list": cfg.get("default_list"), "lists": lists,
         "tutorial_creator_vocab": default_tc_vocab(h)})


def cmd_config(args):
    h = home_dir(args)
    cfg = read_config(h) or {"schema_version": SCHEMA_VERSION}
    if args.default_list is not None:
        if args.default_list and not os.path.exists(list_path(h, args.default_list)):
            fail(f"No list named '{args.default_list}'.")
        cfg["default_list"] = args.default_list or None
    if args.tc_vocab is not None:
        cfg["tutorial_creator_vocab"] = os.path.abspath(args.tc_vocab) if args.tc_vocab else None
    os.makedirs(h, exist_ok=True)
    write_atomic(config_path(h), ydump(cfg))
    out({"ok": True, "config": cfg})


def cmd_new_list(args):
    h = home_dir(args)
    os.makedirs(lists_dir(h), exist_ok=True)
    scope = load_json_arg(args.scope) if args.scope else {}
    slug = args.slug or slugify(args.name)
    if os.path.exists(list_path(h, slug)):
        fail(f"A list with slug '{slug}' already exists.", path=list_path(h, slug))
    level = scope.get("level", "intermediate")
    if level not in LEVELS:
        fail(f"scope.level must be one of {sorted(LEVELS)}")
    data = {
        "schema_version": SCHEMA_VERSION,
        "name": args.name,
        "slug": slug,
        "created": today(),
        "scope": {
            "subject": scope.get("subject", args.name),
            "level": level,
            "include": scope.get("include", []),
            "exclude": scope.get("exclude", []),
            "known_terms": scope.get("known_terms", []),
            "notes": scope.get("notes", ""),
        },
        "entries": [],
    }
    changes = prepared_list(h, data)
    cfg = read_config(h)
    if not cfg.get("default_list"):
        cfg.setdefault("schema_version", SCHEMA_VERSION)
        cfg["default_list"] = slug
        changes[config_path(h)] = ydump(cfg).encode("utf-8")
    commit_files(h, changes)
    out({"ok": True, "slug": slug, "path": list_path(h, slug), "scope": data["scope"]})


def cmd_set_scope(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    patch = load_json_arg(args.scope)
    if "level" in patch and patch["level"] not in LEVELS:
        fail(f"scope.level must be one of {sorted(LEVELS)}")
    data["scope"].update(patch)
    if args.rename:
        data["name"] = args.rename
    save_list(h, data)
    out({"ok": True, "scope": data["scope"], "name": data["name"]})


def cmd_lists(args):
    out({"ok": True, "home": home_dir(args), "lists": all_lists(home_dir(args))})


def cmd_show(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    if args.term:
        hits = [e for e in data["entries"]
                if match([args.term], index_terms([e], args.slug))]
        out({"ok": True, "matches": hits})
    summary = {k: v for k, v in data.items() if k != "entries"}
    summary["entry_count"] = len(data["entries"])
    summary["terms"] = [e["term"] for e in data["entries"]]
    out({"ok": True, **summary})


def cmd_dupes(args):
    """Surface-form duplicate check for candidates BEFORE they are added."""
    h = home_dir(args)
    cands = load_json_arg(args.candidates)
    idx = []
    targets = [l["slug"] for l in all_lists(h)] if args.all or not args.list else [args.list]
    for s in targets:
        idx += index_terms(load_list(h, s)["entries"], s)
    tc_path = args.tc or (default_tc_vocab(h) if args.with_tc else None)
    if tc_path:
        idx += index_terms(tc_entries(tc_path), "tutorial-creator")
    res, pairs_in_batch = [], []
    for i, c in enumerate(cands):
        forms = [c["term"]] + list(c.get("aliases") or [])
        hits = match(forms, idx)
        res.append({"term": c["term"], "matches": hits})
        for j in range(i):
            other = cands[j]
            if match(forms, index_terms([other], "batch")):
                pairs_in_batch.append([other["term"], c["term"]])
    out({"ok": True, "checked_against": targets + (["tutorial-creator"] if tc_path else []),
         "results": res, "duplicates_within_batch": pairs_in_batch,
         "note": "Surface-form matches only. Synonyms, acronyms vs expansions, and "
                 "same-concept-different-name pairs still need a judgment pass."})


def cmd_audit(args):
    """Find duplicate pairs already stored: within each list and across lists."""
    h = home_dir(args)
    slugs = [l["slug"] for l in all_lists(h)] if args.all or not args.slug else [args.slug]
    within, across = [], []
    loaded = {s: load_list(h, s)["entries"] for s in (l["slug"] for l in all_lists(h))}
    for s in slugs:
        ents = loaded[s]
        for i, e in enumerate(ents):
            suppressed = {exact(x) for x in e.get("distinct_from") or []}
            for f in ents[i + 1:]:
                if exact(f["term"]) in suppressed or exact(e["term"]) in {exact(x) for x in f.get("distinct_from") or []}:
                    continue
                hit = match([e["term"]] + (e.get("aliases") or []), index_terms([f], s))
                if hit:
                    within.append({"list": s, "a": e["term"], "b": f["term"], "tier": hit[0]["tier"]})
        for other, oents in loaded.items():
            if other == s or (other in slugs and other < s):
                continue
            for e in ents:
                hit = match([e["term"]] + (e.get("aliases") or []), index_terms(oents, other))
                for x in hit:
                    across.append({"a_list": s, "a": e["term"], "b_list": other, "b": x["term"], "tier": x["tier"]})
    out({"ok": True, "within_list": within, "across_lists": across,
         "note": "Cross-list pairs are often legitimate (one term, two subjects). "
                 "Within-list pairs are the ones to resolve."})


def cmd_add(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    new = load_json_arg(args.entries)
    if isinstance(new, dict):
        new = [new]
    problems, clashes, added = [], [], []
    idx = index_terms(data["entries"], args.slug)
    for e in new:
        p = entry_problems(e)
        if p:
            problems.append({"term": e.get("term") if isinstance(e, dict) else None, "problems": p})
            continue
        hits = [x for x in match([e["term"]] + (e.get("aliases") or []), idx)
                if exact(x["term"]) not in {exact(d) for d in e.get("distinct_from") or []}]
        confirmed = e.pop("confirmed_distinct", False)
        if hits and not confirmed:
            clashes.append({"term": e["term"], "matches": hits})
            continue
        if confirmed:
            e["distinct_from"] = list(dict.fromkeys(e.get("distinct_from", []) + [x["term"] for x in hits]))
        ce = clean_entry(e)
        data["entries"].append(ce)
        idx += index_terms([ce], args.slug)
        added.append(ce["term"])
    if problems or clashes:
        if not args.partial:
            out({"ok": False, "error": "Nothing written: fix these entries or pass --partial.",
                 "invalid": problems, "duplicates": clashes,
                 "hint": "For a confirmed non-duplicate, add the other term to 'distinct_from' "
                         "or set \"confirmed_distinct\": true."}, 1)
    if added:
        save_list(h, data)
    out({"ok": True, "added": added, "skipped_invalid": problems, "skipped_duplicates": clashes,
         "count": len(data["entries"])})


def find_entry(data: dict, term: str) -> dict:
    for e in data["entries"]:
        if exact(e["term"]) == exact(term):
            return e
    hits = [e for e in data["entries"] if any(exact(a) == exact(term) for a in e.get("aliases", []))]
    if not hits:
        hits = [e for e in data["entries"] if norm(e["term"]) == norm(term)
                or norm(term) in {norm(a) for a in e.get("aliases", [])}]
    if len(hits) == 1:
        return hits[0]
    if hits:
        fail("Ambiguous term; use an exact stored name.", matches=[e["term"] for e in hits])
    fail(f"'{term}' is not in list '{data['slug']}'.")


def cmd_update(args):
    """Patch fields of existing entries. JSON: [{"term": "<existing term>", ...fields}]"""
    h = home_dir(args)
    data = load_list(h, args.slug)
    patches = load_json_arg(args.entries)
    if isinstance(patches, dict):
        patches = [patches]
    changed = []
    for p in patches:
        e = find_entry(data, p["term"])
        new_term = p.pop("rename_to", None)
        for k, v in p.items():
            if k == "term":
                continue
            if k in ("seen_in",) and isinstance(v, list) and args.append_seen:
                e.setdefault("seen_in", []).extend(v)
            else:
                e[k] = v
        if new_term:
            old_term = e["term"]
            e.setdefault("aliases", [])
            if exact(e["term"]) not in {exact(a) for a in e["aliases"]}:
                e["aliases"].append(e["term"])
            e["term"] = new_term
            for other in data["entries"]:
                for field in ("related_terms", "distinct_from"):
                    other[field] = [new_term if exact(t) == exact(old_term) else t for t in other.get(field, [])]
        probs = entry_problems(e)
        if probs:
            fail("Nothing written: invalid update.", problems=probs)
        e.update(clean_entry(e))
        probs = entry_problems(e)
        if probs:
            fail(f"Update would leave '{e['term']}' invalid.", problems=probs)
        changed.append(e["term"])
    save_list(h, data)
    out({"ok": True, "updated": changed})


def cmd_remove(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    gone = []
    for t in args.terms:
        e = find_entry(data, t)
        data["entries"].remove(e)
        gone.append(e["term"])
    for e in data["entries"]:
        e["related_terms"] = [r for r in e.get("related_terms") or [] if exact(r) not in {exact(g) for g in gone}]
    save_list(h, data)
    out({"ok": True, "removed": gone, "count": len(data["entries"])})


def cmd_merge(args):
    """Fold DROP into KEEP. KEEP's text wins unless --override supplies fields."""
    h = home_dir(args)
    data = load_list(h, args.slug)
    keep, drop = find_entry(data, args.keep), find_entry(data, args.drop)
    if keep is drop:
        fail("KEEP and DROP resolve to the same entry.")
    if args.override:
        for k, v in load_json_arg(args.override).items():
            if k != "term":
                keep[k] = v

    def union(a, b):
        seen, res = set(), []
        for x in (a or []) + (b or []):
            key = exact(x) if isinstance(x, str) else json.dumps(x, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                res.append(x)
        return res

    keep["aliases"] = [a for a in union(keep.get("aliases"), [drop["term"]] + (drop.get("aliases") or []))
                       if exact(a) != exact(keep["term"])]
    keep["related_terms"] = [r for r in union(keep.get("related_terms"), drop.get("related_terms"))
                             if exact(r) not in {exact(keep["term"]), exact(drop["term"])}]
    keep["distinct_from"] = [t for t in union(keep.get("distinct_from"), drop.get("distinct_from"))
                             if exact(t) not in {exact(keep["term"]), exact(drop["term"])}]
    fe_drop = drop.get("first_encountered") or {}
    keep["seen_in"] = union(keep.get("seen_in"), (drop.get("seen_in") or []) +
                            ([{"source": fe_drop.get("source", ""), "context": fe_drop.get("context", ""),
                               "date": str(fe_drop.get("date", ""))}] if fe_drop else []))
    if drop.get("notes"):
        keep["notes"] = (keep.get("notes") or "").rstrip() + f"\n(merged from {drop['term']}) {drop['notes']}".rstrip()
        keep["notes"] = keep["notes"].strip()
    data["entries"].remove(drop)
    for e in data["entries"]:
        e["related_terms"] = [keep["term"] if exact(r) == exact(drop["term"]) else r
                              for r in e.get("related_terms") or []]
    probs = entry_problems(keep)
    if probs:
        fail("Nothing written: invalid merge.", problems=probs)
    for other in data["entries"]:
        other["distinct_from"] = [keep["term"] if exact(t) == exact(drop["term"]) else t
                                  for t in other.get("distinct_from", [])]
    keep.update(clean_entry(keep))
    save_list(h, data)
    out({"ok": True, "kept": keep["term"], "dropped": drop["term"], "aliases": keep["aliases"]})


def cmd_move(args):
    """Move/copy once per identity, then reconcile same-list relationships."""
    h = home_dir(args)
    if args.slug == args.to:
        fail("Source and destination must be different lists.")
    src, dst = load_list(h, args.slug), load_list(h, args.to)
    idx, moved, clashes, selected = index_terms(dst["entries"], args.to), [], [], set()
    for t in args.terms:
        e = find_entry(src, t)
        key = exact(e["term"])
        if key in selected:
            continue
        selected.add(key)
        hits = match([e["term"]] + (e.get("aliases") or []), idx)
        hits = [hit for hit in hits if exact(hit["term"]) not in {exact(t) for t in e.get("distinct_from", [])}
                and not any(exact(other["term"]) == exact(hit["term"])
                            and exact(e["term"]) in {exact(t) for t in other.get("distinct_from", [])}
                            for other in dst["entries"])]
        if hits:
            clashes.append({"term": e["term"], "matches": hits})
            continue
        copied = copy.deepcopy(e)
        dst["entries"].append(copied)
        idx += index_terms([copied], args.to)
        moved.append(e["term"])
    if not args.copy:
        src["entries"] = [e for e in src["entries"] if e["term"] not in moved]
    removed_links = []
    if moved:
        changed = [dst] if args.copy else [dst, src]
        for data in changed:
            terms = {exact(e["term"]) for e in data["entries"]}
            for e in data["entries"]:
                missing = [t for t in e.get("related_terms", []) if exact(t) not in terms]
                if missing:
                    removed_links.append({"list": data["slug"], "term": e["term"], "related_terms": missing})
                    e["related_terms"] = [t for t in e["related_terms"] if exact(t) in terms]
        changes = {}
        for data in changed:
            changes.update(prepared_list(h, data))
        commit_files(h, changes)
        for data in changed:
            prune_backups(h, data["slug"])
    out({"ok": True, "moved" if not args.copy else "copied": moved, "skipped_duplicates": clashes,
         "removed_relationships": removed_links})


def cmd_undo(args):
    h = home_dir(args)
    load_list(h, args.slug)  # Refuse unsupported current formats, even for undo.
    pattern = storage_path(h, ".backups", f"{checked_slug(args.slug)}--*.yaml")
    backs = sorted(glob.glob(pattern))
    if not backs:
        fail(f"No backups for '{args.slug}'.")
    latest = backs[-1]
    original = file_bytes(latest)
    data = yaml.safe_load(original)
    if not isinstance(data, dict) or data.get("slug") != args.slug:
        fail("Backup identifier does not match the requested list.")
    changes = prepared_list(h, data, with_backup=False)
    changes[list_path(h, args.slug)] = original  # Restore original formatting exactly.
    changes[latest] = None
    if args.dry_run:
        out({"ok": True, "would_restore": latest, "backups_available": len(backs)})
    commit_files(h, changes, {latest: original})
    out({"ok": True, "restored": latest, "entries": len(data["entries"]), "backups_remaining": len(backs) - 1})


def cmd_validate(args):
    h = home_dir(args)
    slugs = [l["slug"] for l in all_lists(h)] if args.all or not args.slug else [args.slug]
    report = {}
    for slug in slugs:
        data = read_yaml(list_path(h, slug))
        issues = list_problems(data)
        if isinstance(data, dict) and data.get("slug") != slug:
            issues.append("stored slug does not match the filename")
        report[slug] = issues
    ok = all(not v for v in report.values())
    out({"ok": ok, "issues": report}, 0 if ok else 1)


# ------------------------------------------------------------------ render

_CODE = re.compile(r"[{};=]|->|\(\)|^\s*(func|let|var|actor|struct|class|enum|protocol|extension|import|"
                   r"await|async|try|return|if|for|guard|@\w+|#\w+|\$\w+|\w+\()", re.M)


def looks_like_code(ex: str) -> bool:
    """Fence an example as code unless it reads as a prose sentence."""
    if "\n" in ex:
        return True
    # A capitalized sentence of several words ending in . ! or ? is prose even if it quotes code.
    # [a-z]* rather than [a-z]+, so a sentence opening with "A" or "I" counts too.
    prose = bool(re.match(r"^[A-Z][a-z]*\b", ex)) and ex.rstrip().endswith((".", "!", "?")) and len(ex.split()) >= 5
    return bool(_CODE.search(ex)) and not prose


def render_md(data: dict) -> str:
    sc = data.get("scope") or {}
    ents = sorted(data.get("entries") or [], key=lambda e: sort_key(e["term"]))
    lines = [f"# {data.get('name')}: vocabulary", "",
             f"> {sc.get('subject', '')} · level: {sc.get('level', '')} · {len(ents)} terms · "
             f"generated from `{data.get('slug')}.yaml` on {today()}. Ask lexicon to edit this glossary; do not edit these generated files directly.", ""]
    if not ents:
        lines.append("_No terms yet._")
    letter = None
    for e in ents:
        first = (re.sub(r"^[^\w]+", "", e["term"]) or "#")[0].upper()
        if not first.isalpha():
            first = "#"
        if first != letter:
            letter = first
            lines += [f"## {letter}", ""]
        meta = [f"*{e.get('type', 'concept')}*"]
        if e.get("aliases"):
            meta.append("also: " + ", ".join(e["aliases"]))
        lines += [f"### {e['term']}", "", " · ".join(meta), "",
                  f"**Definition.** {e.get('definition', '').strip()}", ""]
        ex = (e.get("example") or "").strip()
        if looks_like_code(ex):
            lines += ["**Example.**", "", "```", ex, "```", ""]
        else:
            lines += [f"**Example.** {ex}", ""]
        lines += [f"**Use case.** {e.get('use_case', '').strip()}", ""]
        if e.get("related_terms"):
            lines += ["Related: " + ", ".join(e["related_terms"]), ""]
        fe = e.get("first_encountered") or {}
        prov = ", ".join(x for x in [fe.get("context"), fe.get("source"), str(fe.get("date") or "")] if x)
        if prov:
            lines += [f"<sub>First seen: {prov}</sub>", ""]
    return "\n".join(lines).rstrip() + "\n"


_HTML_CSS = """
:root{--bg:#fafaf7;--fg:#1c1c1a;--muted:#6b6b66;--line:#dcdcd4;--card:#fff;--accent:#2a5bd7;--chip:#eceee8;--mark:#fff2a8}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161616;--fg:#e8e8e4;--muted:#9a9a93;--line:#333330;--card:#1f1f1e;--accent:#8fb0ff;--chip:#2a2a28;--mark:#5a4d00}}
:root[data-theme=dark]{--bg:#161616;--fg:#e8e8e4;--muted:#9a9a93;--line:#333330;--card:#1f1f1e;--accent:#8fb0ff;--chip:#2a2a28;--mark:#5a4d00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:0 16px 64px}
header{padding:28px 0 12px}
h1{margin:0;font-size:1.6rem}
.sub{color:var(--muted);font-size:.9rem;margin-top:4px}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);z-index:5}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input[type=search]{flex:1;min-width:180px;padding:9px 12px;font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px}
button{font:inherit;font-size:.85rem;color:var(--fg);background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:7px 11px;cursor:pointer}
button[aria-pressed=true]{background:var(--accent);color:#fff;border-color:var(--accent)}
.chips{margin-top:8px}
.letters{margin-top:8px;display:flex;flex-wrap:wrap;gap:2px}
.letters a{padding:2px 7px;border-radius:6px;text-decoration:none;color:var(--accent);font-size:.9rem}
.letters a.off{color:var(--muted);opacity:.4;pointer-events:none}
.count{color:var(--muted);font-size:.85rem;margin:14px 0 4px}
h2{margin:26px 0 6px;font-size:1.1rem;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:4px;scroll-margin-top:130px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;scroll-margin-top:130px}
details:target{outline:2px solid var(--accent)}
summary{cursor:pointer;padding:10px 14px;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"\\25B8";color:var(--muted);margin-right:8px}
details[open]>summary::before{content:"\\25BE"}
.term{font-weight:600}
.badge{font-size:.72rem;background:var(--chip);border-radius:999px;padding:1px 8px;margin-left:8px;color:var(--muted)}
.gloss{display:block;color:var(--muted);font-size:.9rem;margin:2px 0 0 20px}
details[open] .gloss{display:none}
.body{padding:0 14px 12px 34px}
.body p{margin:8px 0}
.lbl{font-weight:600}
pre{background:var(--chip);padding:8px 10px;border-radius:6px;overflow-x:auto;font-size:.85rem;margin:4px 0}
.prov{color:var(--muted);font-size:.78rem;word-break:break-word}
a{color:var(--accent)}
mark{background:var(--mark);color:inherit;border-radius:2px}
.empty{color:var(--muted);padding:24px 0}
[hidden]{display:none!important}
@media print{.bar{display:none}details{break-inside:avoid}}
"""

_HTML_JS = """
(function(){
var q=document.getElementById('q'),count=document.getElementById('count'),
    entries=[].slice.call(document.querySelectorAll('details.entry')),
    secs=[].slice.call(document.querySelectorAll('section.letter')),
    links={},chips=[].slice.call(document.querySelectorAll('.chips button')),
    active={},tog=document.getElementById('toggle'),empty=document.getElementById('empty');
[].forEach.call(document.querySelectorAll('.letters a'),function(a){links[a.getAttribute('data-l')]=a});
function apply(){
  var t=q.value.trim().toLowerCase(),terms=t?t.split(/\\s+/):[],shown=0,anyType=Object.keys(active).length>0;
  entries.forEach(function(e){
    var h=e.getAttribute('data-s'),ok=terms.every(function(w){return h.indexOf(w)>-1});
    if(ok&&anyType&&!active[e.getAttribute('data-t')])ok=false;
    e.hidden=!ok;if(ok)shown++;
  });
  var has={};
  secs.forEach(function(s){
    var vis=s.querySelector('details.entry:not([hidden])');s.hidden=!vis;
    if(vis)has[s.getAttribute('data-l')]=1;
  });
  for(var l in links)links[l].classList.toggle('off',!has[l]);
  count.textContent=shown+' of '+entries.length+' terms';
  empty.hidden=shown>0;
}
q.addEventListener('input',apply);
chips.forEach(function(c){c.addEventListener('click',function(){
  var k=c.getAttribute('data-t'),on=!active[k];
  if(on)active[k]=1;else delete active[k];
  c.setAttribute('aria-pressed',on);apply();
})});
tog.addEventListener('click',function(){
  var open=tog.getAttribute('aria-pressed')!=='true';
  tog.setAttribute('aria-pressed',open);tog.textContent=open?'Collapse all':'Expand all';
  entries.forEach(function(e){if(!e.hidden)e.open=open});
});
document.addEventListener('keydown',function(e){
  if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus()}
  else if(e.key==='Escape'&&document.activeElement===q){q.value='';apply();q.blur()}
});
[].forEach.call(document.querySelectorAll('a.rel'),function(a){
  a.addEventListener('click',function(){
    var id=a.getAttribute('href').slice(1),d=document.getElementById(id);
    if(d){d.hidden=false;d.open=true}
  });
});
function hashOpen(){var d=location.hash&&document.getElementById(location.hash.slice(1));if(d&&d.tagName==='DETAILS')d.open=true}
window.addEventListener('hashchange',hashOpen);hashOpen();
window.addEventListener('beforeprint',function(){entries.forEach(function(e){e.open=true})});
apply();
})();
"""


def render_html(data: dict) -> str:
    """One self-contained HTML page per list: search, A-Z jump bar, type filter, expand/collapse,
    related-term links, light and dark mode. Everything is computed from the stored entries."""
    esc = lambda x: htmllib.escape(str(x), quote=True)
    sc = data.get("scope") or {}
    ents = sorted(data.get("entries") or [], key=lambda e: sort_key(e["term"]))
    ids, seen = {}, set()
    for e in ents:
        base = re.sub(r"[^a-z0-9]+", "-", e["term"].lower()).strip("-") or "term"
        cand, n = base, 2
        while cand in seen:
            cand, n = f"{base}-{n}", n + 1
        seen.add(cand)
        ids[e["term"]] = cand
    by_name = {}
    for e in ents:
        by_name[e["term"].lower()] = ids[e["term"]]
        for a in e.get("aliases") or []:
            by_name.setdefault(a.lower(), ids[e["term"]])
    types = sorted({(e.get("type") or "concept") for e in ents})

    def letter_of(t):
        first = (re.sub(r"^[^\w]+", "", t) or "#")[0].upper()
        return first if first.isalpha() else "#"

    letters = sorted({letter_of(e["term"]) for e in ents}, key=lambda c: (c == "#", c))
    nav = "".join(f'<a href="#sec-{"num" if l == "#" else l}" data-l="{esc(l)}">{esc(l)}</a>' for l in letters)
    chips = "".join(f'<button type="button" data-t="{esc(t)}" aria-pressed="false">{esc(t)}</button>' for t in types)

    body, cur = [], None
    for e in ents:
        l = letter_of(e["term"])
        if l != cur:
            if cur is not None:
                body.append("</section>")
            cur = l
            body.append(f'<section class="letter" data-l="{esc(l)}"><h2 id="sec-{"num" if l == "#" else esc(l)}">{esc(l)}</h2>')
        d = (e.get("definition") or "").strip()
        first = re.split(r"(?<=[.!?])\s", d, maxsplit=1)[0] if d else ""
        aliases = e.get("aliases") or []
        hay = " ".join([e["term"], *aliases, d, e.get("example") or "", e.get("use_case") or ""]).lower()
        typ = e.get("type") or "concept"
        ex = (e.get("example") or "").strip()
        ex_html = (f'<p><span class="lbl">Example.</span></p><pre>{esc(ex)}</pre>' if looks_like_code(ex)
                   else f'<p><span class="lbl">Example.</span> {esc(ex)}</p>')
        rel = []
        for r in e.get("related_terms") or []:
            tid = by_name.get(r.lower())
            rel.append(f'<a class="rel" href="#{tid}">{esc(r)}</a>' if tid else esc(r))
        fe = e.get("first_encountered") or {}
        prov = [esc(x) for x in (fe.get("context"), fe.get("source"), fe.get("date")) if x]
        parts = [f'<details class="entry" id="{ids[e["term"]]}" data-t="{esc(typ)}" data-s="{esc(hay)}">',
                 f'<summary><span class="term">{esc(e["term"])}</span><span class="badge">{esc(typ)}</span>'
                 f'<span class="gloss">{esc(first)}</span></summary><div class="body">']
        if aliases:
            parts.append(f'<p class="prov">also: {esc(", ".join(aliases))}</p>')
        parts.append(f'<p><span class="lbl">Definition.</span> {esc(d)}</p>')
        parts.append(ex_html)
        parts.append(f'<p><span class="lbl">Use case.</span> {esc((e.get("use_case") or "").strip())}</p>')
        if rel:
            parts.append(f'<p><span class="lbl">Related:</span> {", ".join(rel)}</p>')
        if prov:
            parts.append(f'<p class="prov">First seen: {", ".join(prov)}</p>')
        parts.append("</div></details>")
        body.append("".join(parts))
    if cur is not None:
        body.append("</section>")

    title = f"{data.get('name')}: vocabulary"
    sub = (f"{esc(sc.get('subject', ''))} &middot; level: {esc(sc.get('level', ''))} &middot; {len(ents)} terms "
           f"&middot; generated from <code>{esc(data.get('slug'))}.yaml</code> on {today()}. "
           "Ask lexicon to edit this glossary; do not edit this generated file directly.")
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)}</title><style>{_HTML_CSS}</style></head><body><div class=\"wrap\">"
            f"<header><h1>{esc(title)}</h1><div class=\"sub\">{sub}</div></header>"
            "<div class=\"bar\"><div class=\"row\">"
            "<input id=\"q\" type=\"search\" placeholder=\"Search terms, definitions, examples  (press / to focus)\" aria-label=\"Search\">"
            "<button id=\"toggle\" type=\"button\" aria-pressed=\"false\">Expand all</button></div>"
            f"<div class=\"chips row\" aria-label=\"Filter by type\">{chips}</div>"
            f"<nav class=\"letters\" aria-label=\"Jump to letter\">{nav}</nav></div>"
            "<div id=\"count\" class=\"count\"></div>"
            "<div id=\"empty\" class=\"empty\" hidden>No terms match.</div>"
            f"<main>{''.join(body) if body else '<p class=\"empty\">No terms yet.</p>'}</main>"
            f"</div><script>{_HTML_JS}</script></body></html>\n")


def render_to_file(h: str, data: dict):
    slug = checked_slug(data["slug"])
    commit_files(h, {storage_path(h, "lists", f"{slug}.md"): render_md(data).encode("utf-8"),
                     storage_path(h, "lists", f"{slug}.html"): render_html(data).encode("utf-8")})


def cmd_render(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    if args.stdout:
        sys.stdout.write(render_md(data))
        return
    render_to_file(h, data)
    out({"ok": True, "path": os.path.join(lists_dir(h), f"{args.slug}.md"),
         "html_path": os.path.join(lists_dir(h), f"{args.slug}.html")})


# --------------------------------------------------------- tutorial-creator

def to_tc(e: dict, list_name: str) -> dict:
    use = (e.get("use_case") or "").strip()
    ex = (e.get("example") or "").strip()
    if ex and "Example:" not in use:
        # Inline, the shape tutorial-creator's own entries use. Single-line code gets backticks.
        shown = f"`{ex}`" if looks_like_code(ex) and "\n" not in ex and "`" not in ex else ex
        use = f"{use} Example: {shown}" if use else f"Example: {shown}"
    fe = e.get("first_encountered") or {}
    src = fe.get("source", "") or ""
    is_file = bool(src) and "://" not in src and not src.lower().startswith("session")
    # tutorial-creator's regen-md files a new term under the VOCABULARY.md section whose heading
    # matches its first_encountered.context, creating one if needed, so every export lands under
    # "## External source". The lexicon provenance goes in notes.
    note = f"Exported from lexicon list \"{list_name}\" on {today()}."
    if fe.get("context"):
        note += f" First seen: {fe['context']}."
    if src and not is_file:
        note += f" Source: {src}."
    return {
        "term": e["term"],
        "type": e.get("type") or "concept",
        "definition": (e.get("definition") or "").strip(),
        "use_case": use,
        "first_encountered": {"source_file": src if is_file else "", "context": "external source",
                              "date": str(fe.get("date") or today())},
        "status": "new",
        "test_history": [],
        "applied_test_history": [],
        "related_terms": list(e.get("related_terms") or []),
        "notes": note,
    }


def clean_tc_notes(notes: str) -> str:
    """Drop tutorial-creator's lesson bookkeeping from a note: the `First seen: Day N` lead-in (the Day
    is already in first_encountered.context), the `Also seen in:` repeat-row line, and the
    `Per-Day wording` block. A source, merge history and free text stay."""
    out, in_block = [], False
    for line in notes.split("\n"):
        if in_block:
            if re.match(r"Day \d+(\.\d+)?:", line):
                continue
            in_block = False
        if line.startswith("Per-Day wording"):
            in_block = True
            continue
        if line.startswith("Also seen in:"):
            continue
        line = re.sub(r"^(First seen: Day \d+(?:\.\d+)?) -- Also seen in:.*$", r"\1", line)
        line = re.sub(r"^First seen: Day \d+(?:\.\d+)?\s*(?:--\s*)?", "", line)
        out.append(line.rstrip())
    while out and out[0] in ("", "---"):
        out.pop(0)
    while out and out[-1] in ("", "---"):
        out.pop()
    return "\n".join(out)


def from_tc(t: dict) -> dict:
    use = (t.get("use_case") or "").strip()
    ex = ""
    m = re.search(r"\bExample:\s*(.+)\Z", use, re.S)
    if m:
        ex = m.group(1).strip()
        if ex.startswith("`") and ex.endswith("`") and ex.count("`") == 2:
            ex = ex[1:-1]
        use = use[:m.start()].strip()
    fe = t.get("first_encountered") or {}
    return {
        "term": t.get("term"),
        "aliases": [],
        "type": t.get("type") or "concept",
        "definition": (t.get("definition") or "").strip(),
        "example": ex,
        "use_case": use,
        "related_terms": list(t.get("related_terms") or []),
        "first_encountered": {"source": fe.get("source_file", "") or "",
                              "context": f"imported from tutorial-creator; {fe.get('context', '')}".strip("; "),
                              "date": str(fe.get("date") or today())},
        "notes": clean_tc_notes(t["notes"].strip()) if isinstance(t.get("notes"), str) else "",
        "needs": [k for k, v in (("example", ex), ("use_case", use), ("definition", t.get("definition"))) if not v],
    }


def _view_key(s: str) -> str:
    """How tutorial-creator's regen-md matches a view row to a yaml term: ignoring case, backticks
    and ** bold. Deliberately not norm(), which also folds camelCase, sigils and plurals and would
    call `ViewModifier` and "View modifier" the same term."""
    return re.sub(r"\s+", " ", s.replace("`", "").replace("**", "")).strip().lower()


def view_drift(vocab: str, entries: list[dict]) -> dict:
    """Terms tutorial-creator's VOCABULARY.md shows that its vocabulary.yaml lacks.

    Such terms are invisible to vocab review and flashcards. tutorial-creator's regen-md asks
    about each one instead of erasing it (since tutorial-creator 2.1.0; earlier releases
    rebuilt the view from the yaml and erased them). Rows are matched to terms the way regen-md
    matches them, so this count agrees with the number it will ask about.
    """
    md = os.path.join(os.path.dirname(vocab), "VOCABULARY.md")
    if not os.path.exists(md):
        return {"view": None, "md_only_terms": 0, "examples": []}
    keys = {_view_key(e.get("term", "")) for e in entries if isinstance(e, dict)}
    section, only = "", []
    for line in Path(md).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        m = re.match(r"^\|\s*([^|]+?)\s*\|", line)
        if not m or "cumulative" in section:
            continue
        cell = m.group(1).strip()
        if re.match(r"^[-: ]+$", cell) or cell.lower() in ("term", "day", "#") or cell.isdigit():
            continue
        if _view_key(cell) and _view_key(cell) not in keys:
            only.append(cell)
    return {"view": md, "md_only_terms": len(only), "examples": only[:8]}


def cmd_export_tc(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    issues = list_problems(data)
    if issues:
        fail("Nothing written: source list is invalid.", issues=issues)
    vocab = args.vocab or default_tc_vocab(h)
    if not vocab or not os.path.exists(vocab):
        fail("Could not find tutorial-creator's vocabulary.yaml. Pass --vocab PATH "
             "or set it with: lexicon.py config --tc-vocab PATH")
    chosen = data["entries"] if args.all else [find_entry(data, t) for t in args.terms]
    if not chosen:
        fail("No terms selected. Pass terms or --all.")
    existing = tc_entries(vocab)
    idx = index_terms(existing, "tutorial-creator")
    to_write, skipped = [], []
    for e in chosen:
        hits = match([e["term"]] + (e.get("aliases") or []), idx)
        if hits:
            skipped.append({"term": e["term"], "already_there_as": [x["term"] for x in hits],
                            "tier": hits[0]["tier"]})
            continue
        rec = to_tc(e, data.get("name", data["slug"]))
        to_write.append(rec)
        idx += index_terms([rec], "tutorial-creator")
    if to_write:
        # Keep existing block-style bytes. An empty flow sequence has no entries to preserve.
        original = file_bytes(vocab)
        if (yaml.safe_load(original) or []) != existing:
            fail("Vocabulary changed during preparation; retry. Nothing changed.")
        node = yaml.compose(original)
        prefix = original
        if isinstance(node, yaml.SequenceNode) and node.flow_style:
            if existing:
                fail("Flow-style vocabulary is not appendable. Convert it to block-style with its owning tool first; nothing changed.")
            text = original.decode("utf-8")
            prefix = (text[:node.start_mark.index] + text[node.end_mark.index:]).encode("utf-8")
        chunk = ydump(to_write).encode("utf-8")
        planned = prefix + (b"\n" if prefix and not prefix.endswith(b"\n") else b"") + chunk
        after = yaml.safe_load(planned)
        if not isinstance(after, list) or after != existing + to_write:
            fail("Destination serialization does not support a safe append; nothing changed.")
    if args.dry_run or not to_write:
        out({"ok": True, "dry_run": bool(args.dry_run), "vocab": vocab,
             "view_drift": view_drift(vocab, existing),
             ("would_add" if args.dry_run else "added"): [r["term"] for r in to_write],
             "skipped_existing": skipped, "preview": to_write[:3]})
    marker = os.path.join(os.path.dirname(vocab), f"{os.path.basename(vocab)}.add-{stamp()}")
    if os.path.lexists(marker):
        fail("An undo marker already uses this timestamp. Retry in the next second; nothing changed.")
    for e in chosen:
        if e["term"] in {r["term"] for r in to_write}:
            e["exported_to_tc"] = today()
    # All potentially failing rendering and drift checks precede writes.
    changes = {vocab: planned, marker: ("\n".join(r["term"] for r in to_write) + "\n").encode("utf-8")}
    changes.update(prepared_list(h, data))
    drift = view_drift(vocab, after)
    commit_files(h, changes, {vocab: original, marker: None})
    prune_backups(h, data["slug"])
    if drift["view"] is None:
        nxt = ("There is no VOCABULARY.md beside that yaml yet: run `/tutorial-creator vocab regen-md` to "
               "build one from it, exported terms included. ")
    else:
        nxt = ("Run `/tutorial-creator vocab regen-md` to bring VOCABULARY.md in step with the yaml. The exported "
               "terms get rows; if the view has drifted from the yaml, it also fixes other rows and rebuilds "
               "the Cumulative Count. ")
    if drift["md_only_terms"]:
        nxt += (f"It will also ask about {drift['md_only_terms']} term{'' if drift['md_only_terms'] == 1 else 's'} the view shows that "
                "vocabulary.yaml lacks: keep them or add them to the yaml, since removing one loses it. "
                "(A tutorial-creator older than 2.1.0 erases those terms on a regen instead; "
                "update it first.) ")
    nxt += ("To reverse this export within 24h: `/tutorial-creator vocab undo`. (Installed as a plugin? "
            "Type `/tutorial-creator:tutorial-creator` instead of `/tutorial-creator`.)")
    out({"ok": True, "vocab": vocab, "added": [r["term"] for r in to_write], "skipped_existing": skipped,
         "undo_marker": marker, "view_drift": drift, "next": nxt})


def cmd_import_tc(args):
    h = home_dir(args)
    vocab = args.vocab or default_tc_vocab(h)
    if not vocab or not os.path.exists(vocab):
        fail("Could not find tutorial-creator's vocabulary.yaml. Pass --vocab PATH.")
    rows = tc_entries(vocab)
    if args.type:
        rows = [r for r in rows if (r.get("type") or "") in set(args.type)]
    if args.match:
        rx = re.compile(args.match, re.I)
        rows = [r for r in rows if rx.search(str(r.get("term", ""))) or rx.search(str(r.get("definition", "")))]
    if args.since:
        rows = [r for r in rows if str((r.get("first_encountered") or {}).get("date", "")) >= args.since]
    mapped = [from_tc(r) for r in rows]
    if args.limit:
        mapped = mapped[: args.limit]
    out({"ok": True, "vocab": vocab, "total_in_source": len(tc_entries(vocab)), "selected": len(mapped),
         "entries": mapped,
         "note": "Nothing written. Route each entry to a list, fill anything in 'needs', "
                 "run dupes, then add. Learning state (status, test_history) stays in tutorial-creator."})


# ------------------------------------------------------------------ sources

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def _blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(parts)


def _local_date(ts) -> str | None:
    """Local calendar date of an ISO-8601 timestamp (transcripts write UTC, e.g. 2026-09-20T10:00:00Z)."""
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (AttributeError, ValueError):
        return None


def cmd_session_text(args):
    path = os.path.expanduser(args.path)
    if not os.path.exists(path):
        fail(f"No transcript at {path}")
    chunks, stamps = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = rec.get("type")
            if role not in ("user", "assistant") or rec.get("isMeta"):
                continue
            msg = rec.get("message") or {}
            text = _REMINDER.sub("", _blocks_text(msg.get("content"))).strip()
            if not text or text.startswith("<command-") or text.startswith("<local-command"):
                continue
            chunks.append(f"{role.upper()}: {text}")
            if rec.get("timestamp"):
                stamps.append(rec["timestamp"])
    text = "\n\n".join(chunks)
    truncated = len(text) > args.max_chars
    if truncated:
        text = text[: args.max_chars]
    if args.raw:
        sys.stdout.write(text)
        return
    first, last = (stamps[0], stamps[-1]) if stamps else (None, None)
    out({"ok": True, "path": path, "chars": len(text), "truncated": truncated, "turns": len(chunks),
         "date": _local_date(first), "first_timestamp": first, "last_timestamp": last, "text": text})


def cmd_sessions(args):
    base = os.path.expanduser("~/.claude/projects")
    files = sorted(glob.glob(os.path.join(base, "*", "*.jsonl")), key=os.path.getmtime, reverse=True)
    res = []
    for p in files[: args.limit]:
        first = ""
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    rec = json.loads(line)
                    if rec.get("type") == "user" and not rec.get("isMeta"):
                        t = _REMINDER.sub("", _blocks_text((rec.get("message") or {}).get("content"))).strip()
                        if t and not t.startswith("<"):
                            first = t[:120]
                            break
        except Exception:
            pass
        res.append({"path": p, "project": os.path.basename(os.path.dirname(p)),
                    "modified": dt.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="minutes"),
                    "first_prompt": first})
    out({"ok": True, "sessions": res})


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "pre", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.skip, self.title, self._in_title = [], [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a":
            self.parts.append(" ")
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if not self.skip:
            self.parts.append(data)


def cmd_html_text(args):
    from urllib.parse import urljoin, urlparse, urldefrag
    raw = sys.stdin.read() if args.path == "-" else Path(os.path.expanduser(args.path)).read_text(encoding="utf-8", errors="replace")
    p = _Text()
    p.feed(raw)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    links, same = [], []
    for href in p.links:
        if href.startswith(("mailto:", "javascript:", "tel:")):
            continue
        full = urldefrag(urljoin(args.base, href))[0] if args.base else href
        if full not in links:
            links.append(full)
    if args.base:
        host = urlparse(args.base).netloc
        same = [l for l in links if urlparse(l).netloc == host
                and not re.search(r"\.(png|jpe?g|gif|svg|pdf|zip|css|js|ico|webp|mp4)$", l, re.I)]
    out({"ok": True, "title": p.title.strip(), "chars": len(text), "text": text[: args.max_chars],
         "truncated": len(text) > args.max_chars, "links": links[:200], "same_site_links": same[:200]})


def cmd_norm(args):
    out({"ok": True, "forms": {t: norm(t) for t in args.terms}})


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="lexicon helper: vocabulary lists")
    ap.add_argument("--home", help="lexicon home (default $LEXICON_HOME or ~/.claude/lexicon)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("init").set_defaults(fn=cmd_init)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    c = sp.add_parser("config"); c.add_argument("--default-list"); c.add_argument("--tc-vocab"); c.set_defaults(fn=cmd_config)
    c = sp.add_parser("new-list"); c.add_argument("--name", required=True); c.add_argument("--slug")
    c.add_argument("--scope", help="JSON file or - (subject, level, include, exclude, known_terms, notes)"); c.set_defaults(fn=cmd_new_list)
    c = sp.add_parser("set-scope"); c.add_argument("slug"); c.add_argument("--scope", required=True); c.add_argument("--rename"); c.set_defaults(fn=cmd_set_scope)
    sp.add_parser("lists").set_defaults(fn=cmd_lists)
    c = sp.add_parser("show"); c.add_argument("slug"); c.add_argument("term", nargs="?"); c.set_defaults(fn=cmd_show)
    c = sp.add_parser("dupes"); c.add_argument("--candidates", required=True, help='JSON file or -: [{"term":..,"aliases":[..]}]')
    c.add_argument("--list"); c.add_argument("--all", action="store_true"); c.add_argument("--tc", "--vocab", dest="tc"); c.add_argument("--with-tc", action="store_true"); c.set_defaults(fn=cmd_dupes)
    c = sp.add_parser("audit"); c.add_argument("slug", nargs="?"); c.add_argument("--all", action="store_true"); c.set_defaults(fn=cmd_audit)
    c = sp.add_parser("add"); c.add_argument("slug"); c.add_argument("--entries", required=True); c.add_argument("--partial", action="store_true"); c.set_defaults(fn=cmd_add)
    c = sp.add_parser("update"); c.add_argument("slug"); c.add_argument("--entries", required=True); c.add_argument("--append-seen", action="store_true"); c.set_defaults(fn=cmd_update)
    c = sp.add_parser("remove"); c.add_argument("slug"); c.add_argument("terms", nargs="+"); c.set_defaults(fn=cmd_remove)
    c = sp.add_parser("merge"); c.add_argument("slug"); c.add_argument("keep"); c.add_argument("drop"); c.add_argument("--override"); c.set_defaults(fn=cmd_merge)
    c = sp.add_parser("move"); c.add_argument("slug"); c.add_argument("terms", nargs="+"); c.add_argument("--to", required=True); c.add_argument("--copy", action="store_true"); c.set_defaults(fn=cmd_move)
    c = sp.add_parser("undo"); c.add_argument("slug"); c.add_argument("--dry-run", action="store_true"); c.set_defaults(fn=cmd_undo)
    c = sp.add_parser("validate"); c.add_argument("slug", nargs="?"); c.add_argument("--all", action="store_true"); c.set_defaults(fn=cmd_validate)
    c = sp.add_parser("render"); c.add_argument("slug"); c.add_argument("--stdout", action="store_true"); c.set_defaults(fn=cmd_render)
    c = sp.add_parser("export-tc"); c.add_argument("slug"); c.add_argument("terms", nargs="*"); c.add_argument("--all", action="store_true")
    c.add_argument("--vocab"); c.add_argument("--dry-run", action="store_true"); c.set_defaults(fn=cmd_export_tc)
    c = sp.add_parser("import-tc"); c.add_argument("--vocab"); c.add_argument("--type", nargs="*"); c.add_argument("--match")
    c.add_argument("--since"); c.add_argument("--limit", type=int); c.set_defaults(fn=cmd_import_tc)
    c = sp.add_parser("session-text"); c.add_argument("path"); c.add_argument("--max-chars", type=int, default=400_000)
    c.add_argument("--raw", action="store_true"); c.set_defaults(fn=cmd_session_text)
    c = sp.add_parser("sessions"); c.add_argument("--limit", type=int, default=15); c.set_defaults(fn=cmd_sessions)
    c = sp.add_parser("html-text"); c.add_argument("path", help="HTML file or -"); c.add_argument("--base", help="page URL, for resolving links")
    c.add_argument("--max-chars", type=int, default=200_000); c.set_defaults(fn=cmd_html_text)
    c = sp.add_parser("norm"); c.add_argument("terms", nargs="+"); c.set_defaults(fn=cmd_norm)

    args = ap.parse_args()
    mutators = {"init", "config", "new-list", "set-scope", "add", "update", "remove", "merge",
                "move", "undo", "render", "export-tc"}
    writing = args.cmd in mutators and not getattr(args, "dry_run", False) and not getattr(args, "stdout", False)
    try:
        with contextlib.ExitStack() as stack:
            h = home_dir(args)
            if writing:
                os.makedirs(h, exist_ok=True)
                stack.enter_context(writer_lock(storage_path(h, "", ".write-lock")))
                recovery_root = storage_path(h, "", ".recovery")
                if os.path.isdir(recovery_root) and os.listdir(recovery_root):
                    fail(f"An interrupted write needs recovery; inspect {recovery_root} before making changes.")
                read_config(h)
                if args.cmd == "export-tc":
                    vocab = args.vocab or default_tc_vocab(h)
                    if vocab and os.path.isfile(vocab):
                        args.vocab = os.path.abspath(vocab)
                        stack.enter_context(writer_lock(args.vocab + ".lexicon-lock"))
            args.fn(args)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, yaml.YAMLError, RuntimeError) as ex:
        fail(str(ex))


if __name__ == "__main__":
    main()
