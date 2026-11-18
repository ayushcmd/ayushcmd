#!/usr/bin/env python3
"""Prepare and open the daily 7:00 AM IST Draft PR for Ayush Live Status.

Responsibilities:
- Determine the target IST calendar date (defaults to today in Asia/Kolkata).
- Check for existing PRs on `status/YYYY-MM-DD` to guarantee idempotency
  (never create duplicate PRs for the same date).
- Never overwrite manually entered values on an existing daily branch.
- Prepare a clean branch from the base branch (`main`) with:
  - `data/status.json` reset to explicit placeholders (`__SELECT_STATUS__`, `__HH:MM__`)
  - `data/status_meta.json` stamped with today's IST `effective_date`
- Open a Draft PR with a clear guide and dynamically generated preset table.
- Safely close older *unfilled* Draft PRs from previous dates while preserving
  any older PR that has manual edits or was marked Ready for Review.
- Never auto-merge PRs.

Uses only the Python standard library + git CLI.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from status_engine import (  # noqa: E402
    DAY_BOUNDARY_STR,
    PLACEHOLDER_STATUS,
    PLACEHOLDER_UNTIL,
    TIMEZONE_NAME,
    ensure_ist,
    is_placeholder_value,
    load_presets,
    parse_daily_branch_date,
    parse_iso_date,
    parse_iso_datetime,
)

DEFAULT_PRESETS_FILE = REPO_ROOT / "data" / "status_presets.json"
DEFAULT_BASE_BRANCH = "main"
GITHUB_API_BASE = "https://api.github.com"

CATEGORY_ORDER = ("work", "college", "travel", "life", "other")
CATEGORY_LABELS = {
    "work": "Work",
    "college": "College",
    "travel": "Travel",
    "life": "Life",
    "other": "Other",
}


def daily_branch_name(target_date: date) -> str:
    return f"status/{target_date.isoformat()}"


def daily_pr_title(target_date: date) -> str:
    return f"status: Daily live status for {target_date.isoformat()} (IST)"


def build_placeholder_status() -> dict[str, str]:
    return {
        "status": PLACEHOLDER_STATUS,
        "until": PLACEHOLDER_UNTIL,
    }


def build_status_meta(target_date: date, prepared_at: datetime | None = None) -> dict[str, str]:
    ts = ensure_ist(prepared_at)
    return {
        "effective_date": target_date.isoformat(),
        "timezone": TIMEZONE_NAME,
        "day_boundary": DAY_BOUNDARY_STR,
        "source_branch": daily_branch_name(target_date),
        "prepared_at": ts.isoformat(timespec="seconds"),
    }


def is_placeholder_status_config(config: Any) -> bool:
    """Return True if a status.json dict still has unfilled placeholders."""
    if not isinstance(config, dict):
        return False
    status_val = config.get("status")
    until_val = config.get("until")
    return is_placeholder_value(status_val) and is_placeholder_value(until_val)


def has_manual_edits(config: Any) -> bool:
    """Return True if either field in status.json was replaced with a non-placeholder value."""
    if not isinstance(config, dict):
        return False
    status_val = config.get("status")
    until_val = config.get("until")
    return (
        isinstance(status_val, str) and not is_placeholder_value(status_val)
    ) or (
        isinstance(until_val, str) and not is_placeholder_value(until_val)
    )


def build_pr_body(target_date: date, presets: dict[str, dict[str, Any]]) -> str:
    """Build a concise, Copilot-friendly Draft PR description with all presets."""
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for preset_id, entry in presets.items():
        cat = entry.get("category", "other")
        grouped.setdefault(cat, []).append((preset_id, entry))

    ordered_categories = [c for c in CATEGORY_ORDER if c in grouped] + [
        c for c in sorted(grouped) if c not in CATEGORY_ORDER
    ]

    preset_lines: list[str] = []
    for cat in ordered_categories:
        label = CATEGORY_LABELS.get(cat, cat.title())
        items = ", ".join(
            f"`{pid}` ({entry['emoji']} {entry['title']})"
            for pid, entry in grouped[cat]
        )
        preset_lines.append(f"- **{label}:** {items}")

    presets_md = "\n".join(preset_lines)
    date_str = target_date.isoformat()

    return f"""## Ayush Live Status — {date_str} (IST)

Update **only** the two fields in `data/status.json`:

```json
{{
  "status": "working",
  "until": "21:00"
}}
```

### Rules
1. **Only edit `data/status.json`** (`status` and `until`). Do not edit `data/status_meta.json` — it records today's IST effective date (`{date_str}`).
2. **`status`**: Pick one of the 6 preset IDs below. Emoji, title, category, availability, tags, message, predefined project description (for `working`), and theme color resolve automatically.
3. **`until`**: 24-hour IST time (`HH:MM`) when this status expires:
   - `07:01`–`23:59` → expires on **{date_str}** (IST).
   - `00:00`–`07:00` → expires after midnight on the **next calendar date** (up to the 07:00 AM IST cycle reset).
   - After `until` passes, your status automatically falls back to `offline`.
4. **Automatic Night Status**: Every day from **22:00 to 05:59:59 IST**, your effective status automatically resolves to `sleeping` (`offline` availability) without modifying `data/status.json`. At **06:00 IST**, your manual status is restored if still valid; otherwise it falls back to `offline`.
5. **Validation & Merge**: Saving edits to `data/status.json` (or marking this PR **Ready for review**) runs the `Validate Live Status` check. Review and merge manually — this PR will never auto-merge.

### Available Presets
{presets_md}

> **Tip (GitHub Copilot):** Ask Copilot to update `data/status.json` with your chosen preset and `until` time (e.g., *"Set status to working until 21:00"*).
"""


def plan_daily_pr_action(
    target_date: date,
    existing_prs_for_branch: list[dict[str, Any]],
    open_daily_prs: list[dict[str, Any]],
    branch_exists: bool,
    existing_branch_status: Any = None,
) -> dict[str, Any]:
    """Pure decision planner for idempotent daily PR creation and stale PR handling.

    Returns a dict with:
    - `action`: one of
        - `"skip_already_open"`
        - `"skip_already_merged"`
        - `"skip_already_closed"`
        - `"open_pr_for_existing_branch"`
        - `"create_branch_and_pr"`
    - `existing_pr`: matching PR dict if skipping, else None
    - `stale_prs_to_close`: older (`date < target_date`) open Draft PRs still holding
      untouched placeholders (safe to close once today's PR exists)
    - `stale_prs_to_preserve`: older open daily PRs with manual edits or non-draft state
    - `preserve_branch_contents`: True if existing branch must not be overwritten
    """
    open_match = next((pr for pr in existing_prs_for_branch if pr.get("state") == "open"), None)
    if open_match is not None:
        return {
            "action": "skip_already_open",
            "existing_pr": open_match,
            "stale_prs_to_close": [],
            "stale_prs_to_preserve": [],
            "preserve_branch_contents": True,
        }

    merged_match = next((pr for pr in existing_prs_for_branch if pr.get("merged_at")), None)
    if merged_match is not None:
        return {
            "action": "skip_already_merged",
            "existing_pr": merged_match,
            "stale_prs_to_close": [],
            "stale_prs_to_preserve": [],
            "preserve_branch_contents": True,
        }

    closed_match = next((pr for pr in existing_prs_for_branch if pr.get("state") == "closed"), None)
    if closed_match is not None:
        return {
            "action": "skip_already_closed",
            "existing_pr": closed_match,
            "stale_prs_to_close": [],
            "stale_prs_to_preserve": [],
            "preserve_branch_contents": True,
        }

    stale_to_close: list[dict[str, Any]] = []
    stale_to_preserve: list[dict[str, Any]] = []

    for pr in open_daily_prs:
        head_ref = (pr.get("head") or {}).get("ref", "")
        pr_date = parse_daily_branch_date(head_ref)
        if pr_date is None or pr_date >= target_date:
            continue

        is_draft = bool(pr.get("draft", False))
        pr_status_cfg = pr.get("_status_config")
        if is_draft and is_placeholder_status_config(pr_status_cfg):
            stale_to_close.append(pr)
        else:
            stale_to_preserve.append(pr)

    if branch_exists:
        return {
            "action": "open_pr_for_existing_branch",
            "existing_pr": None,
            "stale_prs_to_close": stale_to_close,
            "stale_prs_to_preserve": stale_to_preserve,
            "preserve_branch_contents": True,
            "existing_branch_has_manual_edits": has_manual_edits(existing_branch_status),
        }

    return {
        "action": "create_branch_and_pr",
        "existing_pr": None,
        "stale_prs_to_close": stale_to_close,
        "stale_prs_to_preserve": stale_to_preserve,
        "preserve_branch_contents": False,
    }


def write_daily_status_files(
    repo_root: Path,
    target_date: date,
    prepared_at: datetime | None = None,
) -> tuple[Path, Path]:
    """Write placeholder `data/status.json` and stamped `data/status_meta.json`."""
    status_path = repo_root / "data" / "status.json"
    meta_path = repo_root / "data" / "status_meta.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)

    status_path.write_text(
        json.dumps(build_placeholder_status(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(build_status_meta(target_date, prepared_at), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return status_path, meta_path


class GitHubClient:
    """Minimal stdlib-only GitHub REST API client."""

    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.owner = repo.split("/", 1)[0]
        self.token = token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{GITHUB_API_BASE}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ayushcmd-live-status",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} failed ({exc.code}): {body}") from exc

    def list_prs_for_branch(self, branch: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({
            "state": "all",
            "head": f"{self.owner}:{branch}",
            "per_page": 100,
        })
        return self._request("GET", f"/repos/{self.repo}/pulls?{query}") or []

    def list_open_prs(self) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"state": "open", "per_page": 100})
        return self._request("GET", f"/repos/{self.repo}/pulls?{query}") or []

    def branch_exists(self, branch: str) -> bool:
        encoded = urllib.parse.quote(branch, safe="")
        try:
            self._request("GET", f"/repos/{self.repo}/branches/{encoded}")
            return True
        except RuntimeError as exc:
            if "(404)" in str(exc):
                return False
            raise

    def fetch_json_file_on_ref(self, file_path: str, ref: str) -> Any:
        query = urllib.parse.urlencode({"ref": ref})
        try:
            data = self._request("GET", f"/repos/{self.repo}/contents/{file_path}?{query}")
        except RuntimeError as exc:
            if "(404)" in str(exc):
                return None
            raise
        if not isinstance(data, dict) or "content" not in data:
            return None
        decoded = base64.b64decode(data["content"]).decode("utf-8")
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            return None

    def create_draft_pr(
        self,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repo}/pulls",
            {
                "title": title,
                "head": head,
                "base": base,
                "body": body,
                "draft": True,
            },
        )

    def comment_and_close_pr(self, pr_number: int, comment: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{pr_number}/comments",
            {"body": comment},
        )
        self._request(
            "PATCH",
            f"/repos/{self.repo}/pulls/{pr_number}",
            {"state": "closed"},
        )


def _run_git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and open the daily 7:00 AM IST Draft PR for Ayush Live Status."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target IST calendar date (YYYY-MM-DD). Defaults to today in Asia/Kolkata.",
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="Override current timestamp with an aware ISO-8601 string.",
    )
    parser.add_argument(
        "--base-branch",
        type=str,
        default=DEFAULT_BASE_BRANCH,
        help=f"Base branch for the daily PR (default: {DEFAULT_BASE_BRANCH}).",
    )
    parser.add_argument(
        "--presets-file",
        type=Path,
        default=DEFAULT_PRESETS_FILE,
        help="Path to presets JSON catalog (default: data/status_presets.json).",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=os.environ.get("GITHUB_REPOSITORY", ""),
        help="GitHub repository in owner/name format (default: $GITHUB_REPOSITORY).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned branch, PR title, and body without mutating git or GitHub.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    current_ist = parse_iso_datetime(args.now) if args.now else ensure_ist()
    target_date = parse_iso_date(args.date) if args.date else current_ist.date()

    presets, _ = load_presets(args.presets_file)
    branch = daily_branch_name(target_date)
    title = daily_pr_title(target_date)
    body = build_pr_body(target_date, presets)

    if args.dry_run:
        print(f"[DRY RUN] Target IST date: {target_date.isoformat()}")
        print(f"[DRY RUN] Branch: {branch} (base: {args.base_branch})")
        print(f"[DRY RUN] PR Title: {title}")
        print("[DRY RUN] Placeholder status.json:")
        print(json.dumps(build_placeholder_status(), indent=2))
        print("[DRY RUN] status_meta.json:")
        print(json.dumps(build_status_meta(target_date, current_ist), indent=2))
        print("[DRY RUN] PR Body preview:\n")
        print(body)
        return 0

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable is required.", file=sys.stderr)
        return 1
    if not args.repo or "/" not in args.repo:
        print("ERROR: --repo (or GITHUB_REPOSITORY) must be set to 'owner/name'.", file=sys.stderr)
        return 1

    gh = GitHubClient(args.repo, token)
    existing_for_branch = gh.list_prs_for_branch(branch)
    open_prs = gh.list_open_prs()

    open_daily_prs: list[dict[str, Any]] = []
    for pr in open_prs:
        head_ref = (pr.get("head") or {}).get("ref", "")
        pr_date = parse_daily_branch_date(head_ref)
        if pr_date is not None and pr_date < target_date:
            enriched = dict(pr)
            enriched["_status_config"] = gh.fetch_json_file_on_ref("data/status.json", head_ref)
            open_daily_prs.append(enriched)

    branch_exists = gh.branch_exists(branch)
    existing_branch_status = (
        gh.fetch_json_file_on_ref("data/status.json", branch) if branch_exists else None
    )

    plan = plan_daily_pr_action(
        target_date=target_date,
        existing_prs_for_branch=existing_for_branch,
        open_daily_prs=open_daily_prs,
        branch_exists=branch_exists,
        existing_branch_status=existing_branch_status,
    )

    action = plan["action"]
    if action in {"skip_already_open", "skip_already_merged", "skip_already_closed"}:
        pr = plan["existing_pr"]
        print(
            f"Skipping daily PR creation ({action}): PR #{pr.get('number')} "
            f"already exists for {branch} ({pr.get('html_url')})."
        )
        return 0

    if action == "create_branch_and_pr":
        _run_git(["config", "user.name", "github-actions[bot]"], REPO_ROOT)
        _run_git(
            ["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            REPO_ROOT,
        )
        _run_git(["checkout", "-B", branch], REPO_ROOT)
        write_daily_status_files(REPO_ROOT, target_date, prepared_at=current_ist)
        _run_git(["add", "data/status.json", "data/status_meta.json"], REPO_ROOT)
        diff_status = _run_git(["status", "--porcelain"], REPO_ROOT)
        if not diff_status:
            print(
                "ERROR: Expected a real Git diff for daily status PR, but working tree is clean.",
                file=sys.stderr,
            )
            return 1
        _run_git(
            ["commit", "-m", f"status: prepare daily live status for {target_date.isoformat()} (IST)"],
            REPO_ROOT,
        )
        _run_git(["push", "-u", "origin", branch], REPO_ROOT)
    elif action == "open_pr_for_existing_branch":
        print(
            f"Branch {branch} already exists on remote; preserving its contents "
            "and opening a Draft PR without overwriting."
        )

    created = gh.create_draft_pr(
        title=title,
        head=branch,
        base=args.base_branch,
        body=body,
    )
    pr_number = created.get("number")
    pr_url = created.get("html_url")
    print(f"Created Draft PR #{pr_number}: {pr_url}")

    for stale in plan["stale_prs_to_close"]:
        stale_num = stale["number"]
        comment = (
            f"Superseded by daily live status Draft PR #{pr_number} for "
            f"**{target_date.isoformat()} (IST)**. Closing this unfilled draft automatically."
        )
        gh.comment_and_close_pr(stale_num, comment)
        print(f"Closed unfilled stale Draft PR #{stale_num}.")

    for kept in plan["stale_prs_to_preserve"]:
        print(
            f"Preserved previous daily PR #{kept['number']} ({kept.get('head', {}).get('ref')}) "
            "because it has manual edits or is ready for review."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
