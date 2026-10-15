# PROGRESS.md — append-only work log

Chronological record of what was done, what worked, what failed, and open questions.
Newest entries at the bottom. For the *current state of truth*, read `CONTEXT.md`.

---

## 2026-10-14 — Session 1 (Claude Opus 5)

### Setup
- Working dir: `/private/tmp/claude-501/-Users-apple-Code/6c736129-54d4-46aa-a13f-f3b9f9d208d3/scratchpad/scout`
- Confirmed **not** touching `~/Code/job-hunt` or `~/Code/kavi`.
- Python 3.12.9, `requests` 2.33.1 already installed — no install needed.
- `gh auth status` confirms the documented credential problem up front:
  logged in as **`tapishr-kpro`** (not `tapishr`), token scopes
  `admin:public_key, gist, read:org, repo`, git protocol ssh.
  Plan per instructions: build + commit locally, attempt push **once**, stop on failure.

### Wrote CONTEXT.md first, before any code
Per the process requirement. Contains purpose, the verified API facts, design
decisions with rationale (three buckets, JSON-over-SQLite, config-driven, politeness),
build state, and a "PICK UP HERE" section.

## 2026-10-15 — email-alert source added (main session)

Added `fetchers/email_alerts.py` (211 lines) reading Indeed and LinkedIn job-alert
mail over IMAP, after confirming neither site may be crawled: Indeed's robots.txt
has `Disallow: /jobs/IN/` under `User-agent: *` plus `Disallow: /*?rss` (and the RSS
endpoint 403s); LinkedIn UA §8.2 forbids automated access. Reading alert mail the
user asked for sidesteps both — first-party data, no bot detection, nothing to
repair when a page template changes.

Two real bugs found by the tests, both fixed:
- A missing or malformed `Date:` header raised `ValueError` out of
  `parsedate_to_datetime` and lost the entire email. Now costs the age, not the post.
- `_text()` collapsed every whitespace run, erasing the block boundary that company
  and location extraction depends on — alerts render them as adjacent `<div>`s.
  Added `_blocks()`, which marks block-level ends before tags are stripped.

Disabled by default in config. 107 tests passing, ruff clean.

### Still open
- The `suspicious` bucket is ~444 of 450 on a live run. All of it comes from the
  five aggregators; every genuinely useful hit so far came from the ATS sources and
  Atlassian's endpoint. Tune aggregator filtering before adding any new source.
- Not pushed: this machine's SSH key is `tapishr-kpro`, which cannot create or push
  to repos under `tapishr`. Needs a PAT with `repo` scope in `gh_token=` in ~/Code/.env.
