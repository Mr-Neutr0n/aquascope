"""Self-healing harvest, the workflow half: for each failing source, try a repair and hand the outcome to a human.

Run by .github/workflows/repair.yml after the weekly harvest (needs an LLM
key in the environment: OPENAI_API_KEY, GROQ_API_KEY, HF_TOKEN or
AQUASCOPE_LLM_API_KEY + _BASE_URL + _MODEL). Per failing source with a
repairable diagnosis:

* `aquascope.maintenance.repair_source` gathers evidence, asks the model for a
  patch, applies it in the working tree and verifies it (lint, the collector's
  tests, a live smoke call);
* a verified patch becomes a branch `repair/<source>-<date>` and a pull
  request labelled `collector-health` + `automated-repair` (never merged here);
* anything else becomes a comment on the source's open collector-health
  issue: what the model concluded and, when it proposed a patch, why it was
  rejected (the diff is attached), so the maintainer starts from there.

Safe to run with --dry-run (no branch, no push, no PR, no comment; prints).
Usage: python .github/scripts/harvest_repair.py archive/health.json [--repo owner/name] [--dry-run] [--max-sources 3]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harvest_issues import diagnose, find_open_issue, gh  # noqa: E402

LABELS = ["collector-health", "automated-repair"]
# Diagnoses (see harvest_issues.diagnose) that can have a code cause; 429 / TLS / timeouts are not patched.
REPAIRABLE_PREFIXES = ("HTTP 404", "HTTP 5xx", "The response is not the format", "Unclassified")


def _repairable(diagnosis: str) -> bool:
    return diagnosis.startswith(REPAIRABLE_PREFIXES)


def _git(*args: str, dry_run: bool = False) -> str:
    if dry_run:
        print("DRY RUN: git", " ".join(args))
        return ""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _pr_body(result, run_url: str, issue: int | None) -> str:
    ev, prop, ver = result.evidence, result.proposal, result.verification
    probes = "\n".join(f"- `{p.url}` -> {p.status or p.error} {p.content_type}" for p in ev.probes) or "- (none)"
    log = "\n".join(ver.log)[-3500:] if ver else ""
    return "\n".join([
        f"Automated repair attempt for **{ev.source}** after the weekly harvest failed"
        + (f" (#{issue})" if issue else "") + ".",
        "",
        "**Harvest error**", "```", ev.error[:800], "```",
        f"**Diagnosis:** {ev.diagnosis}",
        "", "**Probes**", probes,
        "", f"**Model's explanation** ({prop.model}, confidence {prop.confidence:.2f})", "", prop.explanation,
        "", "**Verification** (lint, the collector's tests, a live smoke call, all green before this PR was opened)",
        "```", log, "```",
        "", "This PR was opened by `.github/scripts/harvest_repair.py`. Review the diff like any other contribution: "
        "the model only saw the evidence above. CI does not run on bot-opened PRs; push an empty commit or "
        "close/reopen to trigger it.",
        "", (f"Run: {run_url}" if run_url else ""),
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("health", nargs="?", default="archive/health.json")
    ap.add_argument("--repo", default="Rekin226/aquascope")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--max-sources", type=int, default=3)
    ap.add_argument("--no-live", action="store_true", help="Skip the live smoke call")
    ap.add_argument("--min-confidence", type=float, default=0.5)
    args = ap.parse_args()

    from aquascope.maintenance.repair import repair_source

    path = Path(args.health)
    if not path.exists():
        print(f"{path} not found; nothing to repair")
        return 0
    health = json.loads(path.read_text(encoding="utf-8"))
    failing = [s for s in health.get("sources", []) if not s.get("ok")]
    if not failing:
        print("every source is healthy; nothing to repair")
        return 0
    todo = []
    for s in failing:
        diag = diagnose(s.get("error") or "")
        if _repairable(diag):
            todo.append((s, diag))
        else:
            print(f"[{s['source']}] {diag[:60]}... not a code cause, skipping")
    todo = todo[: args.max_sources]

    summary = []
    for s, diag in todo:
        source, error = s["source"], s.get("error") or ""
        print(f"[{source}] repairing: {diag[:80]}")
        try:
            result = repair_source(source, error, diag, live_check=not args.no_live, min_confidence=args.min_confidence)
        except Exception as exc:  # noqa: BLE001 - one source must not sink the run
            print(f"[{source}] repair crashed: {type(exc).__name__}: {exc}")
            summary.append((source, "error", str(exc)))
            continue
        issue = find_open_issue(args.repo, source, args.dry_run)
        prop, ver = result.proposal, result.verification
        if result.outcome == "patched":
            branch = f"repair/{source}-{date.today().isoformat()}"
            files = ver.files
            _git("checkout", "-b", branch, dry_run=args.dry_run)
            _git("add", *files, dry_run=args.dry_run)
            msg = (f"repair({source}): {prop.explanation[:60]}\n\nAutomated repair proposal after the weekly harvest "
                   "failed. Evidence, verification log and caveats in the PR body.")
            _git("-c", "user.name=aquascope-repair", "-c", "user.email=actions@github.com", "commit", "-q", "-m", msg,
                 dry_run=args.dry_run)
            _git("push", "-q", "-u", "origin", branch, dry_run=args.dry_run)
            url = gh("pr", "create", "--repo", args.repo, "--head", branch, "--title",
                     f"repair({source}): {prop.explanation[:70]}", "--body", _pr_body(result, args.run_url, issue),
                     "--label", ",".join(LABELS), dry_run=args.dry_run)
            _git("checkout", "-q", "-", dry_run=args.dry_run)
            if issue and not args.dry_run:
                gh("issue", "comment", str(issue), "--repo", args.repo, "--body",
                   f"An automated repair passed lint, the collector tests and a live smoke call: {url}. Please review.")
            print(f"[{source}] PATCH VERIFIED -> {url or '(dry run)'}")
            summary.append((source, "patched", url))
        else:
            why = result.note or (prop.explanation if prop else "")
            body = "\n".join([
                f"Automated repair attempt ({health.get('run_at', '?')}): **{result.outcome}**.",
                "", f"Model ({prop.model if prop else 'n/a'}): {prop.explanation if prop else result.note}",
                "", (f"Rejected because: {why}" if result.outcome == "rejected" else ""),
                "",
                ("<details><summary>Proposed diff (not applied)</summary>\n\n```diff\n" + prop.diff[:6000]
                 + "\n```\n</details>" if prop and prop.is_patch else ""),
                ("<details><summary>Verification log</summary>\n\n```\n" + "\n".join(ver.log)[-3000:]
                 + "\n```\n</details>" if ver and ver.log else ""),
                "", "Probes: " + "; ".join(f"{p.url} -> {p.status or p.error}" for p in result.evidence.probes[:6]),
            ])
            if issue:
                gh("issue", "comment", str(issue), "--repo", args.repo, "--body", body, dry_run=args.dry_run)
                print(f"[{source}] {result.outcome} -> commented on #{issue}")
            else:
                print(f"[{source}] {result.outcome} (no open issue to comment on)\n{body[:800]}")
            summary.append((source, result.outcome, why[:100]))
    print("done:", "; ".join(f"{s}: {o}" for s, o, _ in summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
