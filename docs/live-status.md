# Ayush Live Status — Developer Guide

Ayush Live Status is a manually controlled, zero-surveillance profile status backend.
It never infers presence, location, or activity from GitHub commits, events, or external trackers.

---

## 1. How the System Works

```
data/status.json          (2 user-edited fields: status, until)
data/status_meta.json     (workflow-managed IST effective_date)
data/status_presets.json  (central catalog of 6 presets)
        │
        ▼
scripts/status_engine.py  +  scripts/resolve_status.py
        │
        ▼
data/status_resolved.json (unambiguous machine-readable output)
        │
        ▼
scripts/render_status_svg.py -> img/status/available.svg (displayed in README.md)
```

| File | Owner | Purpose |
| --- | --- | --- |
| `data/status.json` | **You (manual)** | The **only** file you edit daily. Strictly two fields: `status` and `until`. |
| `data/status_meta.json` | Automated workflow | Stamps the daily PR's IST `effective_date` (`YYYY-MM-DD`) so statuses never roll forward across days. |
| `data/status_presets.json` | Maintainer | Central catalog of the 6 status presets (including `working.project`). |
| `data/status_resolved.json` | Automated workflow | Resolved, SVG-ready output recording both effective status and underlying `manual_input`. |
| `img/status/available.svg` | Automated workflow | Functional SVG card rendered from `data/status_resolved.json` and embedded in `README.md`. |
| `scripts/status_engine.py` | Core library | Pure Python stdlib validation, IST timezone math, night override, 7 AM boundary, and safe `offline` fallback. |
| `scripts/resolve_status.py` | CLI | Strict PR validator (`--strict --check-only`) and idempotent resolver writing `data/status_resolved.json`. |
| `scripts/render_status_svg.py` | CLI | Functional SVG renderer reading `data/status_resolved.json` and refusing invalid/placeholder states. |
| `scripts/prepare_daily_status_pr.py` | CLI / CI | Idempotent daily Draft PR creator and safe stale-PR handler. |

---

## 2. The Only Two Fields You Edit

Every day, edit **only** `data/status.json`:

```json
{
  "status": "working",
  "until": "21:00"
}
```

- **`status`**: One of the 6 preset IDs in `data/status_presets.json`:
  - `available` — 🟢 Available (`available`)
  - `offline` — 🌙 Offline (`offline`)
  - `exam` — 📝 Exam Crunch (`busy`)
  - `travelling` — 🚆 On the Move (`limited`)
  - `working` — 💻 Building & Shipping (`focused`, includes predefined `project` description)
  - `sleeping` — 😴 Recharging (`offline`)
- **`until`**: Expiration time in 24-hour IST (`HH:MM`, zero-padded `00:00`–`23:59`).

Do **not** add `availability`, `project`, `active_window`, `emoji`, `title`, `tags`, `message`, `color`, or `date` to `data/status.json`. Extra fields are rejected by PR validation. To update the project description shown for `working`, edit `presets.working.project` in `data/status_presets.json`.

---

## 3. Resolution Priority, Night Override & Expiration

All timestamps use **`Asia/Kolkata` (IST, UTC+05:30)** with:
- **Automatic night window:** `22:00:00` through `05:59:59` IST every day.
- **Daily status boundary:** `07:00` IST matching the daily PR cycle.

### Explicit resolution priority
1. **Scheduled night override (`22:00`–`05:59:59` IST):**
   - Effective status is always `sleeping` with `availability: "offline"` (`resolution_source: "night_override"`).
   - Works even if the daily PR was never updated/merged, even if the manual status expired, and even if the manual status is already `offline`.
   - Computed at resolution time — never modifies `data/status.json`. Both the effective status (`sleeping`) and the underlying `manual_input` remain recorded in `data/status_resolved.json`.
2. **Valid manually selected status (`06:00`–`21:59:59` IST):**
   - Active whenever `data/status.json` and `data/status_meta.json` are valid and `now_ist < expires_at_ist` (`resolution_source: "manual"`).
   - At **06:00 IST**, if the manual status is still valid (for example, `until: "07:00"` or `"06:30"` from the previous day's cycle), it is automatically restored until it expires.
3. **Safe offline fallback:**
   - Whenever outside night hours and the manual status is expired, missing, placeholder, or invalid, resolves safely to `offline` (`resolution_source: "offline_fallback"`).

### Expiration calculation (`until`)
Given a daily PR's `effective_date` (`D`, stored in `data/status_meta.json`):

| `until` value (24h IST) | Effective expiration timestamp | Example (`D = 2026-11-18`) |
| --- | --- | --- |
| `07:01` – `23:59` | Same calendar date `D` | `21:00` → `2026-11-18T21:00:00+05:30` |
| `00:00` – `06:59` | Next calendar date `D + 1` (midnight crossover) | `02:00` → `2026-11-19T02:00:00+05:30` |
| `07:00` | Next morning's cycle reset `D + 1 07:00` | `07:00` → `2026-11-19T07:00:00+05:30` |

### Timeline examples (`D = 2026-11-18`)
- `{"status": "working", "until": "21:00"}`:
  - `20:59` → `working` (with `project`)
  - `21:00`–`21:59` → `offline` (expired)
  - `22:00`–`05:59:59` → `sleeping` (`offline` availability, night override)
  - `06:00`+ → `offline` (expired)
- `{"status": "working", "until": "02:00"}` (expires overnight):
  - `21:59` → `working`
  - `22:00`–`05:59:59` → `sleeping` (night override; `computed.manual_expired` flips to `true` at `02:00`)
  - `06:00`+ → `offline` (expired overnight)
- `{"status": "travelling", "until": "07:00"}` (spans through night to 07:00 boundary):
  - `21:59` → `travelling`
  - `22:00`–`05:59:59` → `sleeping` (night override)
  - `06:00`–`06:59:59` → `travelling` (restored at 06:00 because still valid)
  - `07:00`+ → `offline` (reached 07:00 daily boundary and expired; never rolls forward)

---

## 4. How Daily Draft PRs Work

`.github/workflows/daily-status-pr.yml` runs every morning at **7:00 AM IST** (`cron: "30 1 * * *"` UTC) and supports manual runs via `workflow_dispatch` (`date` and `dry_run` inputs).

1. **Branch & Title:** Creates branch `status/YYYY-MM-DD` from `main` with PR title `status: Daily live status for YYYY-MM-DD (IST)`.
2. **Real Git diff & explicit placeholders:** Resets `data/status.json` to `{"status": "__SELECT_STATUS__", "until": "__HH:MM__"}` and stamps `data/status_meta.json` with today's IST `effective_date`.
3. **Edit with Copilot or manually:** Update only `data/status.json` on the Draft PR.
4. **Automatic validation:** `.github/workflows/validate-status.yml` runs strict validation (which always rejects placeholders, invalid presets, bad `HH:MM`, extra keys, and branch/date mismatches — even during night hours).
5. **Manual merge only:** Daily PRs are always opened as Draft and never auto-merged.
6. **Duplicate & stale PR safety:**
   - Never creates duplicate PRs if an open, merged, or closed PR already exists for `status/YYYY-MM-DD`.
   - Never overwrites manual edits on an existing `status/YYYY-MM-DD` branch.
   - Automatically closes older (`date < today`) open **Draft** PRs that still hold untouched placeholders, while preserving any older PR with manual edits or marked Ready for Review.

### GitHub Actions token-trigger note
Draft PRs created with the default `GITHUB_TOKEN` do not trigger `pull_request: [opened]`. Editing `data/status.json` (by you or Copilot) fires `synchronize`, and clicking **Ready for review** fires `ready_for_review` — both trigger `validate-status.yml`. Optionally set a fine-grained PAT as secret `STATUS_BOT_TOKEN` (`Contents: Read & Write`, `Pull requests: Read & Write`) if you also want initial Draft PR creation to run PR checks immediately.

---

## 5. Customizing Presets & Project Description

Edit `data/status_presets.json`:
- To update the `working` project text without changing daily inputs, edit `presets.working.project`.
- Preset rules enforced by tests and CI:
  - Lowercase `snake_case` keys (`^[a-z][a-z0-9_]*$`).
  - Required non-empty strings: `emoji`, `title`, `category`, `availability`, `message`.
  - Unique `title` across all presets (case-insensitive).
  - `availability` in `{"available", "focused", "busy", "limited", "offline"}`.
  - `tags`: 1–2 non-empty strings (max 2).
  - `color` (optional): `#RRGGBB` hex string.
  - `project` (optional): non-empty string when set.
  - `offline` (fallback) and `sleeping` (with `availability: "offline"`) must always exist.

---

## 6. Local Testing

Run all repository tests:

```bash
python -m unittest discover -s tests -v
```

Run only the live status test suite:

```bash
python -m unittest discover -s tests -p "test_status_system.py" -v
```

Validate `data/status.json` strictly:

```bash
python scripts/resolve_status.py --strict --check-only
```

Preview resolution at specific IST timestamps (daytime, night override, 06:00 restoration):

```bash
python scripts/resolve_status.py \
  --effective-date 2026-11-18 \
  --now 2026-11-18T22:00:00+05:30 \
  --check-only \
  --print-json
```

Preview daily Draft PR creation without touching git or GitHub:

```bash
python scripts/prepare_daily_status_pr.py --dry-run
```

Regenerate the local SVG card from `data/status_resolved.json`:

```bash
python scripts/render_status_svg.py
```

---

## 7. GitHub Actions Deployment Prerequisites

1. **Merge `feat/status` to `main`:** Scheduled workflows (`daily-status-pr.yml` at 7:00 AM IST and `resolve-status.yml` every 30 minutes at `:00` and `:30`, hitting `22:00`, `06:00`, and `07:00` IST) only run from the default branch (`main`).
2. **Allow GitHub Actions to create Pull Requests:**
   - **Settings → Actions → General → Workflow permissions** → enable **Allow GitHub Actions to create and approve pull requests**.
   - Explicit minimal workflow permissions are already declared in YAML:
     - `validate-status.yml`: `contents: read`
     - `resolve-status.yml`: `contents: write`
     - `daily-status-pr.yml`: `contents: write`, `pull-requests: write`
3. **Recommended Branch Protection / Ruleset on `main`:**
   - Require the **`validate-status`** (`Validate Live Status`) check to pass before merging (`validate-status.yml` intentionally has no `paths:` filter so unrelated PRs never stall).
4. **Optional `STATUS_BOT_TOKEN` secret:**
   - Only needed if you want automated Draft PR creation itself to immediately show a failing placeholder check before you edit or mark the PR Ready for review.

---

## 8. Future SVG Integration Contract

Future SVG rendering reads `data/status_resolved.json`:
- **Effective display fields:** `status`, `emoji`, `title`, `category`, `availability`, `tags`, `message`, `project`, `color`, `until`, `timezone`, `expired`, `is_fallback`, `is_override`, `resolution_source`.
- **Underlying manual inputs (never mutated by night override):** `manual_input.status`, `manual_input.until`.
- **Computed metadata:** `computed.priority_source`, `computed.night_override_active`, `computed.night_window`, `computed.night_override_ends_at_ist`, `computed.effective_date`, `computed.expires_at_ist`, `computed.expires_at_utc`, `computed.manual_valid`, `computed.manual_expired`, `computed.configured_status`, `computed.active_status`, `computed.fallback_reason`, `computed.override_reason`, `computed.until_display`.
