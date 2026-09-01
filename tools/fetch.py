"""Pull the Premier League season from the public FPL API and write the JSON
files Hugo renders from.

Run this before every `hugo` build. Nothing here needs an API key.

Two facts about the upstream API drive the design of this script:

  1. The FPL API sends no Access-Control-Allow-Origin header, so a browser on
     our own domain cannot fetch it. All of it has to happen at build time,
     which is why this is a build script and not client-side JavaScript.

  2. There is no league table in the API. The `teams` array HAS played/win/
     draw/loss/points fields and they are all permanently zero -- FPL never
     fills them in. The real table has to be computed from the fixtures feed,
     which is what build_table() below does.
"""

import io
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES = "https://fantasy.premierleague.com/api/fixtures/"

OUT = Path(__file__).resolve().parent.parent / "data" / "pl"

# The API 403s a bare urllib request; it wants to look like a browser.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; pl-site build script)"}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def played(fx):
    """Has this match actually happened?

    `finished` is NOT the right test. FPL leaves it False until bonus points
    are confirmed, which can be a day or two after the final whistle -- during
    that window a played match would silently vanish from the table. A present
    score is the honest signal.
    """
    return fx["team_h_score"] is not None and fx["team_a_score"] is not None


def build_table(fixtures, teams):
    """Compute the league table from results. Standard PL rules: 3 for a win,
    1 for a draw, ranked on points, then goal difference, then goals for."""
    row = {
        t["id"]: {
            "id": t["id"],
            "name": t["name"],
            "short": t["short_name"],
            "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "gf": 0, "ga": 0, "gd": 0, "points": 0,
            "form": [],
            "home": {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0},
            "away": {"played": 0, "won": 0, "drawn": 0, "lost": 0, "gf": 0, "ga": 0},
        }
        for t in teams
    }

    # Chronological, so the form guide comes out in the right order.
    for fx in sorted((f for f in fixtures if played(f)),
                     key=lambda f: f["kickoff_time"] or ""):
        h, a = row[fx["team_h"]], row[fx["team_a"]]
        hs, ascore = fx["team_h_score"], fx["team_a_score"]

        for side, gf, ga, venue in ((h, hs, ascore, "home"), (a, ascore, hs, "away")):
            side["played"] += 1
            side["gf"] += gf
            side["ga"] += ga
            side[venue]["played"] += 1
            side[venue]["gf"] += gf
            side[venue]["ga"] += ga

            if gf > ga:
                side["won"] += 1
                side["points"] += 3
                side[venue]["won"] += 1
                side["form"].append("W")
            elif gf == ga:
                side["drawn"] += 1
                side["points"] += 1
                side[venue]["drawn"] += 1
                side["form"].append("D")
            else:
                side["lost"] += 1
                side[venue]["lost"] += 1
                side["form"].append("L")

    table = []
    for r in row.values():
        r["gd"] = r["gf"] - r["ga"]
        r["form"] = r["form"][-5:][::-1]  # last five, newest first
        table.append(r)

    table.sort(key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["name"]))
    for i, r in enumerate(table, 1):
        r["position"] = i
    return table


def build_players(elements, teams, types):
    """Flatten the 109-field player records down to what the site shows.

    The xG fields arrive as strings ("2.10"), so they are cast here rather than
    in the template -- Hugo has no clean way to sort on a stringified float.
    """
    tm = {t["id"]: t["short_name"] for t in teams}
    pos = {t["id"]: t["singular_name_short"] for t in types}
    out = []
    for e in elements:
        if e["minutes"] == 0:
            continue  # hasn't kicked a ball; nothing to rank
        xg = float(e["expected_goals"])
        xa = float(e["expected_assists"])
        out.append({
            "id": e["id"],
            "name": e["web_name"],
            "team": tm[e["team"]],
            "pos": pos[e["element_type"]],
            "minutes": e["minutes"],
            "goals": e["goals_scored"],
            "assists": e["assists"],
            "xg": round(xg, 2),
            "xa": round(xa, 2),
            # The interesting number: finishing above or below the chances
            # taken. Positive means outscoring the underlying numbers.
            "xg_diff": round(e["goals_scored"] - xg, 2),
            "xgi": round(xg + xa, 2),
            "clean_sheets": e["clean_sheets"],
            "saves": e["saves"],
            "yellow": e["yellow_cards"],
            "red": e["red_cards"],
            "cost": e["now_cost"] / 10,
            "points": e["total_points"],
        })
    return out


def build_team_xg(players, table):
    """Team-level xG, summed from the squad. FPL publishes no team xG total,
    but every player's share of it is there and they add up to the team's."""
    agg = {}
    for p in players:
        a = agg.setdefault(p["team"], {"xg": 0.0, "goals": 0})
        a["xg"] += p["xg"]
        a["goals"] += p["goals"]
    out = []
    for r in table:
        a = agg.get(r["short"], {"xg": 0.0, "goals": 0})
        out.append({
            "short": r["short"], "name": r["name"], "position": r["position"],
            "goals": r["gf"], "xg": round(a["xg"], 2),
            "xg_diff": round(r["gf"] - a["xg"], 2),
        })
    out.sort(key=lambda t: -t["xg"])
    return out


def build_matches(fixtures, teams):
    tm = {t["id"]: t["short_name"] for t in teams}
    full = {t["id"]: t["name"] for t in teams}
    results, upcoming = [], []
    for fx in fixtures:
        base = {
            "gw": fx["event"],
            "kickoff": fx["kickoff_time"],
            "home": tm.get(fx["team_h"]), "home_full": full.get(fx["team_h"]),
            "away": tm.get(fx["team_a"]), "away_full": full.get(fx["team_a"]),
        }
        if played(fx):
            results.append({**base, "hs": fx["team_h_score"], "ascore": fx["team_a_score"]})
        elif fx["kickoff_time"]:
            upcoming.append({**base,
                             "h_diff": fx["team_h_difficulty"],
                             "a_diff": fx["team_a_difficulty"]})
    results.sort(key=lambda m: m["kickoff"] or "", reverse=True)
    upcoming.sort(key=lambda m: m["kickoff"] or "")
    return results, upcoming[:10]


def write(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / (name + ".json")
    # Explicit utf-8: several squads have non-ASCII names, and on Windows the
    # default encoding is cp1252, which mangles them.
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print("  wrote data/pl/" + name + ".json")


def main():
    print("fetching FPL API...")
    boot = get(BOOTSTRAP)
    fixtures = get(FIXTURES)

    teams, events = boot["teams"], boot["events"]

    matches_played = sum(1 for f in fixtures if played(f))
    if matches_played == 0:
        print("ERROR: no played matches in the feed -- refusing to overwrite "
              "good data with an empty table.", file=sys.stderr)
        return 1

    table = build_table(fixtures, teams)
    players = build_players(boot["elements"], teams, boot["element_types"])
    results, upcoming = build_matches(fixtures, teams)

    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)

    write("meta", {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "gameweek": current["name"] if current else "Preseason",
        "next_gameweek": nxt["name"] if nxt else None,
        "next_deadline": nxt["deadline_time"] if nxt else None,
        "matches_played": matches_played,
        "matches_total": len(fixtures),
    })
    write("standings", table)
    write("scorers", sorted(players, key=lambda p: (-p["goals"], -p["assists"]))[:20])
    write("assists", sorted(players, key=lambda p: (-p["assists"], -p["goals"]))[:20])
    write("xg", sorted((p for p in players if p["minutes"] >= 90),
                       key=lambda p: -p["xgi"])[:20])
    write("overperformers", sorted((p for p in players if p["goals"] > 0),
                                   key=lambda p: -p["xg_diff"])[:15])
    write("team_xg", build_team_xg(players, table))
    write("results", results[:20])
    write("upcoming", upcoming)

    print("\ndone. %d/%d matches played, %d players with minutes."
          % (matches_played, len(fixtures), len(players)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
