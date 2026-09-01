"""Refresh the data, rebuild, check the output, and deploy to Cloudflare.

Use this instead of running the three steps by hand:

    python tools/deploy.py            # fetch + build + check + deploy
    python tools/deploy.py --dry-run  # everything except the deploy

It exists because of two failures that both ship silently -- the site deploys,
returns 200, and looks correct, so nothing tells you it is wrong:

  * Building while `hugo server` is running lets the dev server write its
    livereload snippet into public/. Deployed, that 404s for every visitor and
    tries to open a websocket to *their* localhost. This actually happened on
    the first deploy of this site.

  * Running `hugo` without `fetch.py` republishes whatever stale JSON is
    sitting in data/pl/. The only visible symptom is the "Updated" stamp in the
    page header, which nobody checks.

So: build clean, then refuse to deploy unless the output passes.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
INDEX = PUBLIC / "index.html"


def run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    # No capture_output anywhere in this script, deliberately. wrangler decides
    # whether it is "interactive" from whether stdout is a TTY, and a captured
    # pipe makes it refuse to use the stored OAuth login and demand a
    # CLOUDFLARE_API_TOKEN instead. Inheriting the terminal keeps the login
    # working. It also avoids a cp1252 UnicodeDecodeError on Windows when
    # wrangler prints its emoji banner into a captured pipe.
    return subprocess.run(cmd, cwd=ROOT, shell=(sys.platform == "win32"), **kw)


def npx():
    """`npx` is npx.cmd on Windows and is not resolvable without the shell."""
    return "npx.cmd" if sys.platform == "win32" else "npx"


def preflight_auth():
    """Fail early and legibly if wrangler cannot authenticate.

    Wrangler's OAuth credentials (from `wrangler login`) only work when it
    believes it is attached to a terminal. Run from a piped or automated
    context with no CLOUDFLARE_API_TOKEN set, `wrangler deploy` dies with a
    wall of text about creating API tokens, several steps after the build --
    which reads like the build broke. Better to say so up front.
    """
    import os
    if os.environ.get("CLOUDFLARE_API_TOKEN"):
        return None
    if sys.stdout.isatty():
        return None
    return (
        "wrangler cannot authenticate here: stdout is not a terminal, so it "
        "will not use your `wrangler login` credentials.\n"
        "    Either run this script directly in your terminal, or set "
        "CLOUDFLARE_API_TOKEN,\n"
        "    or build with --dry-run and run `npx wrangler deploy` yourself."
    )


def step(msg):
    print("\n== " + msg)


def check_output():
    """Assertions against the built page. Each one has actually failed before,
    or would ship a visibly broken site if it did."""
    problems = []

    if not INDEX.exists():
        return ["public/index.html was not produced -- the Hugo build failed"]

    html = INDEX.read_text(encoding="utf-8")

    if "livereload" in html:
        problems.append(
            "livereload script is in the build -- `hugo server` was running "
            "during the build. Stop it and rebuild."
        )

    # The table is the reason the site exists; an empty one still renders as a
    # valid, entirely useless page.
    rows = html.count('<td class="rank">')
    if rows != 20:
        problems.append("expected 20 table rows, found %d" % rows)

    panels = html.count('<section class="panel">')
    if panels != 7:
        problems.append("expected 7 panels, found %d" % panels)

    # Catch a Hugo template that silently rendered nothing into a column.
    if "Gameweek" not in html:
        problems.append("no gameweek in the header -- data/pl/meta.json is empty or stale")

    stamp = re.search(r"Updated (\d{4}-\d{2}-\d{2})", html)
    if not stamp:
        problems.append("no Updated stamp in the header")

    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="build and check, but do not deploy")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="reuse the JSON already in data/pl (offline builds)")
    args = ap.parse_args()

    if not args.skip_fetch:
        step("refreshing data from the FPL API")
        if run([sys.executable, "tools/fetch.py"]).returncode:
            print("\nfetch failed -- nothing deployed.", file=sys.stderr)
            return 1

    step("clean build")
    # Removed rather than overwritten: Hugo leaves orphaned files behind, and a
    # stale fingerprinted stylesheet would be uploaded alongside the new one.
    for d in (PUBLIC, ROOT / "resources"):
        shutil.rmtree(d, ignore_errors=True)
    if run(["hugo", "--quiet"]).returncode:
        print("\nhugo build failed -- nothing deployed.", file=sys.stderr)
        return 1

    step("checking the built page")
    problems = check_output()
    if problems:
        print("\nREFUSING TO DEPLOY:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    print("  all checks passed")

    if args.dry_run:
        print("\ndry run -- built and verified, not deployed.")
        return 0

    step("deploying to Cloudflare")
    problem = preflight_auth()
    if problem:
        print("\nBUILD IS GOOD, DEPLOY SKIPPED.\n    " + problem, file=sys.stderr)
        return 1

    if run([npx(), "wrangler", "deploy"]).returncode:
        print("\ndeploy failed -- see the wrangler output above.", file=sys.stderr)
        return 1

    print("\nhttps://pl-dashboard.stevenschuliger03.workers.dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
