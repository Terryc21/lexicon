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
import datetime as dt
import glob
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
    raw = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
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


def list_path(h: str, slug: str) -> str:
    return os.path.join(lists_dir(h), f"{slug}.yaml")


def config_path(h: str) -> str:
    return os.path.join(h, "config.yaml")


def read_yaml(path: str):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_config(h: str) -> dict:
    p = config_path(h)
    if not os.path.exists(p):
        return {}
    return read_yaml(p) or {}


def write_atomic(path: str, text: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def backup(h: str, slug: str):
    src = list_path(h, slug)
    if not os.path.exists(src):
        return None
    bdir = os.path.join(h, ".backups")
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, f"{slug}--{stamp()}-{os.getpid()}.yaml")
    shutil.copy2(src, dst)
    olds = sorted(glob.glob(os.path.join(bdir, f"{slug}--*.yaml")))
    for o in olds[:-BACKUPS_KEPT]:
        os.remove(o)
    return dst


def load_list(h: str, slug: str) -> dict:
    p = list_path(h, slug)
    if not os.path.exists(p):
        fail(f"No list named '{slug}'.", available=[l["slug"] for l in all_lists(h)])
    data = read_yaml(p) or {}
    data.setdefault("entries", [])
    return data


def save_list(h: str, data: dict):
    slug = data["slug"]
    backup(h, slug)
    data["entries"] = sorted(data.get("entries", []), key=lambda e: sort_key(e.get("term", "")))
    write_atomic(list_path(h, slug), header_comment(data) + ydump(data))
    render_to_file(h, data)


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
            "slug": d.get("slug"),
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
    probs = []
    for k in REQUIRED_TEXT:
        v = e.get(k)
        if not isinstance(v, str) or not v.strip():
            probs.append(f"'{k}' is missing or empty")
    for k in ("aliases", "related_terms", "distinct_from", "seen_in"):
        if k in e and not isinstance(e[k], list):
            probs.append(f"'{k}' must be a list")
    return probs


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
    data = read_yaml(path) or []
    if not isinstance(data, list):
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
    save_list(h, data)
    cfg = read_config(h)
    if not cfg.get("default_list"):
        cfg.setdefault("schema_version", SCHEMA_VERSION)
        cfg["default_list"] = slug
        write_atomic(config_path(h), ydump(cfg))
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
            problems.append({"term": e.get("term"), "problems": p})
            continue
        hits = [x for x in match([e["term"]] + (e.get("aliases") or []), idx)
                if exact(x["term"]) not in {exact(d) for d in e.get("distinct_from") or []}]
        if hits and not e.pop("confirmed_distinct", False):
            clashes.append({"term": e["term"], "matches": hits})
            continue
        e.pop("confirmed_distinct", None)
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
    for e in data["entries"]:
        if norm(e["term"]) == norm(term) or norm(term) in {norm(a) for a in e.get("aliases") or []}:
            return e
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
            e.setdefault("aliases", [])
            if exact(e["term"]) not in {exact(a) for a in e["aliases"]}:
                e["aliases"].append(e["term"])
            e["term"] = new_term
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
    keep.update(clean_entry(keep))
    save_list(h, data)
    out({"ok": True, "kept": keep["term"], "dropped": drop["term"], "aliases": keep["aliases"]})


def cmd_move(args):
    """Move or copy entries to another list (e.g. after re-scoping)."""
    h = home_dir(args)
    src, dst = load_list(h, args.slug), load_list(h, args.to)
    idx, moved, clashes = index_terms(dst["entries"], args.to), [], []
    for t in args.terms:
        e = find_entry(src, t)
        hits = match([e["term"]] + (e.get("aliases") or []), idx)
        if hits:
            clashes.append({"term": e["term"], "matches": hits})
            continue
        dst["entries"].append(json.loads(json.dumps(e, default=str)))
        if not args.copy:
            src["entries"].remove(e)
        moved.append(e["term"])
    if moved:
        save_list(h, dst)
        if not args.copy:
            save_list(h, src)
    out({"ok": True, "moved" if not args.copy else "copied": moved, "skipped_duplicates": clashes})


def cmd_undo(args):
    h = home_dir(args)
    bdir = os.path.join(h, ".backups")
    backs = sorted(glob.glob(os.path.join(bdir, f"{args.slug}--*.yaml")))
    if not backs:
        fail(f"No backups for '{args.slug}'.")
    latest = backs[-1]
    if args.dry_run:
        out({"ok": True, "would_restore": latest, "backups_available": len(backs)})
    cur = list_path(h, args.slug)
    shutil.copy2(latest, cur)
    os.remove(latest)
    data = read_yaml(cur)
    render_to_file(h, data)
    out({"ok": True, "restored": latest, "entries": len(data.get("entries") or []),
         "backups_remaining": len(backs) - 1})


def cmd_validate(args):
    h = home_dir(args)
    slugs = [l["slug"] for l in all_lists(h)] if args.all or not args.slug else [args.slug]
    report = {}
    for s in slugs:
        data = load_list(h, s)
        issues = []
        if data.get("schema_version") != SCHEMA_VERSION:
            issues.append(f"schema_version is {data.get('schema_version')}, expected {SCHEMA_VERSION}")
        if (data.get("scope") or {}).get("level") not in LEVELS:
            issues.append("scope.level missing or invalid")
        terms = {exact(e.get("term", "")) for e in data["entries"]}
        for e in data["entries"]:
            for p in entry_problems(e):
                issues.append(f"{e.get('term')}: {p}")
            for r in e.get("related_terms") or []:
                if exact(r) not in terms:
                    issues.append(f"{e.get('term')}: related term '{r}' is not in this list (dangling)")
        report[s] = issues
    out({"ok": all(not v for v in report.values()), "issues": report})


# ------------------------------------------------------------------ render

_CODE = re.compile(r"[{};=]|->|\(\)|^\s*(func|let|var|actor|struct|class|enum|protocol|extension|import|"
                   r"await|async|try|return|if|for|guard|@\w+|#\w+|\$\w+|\w+\()", re.M)


def looks_like_code(ex: str) -> bool:
    """Fence an example as code unless it reads as a prose sentence."""
    if "\n" in ex:
        return True
    # A capitalized sentence of several words ending in . ! or ? is prose even if it quotes code.
    prose = bool(re.match(r"^[A-Z][a-z]+\b", ex)) and ex.rstrip().endswith((".", "!", "?")) and len(ex.split()) >= 5
    return bool(_CODE.search(ex)) and not prose


def render_md(data: dict) -> str:
    sc = data.get("scope") or {}
    ents = sorted(data.get("entries") or [], key=lambda e: sort_key(e["term"]))
    lines = [f"# {data.get('name')}: vocabulary", "",
             f"> {sc.get('subject', '')} · level: {sc.get('level', '')} · {len(ents)} terms · "
             f"generated from `{data.get('slug')}.yaml` on {today()}. Edit the YAML (or ask lexicon), not this file.", ""]
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


def render_to_file(h: str, data: dict):
    write_atomic(os.path.join(lists_dir(h), f"{data['slug']}.md"), render_md(data))


def cmd_render(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
    if args.stdout:
        sys.stdout.write(render_md(data))
        return
    render_to_file(h, data)
    out({"ok": True, "path": os.path.join(lists_dir(h), f"{args.slug}.md")})


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
        "notes": (t.get("notes") or "").strip() if isinstance(t.get("notes"), str) else "",
        "needs": [k for k, v in (("example", ex), ("use_case", use), ("definition", t.get("definition"))) if not v],
    }


def view_drift(vocab: str, entries: list[dict]) -> dict:
    """Terms tutorial-creator's VOCABULARY.md shows that its vocabulary.yaml lacks.

    Such terms are invisible to vocab review and flashcards. tutorial-creator's regen-md asks
    about each one instead of erasing it (since its 09/28/2026 in-place update; earlier releases
    rebuilt the view from the yaml and erased them).
    """
    md = os.path.join(os.path.dirname(vocab), "VOCABULARY.md")
    if not os.path.exists(md):
        return {"view": None, "md_only_terms": 0, "examples": []}
    keys = {norm(e.get("term", "")) for e in entries if isinstance(e, dict)}
    section, only = "", []
    for line in open(md, encoding="utf-8", errors="replace"):
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        m = re.match(r"^\|\s*([^|]+?)\s*\|", line)
        if not m or "cumulative" in section:
            continue
        cell = m.group(1).strip()
        if re.match(r"^[-: ]+$", cell) or cell.lower() in ("term", "day", "#") or cell.isdigit():
            continue
        if norm(cell) and norm(cell) not in keys:
            only.append(cell)
    return {"view": md, "md_only_terms": len(only), "examples": only[:8]}


def cmd_export_tc(args):
    h = home_dir(args)
    data = load_list(h, args.slug)
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
    if args.dry_run or not to_write:
        out({"ok": True, "dry_run": bool(args.dry_run), "vocab": vocab,
             "view_drift": view_drift(vocab, existing),
             ("would_add" if args.dry_run else "added"): [r["term"] for r in to_write],
             "skipped_existing": skipped, "preview": to_write[:3]})
    # Append-only: the existing file is left byte-for-byte intact. Re-dumping it
    # would reformat hundreds of hand-curated entries.
    original = open(vocab, "rb").read()
    chunk = ydump(to_write)
    with open(vocab, "ab") as f:
        if original and not original.endswith(b"\n"):
            f.write(b"\n")
        f.write(chunk.encode("utf-8"))
    try:
        after = tc_entries(vocab)
        assert len(after) == len(existing) + len(to_write)
    except Exception as ex:  # roll back to the exact original bytes
        with open(vocab, "wb") as f:
            f.write(original)
        fail(f"Append produced an unreadable file; rolled back. ({ex})")
    marker = os.path.join(os.path.dirname(vocab), f"{os.path.basename(vocab)}.add-{stamp()}")
    write_atomic(marker, "\n".join(r["term"] for r in to_write) + "\n")
    for e in chosen:
        if e["term"] in {r["term"] for r in to_write}:
            e["exported_to_tc"] = today()
    save_list(h, data)
    drift = view_drift(vocab, after)
    nxt = "Run `/tutorial-creator vocab regen-md` to add the exported terms to VOCABULARY.md. "
    if drift["md_only_terms"]:
        nxt += (f"It will also ask about {drift['md_only_terms']} terms the view shows that vocabulary.yaml "
                "lacks: keep them or add them to the yaml, since removing one loses it. (A tutorial-creator "
                "older than 09/28/2026 erases those terms on a regen instead; update it first.) ")
    nxt += "To reverse this export within 24h: `/tutorial-creator vocab undo`."
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


def cmd_session_text(args):
    path = os.path.expanduser(args.path)
    if not os.path.exists(path):
        fail(f"No transcript at {path}")
    chunks = []
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
    text = "\n\n".join(chunks)
    truncated = len(text) > args.max_chars
    if truncated:
        text = text[: args.max_chars]
    if args.raw:
        sys.stdout.write(text)
        return
    out({"ok": True, "path": path, "chars": len(text), "truncated": truncated, "turns": len(chunks), "text": text})


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
    raw = sys.stdin.read() if args.path == "-" else open(os.path.expanduser(args.path), encoding="utf-8",
                                                          errors="replace").read()
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
    c.add_argument("--list"); c.add_argument("--all", action="store_true"); c.add_argument("--tc"); c.add_argument("--with-tc", action="store_true"); c.set_defaults(fn=cmd_dupes)
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
    args.fn(args)


if __name__ == "__main__":
    main()
