"""Turn a harvest run's health.json into GitHub issues: one per failing source, closed when it recovers.

Run by .github/workflows/harvest.yml after `aquascope harvest`. Uses the `gh`
CLI (present on GitHub runners) with GITHUB_TOKEN. Deterministic: the
diagnosis comes from the error text, no LLM. Safe to run locally with
--dry-run.

Usage: python .github/scripts/harvest_issues.py archive/health.json [--repo owner/name] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

LABELS = ["collector-health", "help wanted"]
TITLE = "collector-health: {source} station catalog failed in the weekly harvest"


def diagnose(error: str) -> str:
    e = (error or "").lower()
    if "404" in e:
        return "HTTP 404: the endpoint moved or the dataset id changed. Fix the URL/dataset id in the collector."
    if "429" in e or "too many requests" in e:
        return ("HTTP 429: the agency throttles the shared/keyless path. Add a key secret or slow the collector's "
                "RateLimiter.")
    if "certificate" in e or "ssl" in e:
        return ("TLS failure: the agency's certificate chain is not in certifi. See #169 for the relax_strict_tls "
                "pattern.")
    if "timeout" in e or "timed out" in e or "connect" in e:
        return ("Timeout / connection error: the agency was down or blocks non-local runners. Re-check from another "
                "region before changing code.")
    if "5" in e[:4] or "502" in e or "503" in e or "500" in e:
        return "HTTP 5xx from the agency: probably transient. If it repeats next week, look for a replacement endpoint."
    if "json" in e or "zip" in e or "decode" in e:
        return ("The response is not the format the collector expects (HTML error page, changed schema, non-ZIP). "
                "Compare a raw response with the parser.")
    return "Unclassified. The full error is above; reproduce with `aquascope stations --source <key>`."


def gh(*args: str, dry_run: bool = False) -> str:
    if dry_run:
        print("DRY RUN: gh", " ".join(args))
        return ""
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def find_open_issue(repo: str, source: str, dry_run: bool) -> int | None:
    if dry_run:
        return None
    raw = gh("issue", "list", "--repo", repo, "--label", LABELS[0], "--state", "open", "--limit", "100",
             "--json", "number,title")
    for item in json.loads(raw or "[]"):
        if item["title"] == TITLE.format(source=source):
            return int(item["number"])
    return None


def ensure_labels(repo: str, dry_run: bool) -> None:
    for label in LABELS:
        try:
            desc = "A collector's live endpoint failed in the weekly harvest" if label == LABELS[0] else ""
            gh("label", "create", label, "--repo", repo, "--color", "d93f0b" if label == LABELS[0] else "008672",
               "--description", desc, "--force", dry_run=dry_run)
        except subprocess.CalledProcessError:
            pass  # exists or no permission; issue creation still works with existing labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("health", nargs="?", default="archive/health.json")
    ap.add_argument("--repo", default="Rekin226/aquascope")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-url", default="")
    args = ap.parse_args()

    path = Path(args.health)
    if not path.exists():
        print(f"{path} not found; nothing to report")
        return 0
    health = json.loads(path.read_text(encoding="utf-8"))
    sources = health.get("sources", [])
    ensure_labels(args.repo, args.dry_run)

    opened = closed = 0
    for s in sources:
        source, ok, error = s["source"], s["ok"], s.get("error") or ""
        existing = find_open_issue(args.repo, source, args.dry_run)
        if not ok:
            body = "\n".join([
                f"The weekly harvest could not read **{source}**'s station catalog "
                f"({health.get('run_at', '?')}, aquascope {health.get('aquascope_version', '?')}).",
                "",
                "```",
                error[:1500],
                "```",
                "",
                f"**Likely cause:** {diagnose(error)}",
                "",
                f"Licence: {s.get('license')} · agency: {s.get('agency')} · seconds before failing: {s.get('seconds')}",
                "",
                "Reproduce locally:",
                "```bash",
                f"aquascope stations --source {source} --format json -o /tmp/{source}.json",
                "```",
                "",
                (f"Run: {args.run_url}" if args.run_url else ""),
                "",
                "_Opened automatically by the harvest workflow; it closes itself when the source is healthy again._",
            ])
            if existing:
                gh("issue", "comment", str(existing), "--repo", args.repo, "--body",
                   f"Still failing on {health.get('run_at', '?')}:\n\n```\n{error[:800]}\n```\n\n{diagnose(error)}",
                   dry_run=args.dry_run)
                print(f"[{source}] still failing -> commented on #{existing}")
            else:
                url = gh("issue", "create", "--repo", args.repo, "--title", TITLE.format(source=source),
                         "--body", body, "--label", ",".join(LABELS), dry_run=args.dry_run)
                opened += 1
                print(f"[{source}] failing -> opened {url or '(dry run)'}")
        elif existing:
            note = (f"Healthy again on {health.get('run_at', '?')}: {s.get('n_stations', 0)} stations in "
                    f"{s.get('seconds')} s. Closing automatically.")
            gh("issue", "close", str(existing), "--repo", args.repo, "--comment", note, dry_run=args.dry_run)
            closed += 1
            print(f"[{source}] recovered -> closed #{existing}")
    print(f"done: {opened} opened, {closed} closed, {sum(1 for s in sources if not s['ok'])} failing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
