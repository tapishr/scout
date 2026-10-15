# scout

**Job postings lie about geography.** A listing says "Remote". It means "Remote (US)".
Another lists India remote — and quietly attaches a Bengaluru office, so the offer
arrives with a relocation clause. A third is not a job at all, just a talent pool
that "does not guarantee employment".

If you live in India and will not relocate, you re-read the same boards every
morning and re-triage the same postings to find the handful that are genuinely
open to you. `scout` does that pass for you and reports only what changed.

It polls public job APIs, classifies every posting by whether someone living in
India can actually hold the role, diffs against the previous run, and prints a
markdown digest of what is new.

**It never auto-rejects on a keyword.** Anything it cannot classify confidently is
printed in a separate section for you to read. Silently dropping an ambiguous
posting is the exact failure this tool exists to remove.

## Example output

Real output from a first run (truncated):

```markdown
# scout — 2026-10-14 19:06 UTC

**428 new, 0 disappeared**  _(first run — everything currently matching is reported as new)_

428 roles matched the filters across 8 source(s).


### Job-alert email (Indeed, LinkedIn)

Both platforms forbid crawling — Indeed's `robots.txt` carries `Disallow: /jobs/IN/`
under `User-agent: *`, and LinkedIn's User Agreement §8.2 prohibits automated
access. So scout does not touch either site. Instead it reads the **alert emails
they send you**, from your own mailbox: first-party data, delivered on request,
parsed locally. No scraping, no bot detection, no account risk.

Set up saved searches on each platform, filter their alerts into one mail folder,
then:

```bash
export SCOUT_IMAP_HOST=imap.gmail.com
export SCOUT_IMAP_USER=you@example.com
export SCOUT_IMAP_PASSWORD=...    # an app-specific password, not your account password
```

and enable it in `config.yaml`:

```yaml
  email:
    enabled: true
    folder: "Job Alerts"
    max_age_days: 7
```

Credentials are read from the environment only, never from config — this repo is public.

## Remote, India — no office attached (7)
_India remote with no Indian office on the requisition._

- **Senior Software Engineer, Security Platform** — Sezzle
  Remote · posted today · 5,000–9,500 USD · open to: India
  _India remote listed with no Indian office attached_
  <https://himalayas.app/companies/sezzle/jobs/senior-software-engineer-security-platform-5903768307>

- **Senior Infrastructure Engineer** — Atlassian
  Remote - India - Remote, Remote - Remote · posted 35d ago
  _India remote listed with no Indian office attached_
  <https://careers-apac-atlassian.icims.com/jobs/25283/senior-infrastructure-engineer/job?mode=apply>

## Remote India, but an office is listed (5)
_Viable, but the employer has a lever to pull the role onsite._

- **Principal Data Platform Engineer** — Atlassian
  Remote - India - Remote, Remote - Remote, Bengaluru - India - Bengaluru, 560071 India · posted 13d ago
  _India remote listed alongside an Indian office (bengaluru); employer retains a lever to pull the role onsite_
  <https://careers-apac-atlassian.icims.com/jobs/27391/principal-data-platform-engineer/job?mode=apply>

## Needs a human look (416)
_Not auto-rejected — these look open but may not be. Ambiguous ones first._

- **Product Engineering Lead** — Tether Operations Limited
  Remote · posted 4d ago
  _says "Remote" with no country stated — verify India eligibility_
  <https://remoteOK.com/remote-jobs/remote-product-engineering-lead-tether-operations-limited-1137478>
```

The next morning's run against the same boards:

```markdown
**0 new, 0 disappeared**

Nothing new since the last run.
```

## The three buckets

The distinction between the first two is the entire point of the tool.

| Bucket | Meaning | Why it is separate |
|---|---|---|
| `remote-india-only` | India remote listed, no Indian office attached | Nothing left to renegotiate later |
| `remote-india-or-office` | India remote **and** a Bengaluru/Hyderabad/Pune office | Still viable, but the employer has a lever to pull you onsite |
| `suspicious` | "Remote" with no country, or remote-but-elsewhere-only | **Printed, never dropped** — ambiguous ones ranked first |

Roles that fail the seniority or engineering filters are excluded from the digest
but kept one flag away (`--show-excluded`) so the filters can be tuned.

Disappeared postings get their own section: a pulled requisition is signal,
especially if you applied to it.

## Install

```bash
git clone https://github.com/tapishr/scout.git
cd scout
pip install -e ".[dev]"
```

Python 3.10+. Two dependencies: `requests` and `PyYAML`.

## Use

```bash
scout run                    # poll, diff against the last run, print the digest
scout run --dry-run          # print without recording a snapshot
scout run --all              # report everything matching, ignoring the diff
scout run --source atlassian # restrict to one source (repeatable)
scout run --show-excluded    # include filtered-out roles, for tuning
scout run -o digest.md       # write to a file
scout history                # list stored snapshots
scout prune --keep 30        # drop old snapshots
```

Daily, via `cron`:

```cron
0 7 * * * cd ~/scout && /usr/bin/env scout run -o ~/scout-digest.md
```

## Sources

No API keys. Every source below is public and unauthenticated.

| Source | What it is | India signal |
|---|---|---|
| Ashby | Company boards | `location`, `secondaryLocations`, `isRemote` |
| Greenhouse | Company boards | `location.name` (semicolon-packed; split on parse) |
| Atlassian | Bespoke careers endpoint | Explicit `"Remote - India - Remote"` location strings |
| Himalayas | Aggregator | **`locationRestrictions`** — the strongest signal available |
| Remotive | Aggregator | `candidate_required_location` |
| Jobicy | Aggregator | `jobGeo` |
| RemoteOK | Aggregator | `location` (weaker) |
| WorkingNomads | Aggregator | `location` (weaker) |
| Workable | Company boards | `country`, `telecommuting`; ships disabled (see `config.yaml`) |
| SmartRecruiters | Company boards | `location.remote`; ships disabled (see `config.yaml`) |

Aggregators exist to surface companies not on the curated board lists — three of
the seven India-eligible roles in the run above came from Himalayas, not from any
company board.

## Configuration

Everything tunable lives in [`config.yaml`](config.yaml): company slugs, seniority
and role keywords, title exclusions, the India/office location vocabulary,
per-source toggles, timeouts and delays. Nothing is hardcoded.

Keyword matching is whole-word, so `lead` does not match "leader" and `india` does
not match "Indiana". A trailing `*` opts into stem matching: `recruit*` also
catches "recruiter" and "recruiting".

Company slugs 404 silently when a board moves, which would drop a whole company
without a trace, so every run reports the slugs that returned 404.

## Being a good citizen

These are free APIs. Every call has a timeout, calls are spaced by a configurable
delay, retries are bounded and never applied to 4xx, and the `User-Agent`
identifies the tool. One dead source never kills a run — its failure is recorded
and reported in the digest footer.

A source that fails is also excluded from disappearance detection, so a flaky API
cannot produce a digest claiming hundreds of roles were pulled.

## Not in scope

- **No LinkedIn scraping or automation.** Their User Agreement §8.2 prohibits it.
- **No auto-applying.** `scout` finds and reports; a human reads and decides.

## Development

```bash
pytest          # 96 tests, no network access
ruff check .
```

Tests cover the parts with real logic — classification and diffing — against
captured API fixtures. Nothing in the suite hits the live network.

Design decisions, verified API details and a handoff guide live in
[`CONTEXT.md`](CONTEXT.md).

## Licence

MIT
