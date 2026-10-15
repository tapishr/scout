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
