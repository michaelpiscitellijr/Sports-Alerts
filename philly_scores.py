"""Discord notifications for Philadelphia pro teams.

Posts two kinds of messages to a Discord webhook:
  * "Upcoming game" -- once, when a game is within 24 hours of starting
  * "Final score"   -- once, when a game ends

Each run is a single pass; schedule it (e.g. GitHub Actions cron). Which games
were already announced is remembered in state.json so nothing is posted twice.

Setup:
    pip install requests
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/<id>/<token>"
    python philly_scores.py
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"].rstrip("/")
STATE_FILE = "state.json"
BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Eagles, Sixers, Phillies, Flyers, Union
LEAGUES = [
    ("football", "nfl", "NFL"),
    ("basketball", "nba", "NBA"),
    ("baseball", "mlb", "MLB"),
    ("hockey", "nhl", "NHL"),
    ("soccer", "usa.1", "MLS"),
]
TEAM_MATCH = "Philadelphia"
UPCOMING_WINDOW = timedelta(hours=24)
NOT_HAPPENING = {"STATUS_POSTPONED", "STATUS_CANCELED"}


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(upcoming, final):
    with open(STATE_FILE, "w") as f:
        json.dump({"upcoming": sorted(upcoming), "final": sorted(final)}, f)


def philly_games():
    """Yield (league label, event, competition) for Philly games from yesterday to +2 days."""
    today = datetime.now(timezone.utc).date()
    dates = f"{today - timedelta(days=1):%Y%m%d}-{today + timedelta(days=2):%Y%m%d}"
    for sport, league, label in LEAGUES:
        try:
            resp = requests.get(
                f"{BASE}/{sport}/{league}/scoreboard",
                params={"dates": dates, "limit": 300},
                timeout=15,
            )
            resp.raise_for_status()
            events = resp.json().get("events", [])
        except Exception as e:
            print(f"[{label}] fetch failed: {e}")
            continue
        for ev in events:
            comp = ev["competitions"][0]
            if any(TEAM_MATCH in t["team"]["displayName"] for t in comp["competitors"]):
                yield label, ev, comp


def sides(comp):
    away = next(t for t in comp["competitors"] if t["homeAway"] == "away")
    home = next(t for t in comp["competitors"] if t["homeAway"] == "home")
    return away, home


def upcoming_embed(label, comp, start):
    away, home = sides(comp)
    ts = int(start.timestamp())  # Discord shows this in each viewer's own timezone
    lines = [
        f"**{away['team']['displayName']}** @ **{home['team']['displayName']}**",
        f"<t:{ts}:F> (<t:{ts}:R>)",
    ]
    venue = comp.get("venue", {}).get("fullName")
    if venue:
        lines.append(venue)
    tv = [n for b in comp.get("broadcasts", []) for n in b.get("names", [])]
    if tv:
        lines.append("TV: " + ", ".join(tv))
    return {"title": f"{label} \u2022 Upcoming game", "description": "\n".join(lines), "color": 0x3498DB}


def final_embed(label, ev, comp):
    away, home = sides(comp)
    philly = next(t for t in (away, home) if TEAM_MATCH in t["team"]["displayName"])
    if philly.get("winner"):
        color = 0x2ECC71  # Philly won
    elif away.get("winner") or home.get("winner"):
        color = 0xE74C3C  # Philly lost
    else:
        color = 0x95A5A6  # tie
    desc = (
        f"{away['team']['displayName']}  **{away.get('score', 0)}**\n"
        f"{home['team']['displayName']}  **{home.get('score', 0)}**"
    )
    return {"title": f"{label} \u2022 {ev['status']['type']['shortDetail']}", "description": desc, "color": color}


def post(embed):
    r = requests.post(WEBHOOK, json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()


def main():
    state = load_state()
    sent_upcoming = set(state.get("upcoming", []))
    sent_final = set(state.get("final", []))
    now = datetime.now(timezone.utc)

    for label, ev, comp in philly_games():
        key = f"{label}:{ev['id']}"
        status = ev["status"]["type"]
        try:
            if status["state"] == "post" and status.get("completed") and key not in sent_final:
                post(final_embed(label, ev, comp))
                sent_final.add(key)
            elif status["state"] == "pre" and status["name"] not in NOT_HAPPENING and key not in sent_upcoming:
                start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                if now < start <= now + UPCOMING_WINDOW:
                    post(upcoming_embed(label, comp, start))
                    sent_upcoming.add(key)
        except Exception as e:
            print(f"[{label}] discord error: {e}")

    save_state(sent_upcoming, sent_final)


if __name__ == "__main__":
    main()
