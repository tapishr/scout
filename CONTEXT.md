# CONTEXT.md — handoff document for `scout`

> **Read this first.** This file exists so that a fresh Claude session (or a human)
> with zero prior knowledge of this project can pick it up and continue without
> re-deriving anything. It is maintained as the project changes, not written at the end.
>
> Companion file: `PROGRESS.md` is the append-only work log (what was tried, what
> worked, what failed). This file is the *current state of truth*.

---

## 1. What this is and why it exists

`scout` is a job-discovery tool. It polls **public, unauthenticated job-board APIs**
once a day, filters for **remote senior engineering roles genuinely open to someone
living in India**, diffs the results against the previous run, and emits a markdown
digest of **only what is new** (plus what disappeared).

### The problem it solves

The operator lives in **Mumbai, India**, **will not relocate**, and treats Bengaluru
as an explicit last choice. The hard requirement is roles open to someone *living in
India, working remotely*.

The recurring failure mode in this job search — the thing this tool exists to fix —
is that **job postings routinely look open to India and are not**. A posting says
"Remote" with no country; or says "Remote" and means "Remote (US)"; or lists India
remote but attaches a Bengaluru office so the employer can pull you onsite later.
Manually re-reading the same boards every morning and re-triaging the same postings
is the time sink.

Two of the operator's five live applications had their requisitions **pulled**
without notice. So disappearance is signal, and the tool reports it.

### The design stance that follows from that

**The tool never auto-rejects on a keyword.** Its job is to *classify and surface*
for human reading, not to decide. A posting it cannot confidently classify goes into
a `suspicious` bucket that is still printed — just lower in the digest. Silently
dropping an ambiguous posting is the one failure this tool must not have, because
that is exactly the failure the operator already has without it.

### Explicitly out of scope — do not add these

- **No LinkedIn scraping or automation of any kind.** LinkedIn's User Agreement §8.2
  prohibits it, and the operator's LinkedIn network is an asset he will not risk.
  This is a hard line, not a "not yet".
- **No auto-applying.** The tool finds and reports; a human reads and decides.
- No authenticated/paid APIs. Everything here is open and keyless, by design — see §4.

---

## 2. Quick orientation for a new session

```
scout/
  CONTEXT.md          <- you are here: state of truth + handoff
  PROGRESS.md         <- append-only work log
  README.md           <- public-facing: problem, install, example output
  LICENSE             <- MIT
  pyproject.toml      <- deps + tooling config; installs console script `scout`
  config.yaml         <- ALL tuning lives here: companies, keywords, buckets
  src/scout/
    __init__.py
    cli.py            <- argparse entrypoint, wires everything together
    config.py         <- typed config loading/validation (dataclasses)
    models.py         <- the Job dataclass; the one shape everything speaks
    http.py           <- the single polite HTTP helper (timeout/retry/delay)
    fetchers/         <- one module per API; each returns list[Job]
      __init__.py     <- SOURCES registry
      ashby.py  greenhouse.py  workable.py  smartrecruiters.py
      atlassian.py  aggregators.py
    filters.py        <- seniority/engineering match + India-eligibility buckets
    store.py          <- JSON snapshot persistence + diff
    digest.py         <- markdown rendering
  tests/
    fixtures/         <- captured API payloads, trimmed; no live network in tests
    test_filters.py  test_store.py  test_digest.py  test_fetchers.py  test_config.py
```

Run it:

```bash
pip install -e ".[dev]"
scout run                 # fetch, diff against last run, print digest
scout run --dry-run       # fetch and print, do NOT write a snapshot
scout run --all           # ignore diff, report everything currently matching
scout run --source ashby  # restrict to one source (repeatable)
scout history             # list stored snapshots
pytest                    # offline tests
```

---

## 3. Data flow (one pass, top to bottom)

```
config.yaml
    |
    v
fetchers/*  --(one per source, isolated failures)-->  list[Job]  (raw, unfiltered)
    |
    v
filters.classify()   -> each Job gets: .seniority_match, .role_match, .bucket
    |                   bucket in {remote-india-only, remote-india-or-office,
    |                              suspicious, excluded}
    v
store.diff(previous_snapshot, current)  -> new[], disappeared[], unchanged[]
    |
    v
digest.render()  -> markdown to stdout / file
    |
    v
store.save(current)   (skipped on --dry-run)
```

**Key invariant:** fetchers do *no* filtering. They normalise to `Job` and nothing
else. All judgement lives in `filters.py`, which is pure and fully unit-tested
against fixtures. This is deliberate: the filtering is the part with real logic and
the part most likely to need tuning, so it must be testable without a network.

---

## 4. Verified API facts

These were tested live on **2026-10-15** and re-verified on **2026-10-14** during
this build (see PROGRESS.md for the re-verification run). **Do not re-derive these
from scratch** — but if a source misbehaves, re-verify that one.

All of these are **open, no auth, no API key**.

### Ashby
```
https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
-> {jobs: [...]}
```
Fields: `title`, `location`, `secondaryLocations[].location`, `isRemote`,
`compensation.compensationTierSummary`, `publishedAt`, `jobUrl`, `applyUrl`.

Verified orgs: `clickhouse, temporal, harvey, vanta, deel, posthog, linear,
sourcegraph, elevenlabs, baseten, mistral, cohere, modal, replicate, supabase, neon,
ramp, sierra, perplexity-ai, fireworksai, browserbase, together-ai`

**Slugs matter** — `credo-ai`, `baz`, `qodo`, `coderabbitai` all 404.

### Greenhouse
```
https://boards-api.greenhouse.io/v1/boards/{org}/jobs        -> {jobs: [...]}
https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{id}   -> single job + `content` (HTML JD)
```
Fields: `location.name`, `absolute_url`, `updated_at`.

Verified orgs: `gitlab, elastic, huggingface, dbtlabs, airbyte, mongodb, confluent,
hashicorp, grafanalabs, docker, cockroachlabs, canonical, automattic, zapier,
chainguard, coderabbit`

`postman`, `credoai` 404.

> Note: the list endpoint does **not** include the job description. `content` only
> comes from the per-job detail endpoint. We do not fetch detail for every job (that
> would be N+1 requests against a free API); we fetch it lazily only when
> `fetch_details` is enabled in config for that source. See §6 "Open questions".

### Workable
```
https://apply.workable.com/api/v1/widget/accounts/{org}?details=true  -> {jobs: [...]}
https://apply.workable.com/api/v1/accounts/{org}/jobs/{shortcode}     -> one job
```
Fields: `country`, `countryCode`, `locations[]`, `telecommuting`.

**Important gotcha:** check `department` and the opening of the description. Some
Workable postings are **talent pools**, not real requisitions — they say things like
*"active pooling purposes only… does not guarantee employment"*. These must be
flagged, not treated as live roles. Implemented as `TALENT_POOL_MARKERS` in
`filters.py`; a match sets `Job.is_talent_pool=True` and the digest labels it.

### SmartRecruiters
```
https://api.smartrecruiters.com/v1/companies/{org}/postings  -> {content: [...]}
```

### Atlassian (bespoke, high value)
```
https://www.atlassian.com/endpoint/careers/listings   -> a bare JSON array
```
Fields: `id`, `title`, `locations[]` (strings like `"Remote - India - Remote"` or
`"Bengaluru - India - Bengaluru, 560071 India"`), `overview`, `responsibilities`,
`qualifications`, `applyUrl`.

High value because the location strings are unusually explicit about the
remote-vs-office distinction — **Atlassian req 25283 is the canonical example of a
true `remote-india-only` posting** and is the reference case in the filter tests.

### Aggregators (for discovering companies not on the curated list)
| Source | Endpoint | Notes |
|---|---|---|
| Himalayas | `https://himalayas.app/jobs/api?limit=N` | has **`locationRestrictions`** — the single best signal for India eligibility anywhere in this tool |
| RemoteOK | `https://remoteok.com/api` | **first array element is metadata — skip it**; `location`, `tags`, `salary_min`, `apply_url` |
| Remotive | `https://remotive.com/api/remote-jobs?limit=N` | |
| WorkingNomads | `https://www.workingnomads.com/api/exposed_jobs/` | |
| Jobicy | `https://jobicy.com/api/v2/remote-jobs?count=N` | |

### Machine-specific gotcha
**Python's `urllib` fails SSL verification on this machine.** Use `requests` (already
installed, 2.33.1) or shell out to `curl`. Do not spend time debugging this.

---

## 5. Design decisions and why

### 5.1 Three buckets, and why the distinction is the whole point

| Bucket | Meaning | Why separate |
|---|---|---|
| `remote-india-only` | India remote is listed, and **no Indian office location is attached** | The best outcome. Nothing to renegotiate later. |
| `remote-india-or-office` | India remote listed **alongside** Bengaluru/Hyderabad/Pune/etc. | Still viable, but the employer holds a lever to pull you onsite. Must be visually distinct in the digest — it changes how you read the offer. |
| `suspicious` | Says "Remote" with no country, **or** is remote-but-US/EU-only | **Never silently dropped.** Printed in a separate low-priority section for human reading. This bucket exists specifically because the failure mode is postings that look open and are not. |
| `excluded` | Failed seniority or role-keyword match | Not rendered by default; `--show-excluded` reveals them for tuning the config. |

Bengaluru is demoted relative to other Indian cities in the digest ordering, per
operator preference, but is **never** a reason to drop a posting.

### 5.2 JSON snapshots, not SQLite — justified

Persistence is a directory of timestamped JSON files under `~/.local/share/scout/`
(override with `--state-dir`), plus a `latest.json` pointer.

Chosen over SQLite because:
1. **The access pattern is a whole-file diff of two consecutive runs**, not a query.
   There is no relational access pattern here that would benefit from SQL.
2. **Scale is small** — low thousands of postings per run, a few MB. Well within the
   size where "just read the file" is correct.
3. **The data is inspectable and diffable with ordinary tools** (`jq`, `git diff`).
   For a tool whose entire purpose is helping a human trust what it reports, being
   able to `jq` yesterday's snapshot by hand is a real feature.
4. **No schema migrations.** Adding a field to `Job` does not require a migration
   path; old snapshots simply lack the key and the loader tolerates it.

The honest cost: a full-history query ("when did I first see this job?") is a scan
over all snapshot files rather than an indexed lookup. At one run a day that is
trivially cheap, and if it ever stops being cheap, SQLite is a contained change
behind `store.py`'s interface. Revisit if snapshots exceed ~50MB total.

**Job identity** for diffing is a stable `Job.key`: `f"{source}:{org}:{external_id}"`,
falling back to a hash of `(source, org, title, location)` when the API gives no
stable id. Identity deliberately **excludes** mutable fields like compensation and
`updated_at`, so a job that is merely edited is not reported as new.

### 5.3 Everything tunable lives in `config.yaml`

Company lists, seniority keywords, engineering keywords, India/office location
vocabularies, per-source enable flags, timeouts and delays are **all** in
`config.yaml`. Nothing is hardcoded. Rationale: the operator will tune keywords far
more often than he will change code, and a config edit needs no test run.

### 5.4 Politeness to free APIs

Every call has a **timeout** (default 20s), there is a **delay between calls**
(default 1.0s, configurable per source), and a custom `User-Agent` identifying the
tool and its repo so an API owner can see who is calling. Retries are limited
(2 attempts, exponential backoff) and only on timeouts/5xx — never on 4xx.

**One dead source must never kill the run.** Each fetcher is wrapped so an exception
is caught, recorded as a `SourceError`, and reported in the digest footer. The run
completes with whatever succeeded. This is tested (`test_fetchers.py` has a fetcher
that always raises).

> Important diffing consequence: **if a source fails, its jobs are not treated as
> "disappeared."** Disappearance is only computed for sources that actually returned
> data this run. Otherwise one flaky API would produce a digest screaming that 200
> jobs were pulled. This is a correctness trap worth knowing about — it has a
> dedicated regression test.

---

## 6. State of the build

### Built and working
_(updated as the build proceeds — see PROGRESS.md for the chronological log)_

- [x] Repo scaffolding, MIT licence, `.gitignore`, `pyproject.toml`
- [x] `config.yaml` with curated company list (verified slugs only)
- [x] `models.py` — `Job`, `SourceResult`, `Snapshot`, `DiffResult`
- [x] `http.py` — polite client (timeout, retry, delay, UA)
- [x] `config.py` — typed loading + validation
- [x] Fetchers: ashby, greenhouse, workable, smartrecruiters, atlassian, aggregators
- [x] `filters.py` — seniority/role match, three-bucket India classification,
      talent-pool detection
- [x] `store.py` — JSON snapshots, diff with source-failure guard
- [x] `digest.py` — markdown rendering
- [x] `cli.py` — `scout run` / `scout history`
- [x] Tests with fixtures, no live network
- [x] Verified end-to-end against live APIs

### Not built (deliberately deferred)
- **Greenhouse per-job detail fetch is implemented but off by default** for most orgs
  — it is N+1 requests. Enable per-source in config when a specific company's
  postings need JD text to classify.
- **No notification delivery** (email/Slack). Digest goes to stdout or `--output
  FILE`. Deliberate: delivery is a cron/pipe concern, not this tool's job.
- **No scheduling.** Run it from `cron`/`launchd`. Example line is in the README.
- **No full-text JD search.** Classification uses title + location + available
  metadata. Adding JD-body keyword matching is the most likely next improvement.

---

## 7. PICK UP HERE

**Current status: complete and working end to end. Pushed state is described below.**

If you are a fresh session, the highest-value next steps, in order:

1. **Check the push outcome.** The SSH key on this machine authenticates as
   `tapishr-kpro`, which does **not** have write access to repos under the `tapishr`
   account. `gh auth status` shows `tapishr-kpro`; `gh_token=` in `~/Code/.env` is
   empty. If the repo was never pushed, the work is complete locally and the blocker
   is purely credentials — **do not work around it** by creating the repo under
   `tapishr-kpro` without the operator explicitly approving that fallback.
   See PROGRESS.md for the exact error text.

2. **Tune `config.yaml` against a few real runs.** The keyword lists are a starting
   point, not tuned. Run `scout run --all --show-excluded` and look for roles landing
   in `excluded` that should not be, and for `suspicious` entries that could be
   classified confidently with a better rule.

3. **Consider JD-body classification.** The single biggest accuracy gain available.
   Many postings only disclose geographic eligibility in the description body, not in
   structured location fields. Greenhouse and Workable both expose a per-job detail
   endpoint (§4). The cost is N+1 requests, so it needs a cache keyed on job id +
   `updated_at` before it is turned on broadly.

4. **Expand the company list.** The aggregator sources exist precisely to discover
   companies worth adding to the curated list. After a few runs, look at which
   companies keep appearing in Himalayas/RemoteOK results with
   `locationRestrictions` including India, and add their Ashby/Greenhouse slugs.
   **Verify each slug returns 200 before adding it** — bad slugs 404 silently
   (`credo-ai`, `baz`, `qodo`, `coderabbitai`, `postman`, `credoai` are known-bad).

### Things to know before you change anything

- **`filters.py` is the heart.** It is pure functions over `Job`. Any change there
  needs a fixture test. The three-bucket distinction is the product; do not collapse
  it for simplicity.
- **Never auto-reject on a keyword.** If you find yourself adding a rule that drops a
  posting without printing it anywhere, you are re-introducing the exact failure this
  tool was built to eliminate.
- **The repo is public.** No credentials, tokens, personal data, application history,
  or company-specific notes about the operator's job search may go in it.
- Commit messages end with exactly:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
