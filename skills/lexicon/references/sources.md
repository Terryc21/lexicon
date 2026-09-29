# Getting text out of each source

Whatever the source, end up with plain text plus a short description you can show the user
("this session, 136 turns", "3 pages from example.com, 41k chars") and use as `first_encountered.source`.

## This session

The conversation is already in your context, so there is nothing to fetch. Scan what you can see.
If the session is long and early turns may have been summarized away, say that the scan covers
what is currently in context, or use the transcript file (next section) for the full record.
Source label: `session transcript`, with the project and date in `context`.

## A past session

Claude Code stores transcripts as JSONL under `~/.claude/projects/<project-dir>/<session-id>.jsonl`.

```bash
python3 "$S" sessions --limit 15          # recent transcripts: path, project, time, first prompt
python3 "$S" session-text <path.jsonl>    # user + assistant text only; tool calls and reminders dropped
```

`session-text` also returns `date` (the local calendar date of the session's first turn),
`first_timestamp` and `last_timestamp`. Use `date` as `first_encountered.date` for every term taken
from that session. `add` stamps today's date when none is given, which is wrong for last week's
session. (`date` is null when the transcript has no timestamps; then ask the user.) For the session
you are in now, today is right.

Let the user pick from `sessions` when they say "my last session" or "the session where I...".
`session-text` truncates at 400k characters by default (`--max-chars`). If it reports
`truncated: true`, say so and offer to scan the rest.

## Local files

- Plain text, Markdown, and source code: read them directly.
- **PDF**: the Read tool reads PDFs. For long ones, pass page ranges and tell the user which pages
  you covered.
- **Word (.docx), RTF, Pages**: on macOS, `textutil -convert txt -stdout "<file>"` prints the text.
  If that fails, a docx skill may be available.
- **Local HTML**: `python3 "$S" html-text "<file>.html"` strips scripts and styles, and returns the
  text plus the links.
- **A folder**: ask which files, or propose a glob (for example `docs/**/*.md`) and state how many
  files it matches before reading them.

## A web page

Use WebFetch on the URL and ask it for the page's full main text. If WebFetch is unavailable, or
you need exact text or the link list, fetch the raw HTML and extract it:

```bash
curl -sL --max-time 20 "<url>" | python3 "$S" html-text - --base "<url>"
```

If a page is paywalled, needs a login, or comes back nearly empty (a JavaScript-rendered app), say
so plainly. Never guess at its content.

## A whole site

Crawling multiplies cost and noise, so it is always opt-in and capped.

1. Start from the URL the user gave. Get its same-site links (`html-text --base` returns
   `same_site_links`, or ask WebFetch to list the page's internal links).
2. Propose a plan before fetching more: the page cap (default **10**, never more than 50 without
   an explicit yes), and which links you will follow. Prefer docs, glossary, guide, tutorial, and
   reference pages. Skip login, cart, tag-archive, and pagination pages.
3. Fetch breadth-first, stay on the same host, never fetch a page twice, and respect an obvious
   "no crawling" signal (robots.txt disallowing the path, rate-limit responses). Stop at the cap.
4. Report which pages were read and which failed. Record each term's page URL as its `source`,
   not just the site root.

## Pasted text

Treat it as already fetched. If the user says where it came from, record that as the source;
otherwise use `pasted text`. Never invent a URL for it. For very long pastes (roughly over 50k
characters), suggest saving them to a file instead.
