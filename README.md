# Premier League Dashboard

A static Premier League stats site: league table, form guide, scorers, assists
and expected-goals tables, rebuilt from the official Fantasy Premier League
feed. Hugo + Cloudflare, same shape as `ppo-site`.

## Deploy

Live at **https://pl-dashboard.stevenschuliger03.workers.dev**

```bash
python tools/deploy.py
```

That refreshes the data, does a clean build, checks the output, and only then
uploads to Cloudflare. Use `--dry-run` to build and check without deploying.

**Run it in a real terminal.** Wrangler only uses your `wrangler login`
credentials when it thinks it is attached to a TTY; piped or automated, it
ignores them and demands a `CLOUDFLARE_API_TOKEN`. The script checks for this
before building anything and tells you which of the three fixes to use, rather
than failing halfway through with a wall of API-token documentation.

Auth is per-machine, via `npx wrangler login` (OAuth, account
`stevenschuliger03@gmail.com`). It is already done on this machine.

Use it rather than running the steps by hand. Two mistakes here ship a site
that returns 200 and looks fine, so nothing tells you it is broken:

- **Building while `hugo server` is running.** The dev server writes its
  livereload snippet into `public/`. Deployed, that 404s for every visitor and
  tries to open a websocket to *their* localhost. This shipped on the first
  deploy of this site before it was caught.
- **Running `hugo` without `fetch.py`.** Republishes whatever stale JSON is in
  `data/pl/`. The only symptom is the "Updated" stamp in the page header.

`deploy.py` refuses to upload if either happens.

## Rebuild only

```bash
python tools/fetch.py && hugo
```

`fetch.py` writes `data/pl/*.json`; Hugo renders those into `public/`. Both
steps, every time.

Local preview: `hugo server --port 1315`, or the `pl-site` entry in
`~/.claude/launch.json`. **Stop the dev server before building to deploy.**

## Automatic rebuilds

Repo: https://github.com/stevenschuliger03/pl-site

`.github/workflows/deploy.yml` rebuilds and redeploys on three triggers:

| Trigger | When | Why |
| --- | --- | --- |
| `push` | commits to `main` | ship template and style changes |
| `schedule` | daily, 06:15 UTC | **the important one** — refreshes the data |
| `workflow_dispatch` | manual button | pull fresh data right after a match |

The scheduled run is the whole reason this workflow exists. The site renders a
snapshot of the season, so without a timed rebuild the table silently freezes
at whatever gameweek was current when the last commit landed.

CI runs `python tools/deploy.py --dry-run` before deploying, so the same checks
that block a bad local deploy also gate the pipeline.

### One-time setup: the Cloudflare API token

CI cannot use your `wrangler login` credentials — those are OAuth, stored on
one machine. GitHub's runners need an API token instead.

1. https://dash.cloudflare.com/profile/api-tokens → **Create Token**
2. Use the **Edit Cloudflare Workers** template
3. Scope Account Resources to your account, create it, copy the value
4. Store it as a repo secret — the value should go straight from Cloudflare to
   GitHub and be pasted at the prompt, never into a file or a chat window:

```bash
gh secret set CLOUDFLARE_API_TOKEN --repo stevenschuliger03/pl-site
```

Then re-run the latest workflow (`gh run rerun --failed`) to confirm it
deploys green.

Until that secret exists every run fails at the deploy step with
`necessary to set a CLOUDFLARE_API_TOKEN`. The build and verification steps
still run and still pass, so a red run does not mean the site is broken.

### Pinning note

`wranglerVersion` in the workflow is pinned deliberately. Left unset, the
action's `npx` call cannot install wrangler non-interactively and falls back to
whatever version is preinstalled on the runner — which predates assets-only
Workers and demands a `main` entry point this config does not have. It fails
looking like a broken config rather than a stale tool.

## Where the data comes from

Two public endpoints, no API key, no account, no rate limit worth worrying
about:

| Endpoint | What it gives us |
| --- | --- |
| `/api/bootstrap-static/` | 20 teams, 626 players, 109 fields each — including real xG and xA |
| `/api/fixtures/` | All 380 fixtures, with scores for the ones that have been played |

## Three things about this API that shaped the build

These are the non-obvious parts. If something breaks, start here.

**1. There is no league table.** The `teams` array has `played`, `win`, `draw`,
`loss` and `points` fields and they are permanently zero — FPL never populates
them. Its `position` field is a preseason seeding, not a standing. The real
table is computed from the fixtures feed in `build_table()`: 3 for a win, 1 for
a draw, ranked on points, then goal difference, then goals for.

**2. `finished` is the wrong test for "has this match been played".** FPL leaves
it `False` until bonus points are confirmed, which can lag the final whistle by
a day or more. During that window a played match would silently drop out of the
table. `played()` tests for a present score instead, which is the honest signal.

**3. No CORS headers.** The API sends no `Access-Control-Allow-Origin`, so a
browser on our own domain cannot fetch it. That is the whole reason this is a
build-time Python script rather than client-side JavaScript, and the reason the
site is static rather than live.

## Gotcha for anyone editing the templates

Hugo decodes JSON numbers as `float64`. `eq .position 5` compares a float to an
int literal and is **silently false** — it left 5th place with no European rail
until it was caught. `le` and `ge` coerce properly; `eq` does not. Use
comparisons, not equality, on anything that came out of the JSON.

## Layout

```
tools/fetch.py     pulls the API, computes the table, writes data/pl/*.json
tools/deploy.py    fetch + clean build + output checks + wrangler deploy
data/pl/*.json     generated — safe to delete, regenerated every build
layouts/index.html the whole dashboard, one template
assets/css/main.css
```

## Data caveats

- Team xG is summed from the squad. FPL publishes no team total, but every
  player's share is there and they add up.
- Kick-off times are UTC.
- Early season, the xG columns are noise — a striker two goals above xG after
  two matches has told you nothing yet. They get meaningful around GW8–10.
- Not affiliated with the Premier League. The FPL API is public but
  undocumented, so field names can change without notice.
