"""Posts live scores for Philadelphia pro teams to a Discord channel via webhook.

One message per game: created when the game starts, edited on every score/clock
change, and left as the final score when the game ends.

Setup:
    pip install requests
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/<id>/<token>"
    python philly_scores.py
"""
import json
import os
import time

import requests

WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"].rstrip("/")
POLL_SECONDS = 30
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))  # 0 = run forever
STATE_FILE = "state.json"
BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Eagles, Sixers, Phillies, Flyers, Union
LEAGUES = [
    ("football", "nfl"),
    ("basketball", "nba"),
    ("baseball", "mlb"),
    ("hockey", "nhl"),
    ("soccer", "usa.1"),
]
TEAM_MATCH = "Philadelphia"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def philly_games():
    for sport, league in LEAGUES:
        try:
            resp = requests.get(f"{BASE}/{sport}/{league}/scoreboard", timeout=15)
            resp.raise_for_status()
            events = resp.json().get("events", [])
        except Exception as e:
            print(f"[{league}] fetch failed: {e}")
            continue
        for ev in events:
            comp = ev["competitions"][0]
            if any(TEAM_MATCH in t["team"]["displayName"] for t in comp["competitors"]):
                yield league.upper().replace(".1", ""), ev, comp


def build_embed(league, ev, comp):
    status = ev["status"]["type"]
    away = next(t for t in comp["competitors"] if t["homeAway"] == "away")
    home = next(t for t in comp["competitors"] if t["homeAway"] == "home")
    desc = (
        f"{away['team']['displayName']}  **{away.get('score', 0)}**\n"
        f"{home['team']['displayName']}  **{home.get('score', 0)}**"
    )
    live = status["state"] == "in"
    return {
        "title": f"{league} \u2022 {status['shortDetail']}",
        "description": desc,
        "color": 0x2ECC71 if live else 0x95A5A6,
    }


def send_or_edit(rec, embed):
    if rec is None:
        r = requests.post(WEBHOOK, params={"wait": "true"}, json={"embeds": [embed]}, timeout=15)
        r.raise_for_status()
        return {"msg": r.json()["id"]}
    r = requests.patch(f"{WEBHOOK}/messages/{rec['msg']}", json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()
    return rec


def main():
    state = load_state()
    deadline = time.time() + RUN_SECONDS if RUN_SECONDS else None
    while True:
        for league, ev, comp in philly_games():
            if ev["status"]["type"]["state"] == "pre":
                continue  # not started yet
            gid = ev["id"]
            embed = build_embed(league, ev, comp)
            key = json.dumps(embed, sort_keys=True)
            rec = state.get(gid)
            if rec and rec.get("key") == key:
                continue  # nothing changed
            try:
                rec = send_or_edit(rec, embed)
            except Exception as e:
                print(f"[{league}] discord error: {e}")
                continue
            rec["key"] = key
            state[gid] = rec
            save_state(state)
        if deadline and time.time() + POLL_SECONDS > deadline:
            break  # let the next scheduled run take over
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
