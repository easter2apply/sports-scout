"""
Sports Scout: finds sports bets where FanDuel, DraftKings or Kalshi pay MORE than the game is really worth.

How it decides what "really worth" means
  The sharpest sportsbook (Pinnacle) sets the most accurate lines in the world. Take its odds, remove its
  built-in cut (the "vig"), and you get a fair win chance. When FanDuel or DraftKings pays better than that
  fair chance, the bet has positive expected value (+EV). That's the only edge that holds up long term.

What it sends you (Telegram)
  🏈 BET      a straight bet (moneyline, spread or total) with the book, the line, the odds and the stake
  🎯 PROP     a player prop (only when your Odds API plan has enough credits; see PROPS below)
  🎲 PARLAY   once a day, the best 2–3 leg parlay built only from +EV picks (for fun: low hit rate)
  ✅/❌        results when games finish, with paper profit/loss

The panel (6 analysts at the desk) votes on every candidate. The Sharp must say BET, and 4 of 6 must agree.

Needs (GitHub secrets): ODDS_API_KEY (free at the-odds-api.com), TELEGRAM_BOT_TOKEN
Everything is paper-tracked on a ${BANKROLL} bankroll so you can see whether following it would have made money.
"""

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import studio

# ─────────────────────────── SETTINGS (edit these) ───────────────────────────
BANKROLL = 100.00            # paper bankroll in dollars
KELLY = 0.25                 # bet a quarter of the "Kelly" amount (the math-optimal size is too swingy)
MAX_BET = 0.03               # never more than 3% of the bankroll on one bet
MIN_BET = 1.00
MIN_EV = 0.03                # alert when a bet is worth at least +3% (fair value vs the price you get)
FAIR_RANGE = (0.22, 0.78)    # straight bets only between ~-350 and ~+350 (longshots are where models fool themselves)
MIN_BOOKS = 4                # need at least 4 books on the same line to trust the consensus
HOURS_AHEAD = (0.5, 30)      # only games starting 30 min to 30 hours from now
MAX_NEW_PER_SCAN = 4
SPORTS = {                   # The Odds API key : (label, emoji, Kalshi series, ESPN path)
    "americanfootball_nfl": ("NFL", "🏈", "KXNFLGAME", "football/nfl"),
    "americanfootball_ncaaf": ("College football", "🏈", "KXNCAAFGAME", "football/college-football"),
    "basketball_nba": ("NBA", "🏀", "KXNBAGAME", "basketball/nba"),
    "icehockey_nhl": ("NHL", "🏒", "KXNHLGAME", "hockey/nhl"),
}
SHARP = "pinnacle"
BET_BOOKS = {"fanduel": "FanDuel", "draftkings": "DraftKings"}
BOOKS = "pinnacle,fanduel,draftkings,betmgm,williamhill_us,espnbet,betrivers,bovada,lowvig,betonlineag"   # 10 books = 1 credit/market
MARKETS = "h2h,spreads,totals"
PROPS = "auto"               # "auto" = on only when your Odds API plan has 5,000+ credits a month; "on" / "off"
PROP_MARKETS = {
    "americanfootball_nfl": "player_pass_yds,player_rush_yds,player_reception_yds,player_receptions",
    "americanfootball_ncaaf": "player_pass_yds,player_rush_yds",
    "basketball_nba": "player_points,player_rebounds,player_assists,player_threes",
    "icehockey_nhl": "player_points,player_shots_on_goal",
}
PROP_GAMES_PER_SPORT = 3     # props cost credits per game: check only the next few games
PARLAY_HOUR = 11             # the daily parlay goes out after 11 AM ET
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
ODDS = "https://api.the-odds-api.com/v4"
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
STATE_FILE = "sports_state.json"
ET = ZoneInfo("America/New_York")
PANEL = ["The Sharp", "The Shopper", "Line Watcher", "The Insider", "The Skeptic", "The Clock"]
ROLES = {"The Sharp": "price beats the sharpest line (Pinnacle, no vig)",
         "The Shopper": "best price around, enough books agree",
         "Line Watcher": "the line isn't moving against us",
         "The Insider": "no fresh injury news on our side",
         "The Skeptic": "not a longshot; edge holds vs the market average",
         "The Clock": "good timing; odds are fresh"}
# ─────────────────────────────────────────────────────────────────────────────


class ApiError(Exception):
    pass


def http(url, params=None, body=None, headers=None, tries=3, want_headers=False):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Accept": "application/json", "User-Agent": "sports-scout/1.0"}
    hdr.update(headers or {})
    if data is not None:
        hdr["Content-Type"] = "application/json"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdr)
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read().decode()
                j = json.loads(raw) if raw else {}
                return (j, dict(r.headers)) if want_headers else j
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:200]
            if e.code in (429, 502, 503) and attempt < tries - 1:
                time.sleep(2 * (attempt + 1)); continue
            raise ApiError(f"{e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < tries - 1:
                time.sleep(1.5); continue
            raise ApiError(str(e))


# ───────────────────────────── odds math ─────────────────────────────────────

def dec(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def american(decimal):
    if decimal >= 2:
        return f"+{round((decimal - 1) * 100)}"
    return f"{round(-100 / (decimal - 1))}"


def fair_american(p):
    return american(1 / p) if 0 < p < 1 else "—"


def devig(prices):
    """{outcome: american odds} for one book's two-way market → {outcome: fair probability}."""
    raw = {k: 1 / dec(v) for k, v in prices.items()}
    s = sum(raw.values())
    return {k: v / s for k, v in raw.items()} if s > 0 else {}


def kelly_stake(p, d, bank):
    f = (p * d - 1) / (d - 1) if d > 1 else 0
    return round(max(MIN_BET, min(MAX_BET, f * KELLY) * bank), 2) if f > 0 else 0.0


STOP = {"the", "fc", "state", "university", "of", "st", "city"}


def tokens(name):
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in STOP and len(w) > 1}


def same_team(a, b):
    ta, tb = tokens(a), tokens(b)
    return bool(ta and tb and (ta <= tb or tb <= ta or (ta & tb and len(ta & tb) == min(len(ta), len(tb)))))


# ───────────────────────────── The Odds API (with a credit budget) ───────────

def credits(st):
    return st.setdefault("credits", {"remaining": None, "used": None, "month": None, "spent_today": 0, "day": None})


def note_credits(st, hdr):
    c = credits(st)
    lower = {k.lower(): v for k, v in hdr.items()}
    try:
        c["remaining"] = int(float(lower.get("x-requests-remaining")))
        c["used"] = int(float(lower.get("x-requests-used")))
    except (TypeError, ValueError):
        pass
    last = lower.get("x-requests-last")
    day = dt.datetime.now(ET).strftime("%Y-%m-%d")
    if c.get("day") != day:
        c.update(day=day, spent_today=0)
    try:
        c["spent_today"] += int(float(last or 0))
    except ValueError:
        pass


def daily_budget(st):
    """Spread the month's remaining credits evenly over the days left (keeps 5% in reserve)."""
    c = credits(st)
    if c.get("remaining") is None:
        return 40
    now = dt.datetime.now(ET)
    nxt = (now.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    days_left = max(1, (nxt.date() - now.date()).days)
    return max(0, int(c["remaining"] * 0.95 / days_left) - c.get("spent_today", 0))


def plan_size(st):
    c = credits(st)
    if c.get("remaining") is None or c.get("used") is None:
        return None
    return c["remaining"] + c["used"]


def props_on(st):
    if PROPS == "on":
        return True
    if PROPS == "off":
        return False
    return (plan_size(st) or 0) >= 5000


def upcoming_events(sport, key):
    """Free call (no credits): games in the next HOURS_AHEAD window."""
    now = dt.datetime.now(dt.timezone.utc)
    p = {"apiKey": key, "commenceTimeFrom": (now + dt.timedelta(hours=HOURS_AHEAD[0])).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "commenceTimeTo": (now + dt.timedelta(hours=HOURS_AHEAD[1])).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return http(f"{ODDS}/sports/{sport}/events", p)


def fetch_odds(st, sport, key, log):
    now = dt.datetime.now(dt.timezone.utc)
    p = {"apiKey": key, "bookmakers": BOOKS, "markets": MARKETS, "oddsFormat": "american",
         "commenceTimeFrom": (now + dt.timedelta(hours=HOURS_AHEAD[0])).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "commenceTimeTo": (now + dt.timedelta(hours=HOURS_AHEAD[1])).strftime("%Y-%m-%dT%H:%M:%SZ")}
    data, hdr = http(f"{ODDS}/sports/{sport}/odds", p, want_headers=True)
    note_credits(st, hdr)
    return data


def fetch_props(st, sport, event_id, key):
    p = {"apiKey": key, "bookmakers": BOOKS, "markets": PROP_MARKETS[sport], "oddsFormat": "american"}
    data, hdr = http(f"{ODDS}/sports/{sport}/events/{event_id}/odds", p, want_headers=True)
    note_credits(st, hdr)
    return data


def refresh_due(st, sport, n_sports):
    """How long to wait between odds pulls for one sport, given the credit budget (3 credits per pull)."""
    budget = daily_budget(st)
    per_day = max(0.5, budget / max(1, 3 * n_sports))
    gap_h = max(0.25, min(24, 16 / per_day))          # pulls spread over ~16 waking hours
    last = st.get("odds", {}).get(sport, {}).get("fetched", 0)
    return time.time() - last >= gap_h * 3600 and budget >= 3, gap_h


# ───────────────────────────── finding value ─────────────────────────────────

def market_lines(game):
    """{(market, point_key): {book: {outcome: american}}} for one game."""
    lines = {}
    for b in game.get("bookmakers", []):
        for m in b.get("markets", []):
            mk = m["key"]
            outs = m.get("outcomes", [])
            if mk == "h2h":
                lines.setdefault(("h2h", None), {})[b["key"]] = {o["name"]: o["price"] for o in outs}
            elif mk == "spreads":
                for o in outs:          # pair each team's line with the opposite line
                    pt = o.get("point")
                    if pt is None:
                        continue
                    other = next((x for x in outs if x is not o), None)
                    if other is None:
                        continue
                    lines.setdefault(("spreads", (o["name"], pt)), {})[b["key"]] = {o["name"]: o["price"], "_other": other["price"]}
            elif mk == "totals":
                pt = outs[0].get("point") if outs else None
                if pt is not None and len(outs) == 2:
                    lines.setdefault(("totals", pt), {})[b["key"]] = {o["name"]: o["price"] for o in outs}
    return lines


def fair_for(book_prices, outcome):
    """No-vig chance of `outcome` from one book's two-way market."""
    if "_other" in book_prices:
        return devig({"a": book_prices[outcome], "b": book_prices["_other"]}).get("a")
    if outcome not in book_prices or len(book_prices) != 2:
        return None
    return devig(book_prices).get(outcome)


def describe(sport, game, market, outcome, point):
    home, away = game["home_team"], game["away_team"]
    short = lambda t: t.split()[-1] if len(t.split()) > 1 else t
    matchup = f"{short(away)} @ {short(home)}"
    if market == "h2h":
        return f"{outcome} to win", matchup, "moneyline"
    if market == "spreads":
        return f"{outcome} {point:+g}", matchup, "spread"
    return f"{outcome} {point:g} total points", matchup, "total"


def find_straights(st, sport, games, now_ts):
    out = []
    lines_prev = st.setdefault("fair_hist", {})
    for g in games:
        try:
            start = dt.datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            continue
        hrs = (start - now_ts) / 3600
        for (mk, pk), books in market_lines(g).items():
            outcomes = ([pk[0]] if mk == "spreads" else
                        list(next(iter(books.values())).keys()) if books else [])
            for outcome in outcomes:
                if outcome == "_other":
                    continue
                fairs = {bk: fair_for(pr, outcome) for bk, pr in books.items()}
                fairs = {k: v for k, v in fairs.items() if v}
                if len(fairs) < MIN_BOOKS:
                    continue
                consensus = statistics.median(fairs.values())
                sharp = fairs.get(SHARP)
                fair = sharp if sharp is not None else consensus
                point = pk[1] if mk == "spreads" else pk if mk == "totals" else None
                key = f"{g['id']}|{mk}|{outcome}|{point}"
                prev = lines_prev.get(key)
                lines_prev[key] = [round(fair, 4), now_ts]
                best = None
                for bk, label in BET_BOOKS.items():
                    pr = books.get(bk, {}).get(outcome)
                    if pr is None:
                        continue
                    d = dec(pr)
                    ev = fair * d - 1
                    if best is None or ev > best["ev"]:
                        best = {"book": bk, "book_name": label, "odds": pr, "dec": d, "ev": ev}
                if not best:
                    continue
                all_prices = [dec(pr[outcome]) for pr in books.values() if outcome in pr]
                bet, matchup, kind = describe(sport, g, mk, outcome, point)
                out.append({"type": "straight", "sport": sport, "game_id": g["id"], "home": g["home_team"], "away": g["away_team"],
                            "start": start, "hours": hrs, "market": mk, "outcome": outcome, "point": point,
                            "bet": bet, "matchup": matchup, "kind": kind, "fair": fair, "consensus": consensus,
                            "sharp": sharp, "books": len(fairs), "best_dec_all": max(all_prices) if all_prices else None,
                            "move": (fair - prev[0]) if prev and now_ts - prev[1] < 12 * 3600 else None, **best})
    return out


def find_props(st, sport, event, data, now_ts):
    """Player props: same idea, with the market average (or Pinnacle) as the fair line."""
    out = []
    start = dt.datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).timestamp()
    by = {}
    for b in data.get("bookmakers", []):
        for m in b.get("markets", []):
            for o in m.get("outcomes", []):
                who, pt = o.get("description"), o.get("point")
                if who is None or pt is None or o.get("name") not in ("Over", "Under"):
                    continue
                by.setdefault((m["key"], who, pt), {}).setdefault(b["key"], {})[o["name"]] = o["price"]
    for (mk, who, pt), books in by.items():
        books = {k: v for k, v in books.items() if len(v) == 2}
        if len(books) < 3:
            continue
        for side in ("Over", "Under"):
            fairs = {k: devig(v)[side] for k, v in books.items()}
            fair = fairs.get(SHARP) or statistics.median(fairs.values())
            best = None
            for bk, label in BET_BOOKS.items():
                if bk in books:
                    d = dec(books[bk][side])
                    ev = fair * d - 1
                    if best is None or ev > best["ev"]:
                        best = {"book": bk, "book_name": label, "odds": books[bk][side], "dec": d, "ev": ev}
            if not best:
                continue
            stat = mk.replace("player_", "").replace("_", " ")
            out.append({"type": "prop", "sport": sport, "game_id": event["id"], "home": event["home_team"], "away": event["away_team"],
                        "start": start, "hours": (start - now_ts) / 3600, "market": mk, "outcome": side, "point": pt, "player": who,
                        "bet": f"{who} {side} {pt:g} {stat}", "matchup": f"{event['away_team'].split()[-1]} @ {event['home_team'].split()[-1]}",
                        "kind": "player prop", "fair": fair, "consensus": statistics.median(fairs.values()), "sharp": fairs.get(SHARP),
                        "books": len(books), "best_dec_all": None, "move": None, **best})
    return out


# ───────────────────────────── Kalshi game markets ───────────────────────────

def kalshi_price(m, side):
    v = m.get(f"{side}_dollars")
    if v not in (None, ""):
        try:
            return float(v)
        except ValueError:
            pass
    v = m.get(side)
    return v / 100 if isinstance(v, (int, float)) else None


def kalshi_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100 - 1e-9) / 100


def find_kalshi(series, sport, cands_by_game, games, log):
    """Kalshi 'who wins' markets priced against the same fair line."""
    out = []
    try:
        evs, cursor = [], None
        for _ in range(5):
            p = {"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                p["cursor"] = cursor
            j = http(f"{KALSHI}/events", p)
            evs += j.get("events", []) or []
            cursor = j.get("cursor")
            if not cursor:
                break
    except ApiError as e:
        log.append(f"Kalshi {series}: unavailable ({e})")
        return out
    for ev in evs:
        for m in ev.get("markets", []) or []:
            team = m.get("yes_sub_title") or ""
            g = next((g for g in games if same_team(team, g["home_team"]) or same_team(team, g["away_team"])), None)
            if not g:
                continue
            others = [x.get("yes_sub_title") or "" for x in ev.get("markets", []) if x is not m]
            if others and not any(same_team(o, g["home_team"]) or same_team(o, g["away_team"]) for o in others):
                continue
            h2h = next((c for c in cands_by_game.get(g["id"], []) if c["market"] == "h2h" and same_team(team, c["outcome"])), None)
            ask = kalshi_price(m, "yes_ask")
            if not h2h or ask is None or not 0.05 <= ask <= 0.95:
                continue
            cost = ask + kalshi_fee(ask)
            d = 1 / cost
            slug = (m.get("event_ticker") or "").lower()
            out.append({**h2h, "book": "kalshi", "book_name": "Kalshi", "odds": int(round(ask * 100)), "dec": d,
                        "ev": h2h["fair"] * d - 1, "ticker": m.get("ticker"),
                        "url": f"https://kalshi.com/markets/{slug.split('-')[0]}/{slug}" if slug else "https://kalshi.com/sports"})
    return out


# ───────────────────────────── the panel ─────────────────────────────────────

def insider_news(st, sport, log):
    """Recent ESPN headlines for the league (free), cached 30 min."""
    cache = st.setdefault("news", {}).setdefault(sport, {})
    if time.time() - cache.get("t", 0) < 1800:
        return cache.get("items", [])
    try:
        j = http(f"{ESPN}/{SPORTS[sport][3]}/news", {"limit": 50})
        items = []
        for a in j.get("articles", []):
            try:
                t = dt.datetime.fromisoformat((a.get("published") or "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            items.append({"t": t, "text": f"{a.get('headline', '')} {a.get('description', '')}"})
        cache.update(t=time.time(), items=items)
    except ApiError as e:
        log.append(f"ESPN news {sport}: unavailable ({e})")
    return cache.get("items", [])


INJURY = ("ruled out", " out ", "won't play", "will not play", "doubtful", "injur", "questionable", "suspended", "sidelined", "day-to-day")


def panel(c, news, odds_age_h):
    v = {}
    v["The Sharp"] = (c["ev"] >= MIN_EV, f"fair {fair_american(c['fair'])} vs {c['book_name']} "
                      f"{c['odds'] if c['book'] != 'kalshi' else str(c['odds']) + '¢'}: {c['ev']:+.1%} value")
    if c.get("best_dec_all"):
        best_ok = c["dec"] >= c["best_dec_all"] * 0.985
        v["The Shopper"] = (best_ok and c["books"] >= MIN_BOOKS,
                            f"{c['books']} books priced it" + ("; ours is the best price" if best_ok else "; another book pays more"))
    else:
        v["The Shopper"] = (c["books"] >= 3, f"{c['books']} books priced it")
    if c.get("move") is None:
        v["Line Watcher"] = (None, "first look at this line")
    else:
        v["Line Watcher"] = (c["move"] > -0.02, f"fair chance moved {c['move']:+.1%} since last check"
                             + (" (against us)" if c["move"] <= -0.02 else ""))
    our = c["outcome"] if c["market"] != "totals" else None
    if c["type"] == "prop":
        our = c.get("player")
    nick = (our.split()[-1].lower() if c["type"] != "prop" else our.lower()) if our else None
    def about_us(text):
        t = f" {text.lower()} "
        i = t.find(nick)
        js = [t.find(w, i) for w in INJURY if i >= 0 and t.find(w, i) >= 0]
        return bool(js) and min(js) - i < 60          # the injury word comes right after OUR team/player
    hits = [n for n in news if nick and time.time() - n["t"] < 12 * 3600 and about_us(n["text"])]
    v["The Insider"] = (not hits, ("fresh injury news: " + hits[0]["text"][:70]) if hits else "no fresh injury news on our side")
    sk_ok = (c["type"] == "prop" or FAIR_RANGE[0] <= c["fair"] <= FAIR_RANGE[1]) and c["consensus"] * c["dec"] - 1 > 0
    v["The Skeptic"] = (sk_ok, "edge holds vs the market average too" if sk_ok else
                        ("too much of a longshot/favorite" if not FAIR_RANGE[0] <= c["fair"] <= FAIR_RANGE[1] else "only beats Pinnacle, not the market average"))
    fresh = odds_age_h <= 1.5
    v["The Clock"] = (fresh and c["hours"] >= HOURS_AHEAD[0], f"starts in {c['hours']:.1f}h; odds {odds_age_h * 60:.0f} min old"
                      + ("" if fresh else " (stale: prices may have moved)"))
    return v


def approved(v):
    yes = sum(1 for ok, _ in v.values() if ok is True)
    return v["The Sharp"][0] is True and yes >= 4


# ───────────────────────────── grading (ESPN, free) ──────────────────────────

def espn_games(st, sport, day, log):
    cache = st.setdefault("espn", {})
    key = f"{sport}|{day}"
    hit = cache.get(key)
    if hit and time.time() - hit["t"] < 600:
        return hit["games"]
    p = {"dates": day.replace("-", "")}
    if sport == "americanfootball_ncaaf":
        p.update(groups=80, limit=400)
    try:
        j = http(f"{ESPN}/{SPORTS[sport][3]}/scoreboard", p)
    except ApiError as e:
        log.append(f"ESPN scores {sport}: unavailable ({e})")
        return []
    games = []
    for e in j.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        teams = {}
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            try:
                score = float(c.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            teams[c.get("homeAway")] = {"name": t.get("displayName", ""), "score": score}
        games.append({"id": e.get("id"), "done": bool(((comp.get("status") or e.get("status") or {}).get("type") or {}).get("completed")),
                      "home": teams.get("home", {}), "away": teams.get("away", {})})
    cache[key] = {"t": time.time(), "games": games}
    for k in sorted(cache)[:-40]:
        cache.pop(k, None)
    return games


def espn_box(sport, event_id, log):
    try:
        j = http(f"{ESPN}/{SPORTS[sport][3]}/summary", {"event": event_id})
    except ApiError as e:
        log.append(f"ESPN box score: unavailable ({e})")
        return None
    players = {}
    for team in (j.get("boxscore") or {}).get("players", []):
        for cat in team.get("statistics", []):
            labels = cat.get("labels") or []
            for a in cat.get("athletes", []):
                nm = (a.get("athlete") or {}).get("displayName", "")
                row = players.setdefault(nm, {})
                for lab, val in zip(labels, a.get("stats") or []):
                    row[f"{cat.get('name', '')}:{lab}"] = val
    return players


def stat_value(row, sport, market):
    def num(k):
        v = row.get(k)
        if v is None:
            return None
        v = str(v).split("-")[0]
        try:
            return float(v)
        except ValueError:
            return None
    want = {"player_pass_yds": ["passing:YDS"], "player_rush_yds": ["rushing:YDS"], "player_reception_yds": ["receiving:YDS"],
            "player_receptions": ["receiving:REC"], "player_points": [":PTS", "PTS"], "player_rebounds": [":REB", "REB"],
            "player_assists": [":AST", "AST"], "player_threes": [":3PT", "3PT"], "player_shots_on_goal": [":SOG", "SOG", ":S"]}
    if sport == "icehockey_nhl" and market == "player_points":
        g = next((num(k) for k in row if k.endswith(":G")), None)
        a = next((num(k) for k in row if k.endswith(":A")), None)
        return (g or 0) + (a or 0) if g is not None or a is not None else None
    for w in want.get(market, []):
        for k in row:
            if k == w or k.endswith(w if w.startswith(":") else ":" + w):
                return num(k)
    return None


def grade_pick(p, g, log):
    """'win' / 'loss' / 'push' / None (can't grade yet)."""
    if not g or not g.get("done"):
        return None
    home_is_home = same_team(p["home"], g["home"].get("name", ""))
    hs, as_ = g["home"]["score"], g["away"]["score"]
    if not home_is_home:
        hs, as_ = as_, hs
    if p["type"] == "prop":
        box = espn_box(p["sport"], g["id"], log)
        if box is None:
            return None
        row = next((r for nm, r in box.items() if nm.lower() == p["player"].lower()), None)
        if row is None:
            return "void"                      # didn't play: books refund
        val = stat_value(row, p["sport"], p["market"])
        if val is None:
            return "void"
        diff = val - p["point"] if p["outcome"] == "Over" else p["point"] - val
        p["result_stat"] = val
    elif p["market"] == "totals":
        diff = (hs + as_ - p["point"]) * (1 if p["outcome"] == "Over" else -1)
    else:
        mine, theirs = (hs, as_) if same_team(p["outcome"], p["home"]) else (as_, hs)
        diff = mine - theirs + (p["point"] or 0) if p["market"] == "spreads" else mine - theirs
        if p["market"] == "h2h" and diff == 0:
            return "push"
    return "win" if diff > 0 else "push" if diff == 0 else "loss"


def settle(st, log, msgs):
    now = time.time()
    rec = st.setdefault("record", {"wins": 0, "losses": 0, "pushes": 0, "staked": 0.0, "pl": 0.0})
    keep = []
    for p in st.get("open", []):
        if now < p["start"] + 2.5 * 3600:
            keep.append(p); continue
        day = dt.datetime.fromtimestamp(p["start"], ET).strftime("%Y-%m-%d")
        g = next((x for x in espn_games(st, p["sport"], day, log)
                  if same_team(p["home"], x["home"].get("name", "")) and same_team(p["away"], x["away"].get("name", ""))
                  or same_team(p["home"], x["away"].get("name", "")) and same_team(p["away"], x["home"].get("name", ""))), None)
        res = grade_pick(p, g, log)
        if res is None:
            if now - p["start"] > 4 * 86400:
                p["result"] = "void"; st.setdefault("closed", []).append(p)
            else:
                keep.append(p)
            continue
        p["result"] = res
        p["pl"] = round(p["stake"] * (p["dec"] - 1), 2) if res == "win" else -p["stake"] if res == "loss" else 0.0
        p["graded"] = now
        if res in ("win", "loss", "push"):
            rec[{"win": "wins", "loss": "losses", "push": "pushes"}[res]] += 1
            rec["staked"] = round(rec["staked"] + p["stake"], 2)
            rec["pl"] = round(rec["pl"] + p["pl"], 2)
        st.setdefault("closed", []).append(p)
        if res == "win":
            st.setdefault("parties", []).append({"t": now, "title": p["bet"], "detail": f"+${p['pl']:.2f}"})
            del st["parties"][:-10]
        msgs.append(result_message(p, rec))
    st["open"] = keep
    st["closed"] = st.get("closed", [])[-300:]
    # parlays settle when all their legs have
    for par in [x for x in st.get("parlays", []) if x.get("result") is None]:
        legs = [next((c for c in st["closed"] if c["id"] == lid), None) for lid in par["legs"]]
        if any(l is None for l in legs):
            continue
        if any(l["result"] == "loss" for l in legs):
            par["result"], par["pl"] = "loss", -par["stake"]
        else:
            d = math.prod(l["dec"] for l in legs if l["result"] == "win")
            par["result"], par["pl"] = "win", round(par["stake"] * (d - 1), 2)
        msgs.append(f"{'🎉' if par['result'] == 'win' else '😬'} PARLAY {par['result'].upper()}: {par['title']} "
                    f"({'+' if par['pl'] >= 0 else '−'}${abs(par['pl']):.2f} on paper)")


# ───────────────────────────── messages ──────────────────────────────────────

def when_text(ts):
    t = dt.datetime.fromtimestamp(ts, ET)
    today = dt.datetime.now(ET).date()
    day = "today" if t.date() == today else "tomorrow" if (t.date() - today).days == 1 else t.strftime("%a %b %-d")
    return f"{t.strftime('%-I:%M %p')} ET {day}"


def odds_text(c):
    return f"{c['odds']}¢" if c["book"] == "kalshi" else (f"+{c['odds']}" if c["odds"] > 0 else str(c["odds"]))


def bet_message(c, v):
    emoji = SPORTS[c["sport"]][1] if c["type"] == "straight" else "🎯"
    win = c["stake"] * (c["dec"] - 1)
    yes = sum(1 for ok, _ in v.values() if ok is True)
    head = "PROP" if c["type"] == "prop" else "BET"
    where = (f"On Kalshi: open the game, tap {c['outcome'].split()[-1]} YES, limit {c['odds']}¢ or less."
             if c["book"] == "kalshi" else
             f"On {c['book_name']}: {SPORTS[c['sport']][0]} → {c['matchup']} → {c['kind']} → {c['bet']} at {odds_text(c)} or better.")
    return (f"{emoji} {head}: {c['bet']}\n{SPORTS[c['sport']][0]} · {c['matchup']} · {when_text(c['start'])}\n\n"
            f"💵 Bet ${c['stake']:.2f} on {c['book_name']} at {odds_text(c)}\n"
            f"✅ If it hits: +${win:.2f}   ❌ If not: −${c['stake']:.2f}\n"
            f"📊 Fair odds {fair_american(c['fair'])} ({c['fair']:.0%}) · you get {odds_text(c)} → {c['ev']:+.1%} value\n\n"
            f"👉 {where}\nSkip it if the odds got worse than fair ({fair_american(c['fair'])}).\n\n"
            f"Panel {yes}–{6 - yes}:\n" + "\n".join(f"{'✅' if ok else '❌' if ok is False else '➖'} {n}: {why}" for n, (ok, why) in v.items())
            + (f"\n{c['url']}" if c.get("url") else ""))


def result_message(p, rec):
    icon = {"win": "✅ WON", "loss": "❌ LOST", "push": "➖ PUSH", "void": "⚪ VOID"}[p["result"]]
    extra = f" (finished at {p['result_stat']:g})" if p.get("result_stat") is not None else ""
    roi = rec["pl"] / rec["staked"] if rec["staked"] else 0
    return (f"{icon}: {p['bet']}{extra}\n{p['matchup']} · {p['book_name']} {odds_text(p)} · "
            f"{'+' if p.get('pl', 0) >= 0 else '−'}${abs(p.get('pl', 0)):.2f}\n"
            f"Record {rec['wins']}W–{rec['losses']}L–{rec['pushes']}P · paper P&L {'+' if rec['pl'] >= 0 else '−'}${abs(rec['pl']):.2f} "
            f"({roi:+.1%} return on money bet)")


def parlay_message(par, legs):
    return (f"🎲 PARLAY OF THE DAY ({len(legs)} legs, {par['book_name']})\n\n" +
            "\n".join(f"• {l['bet']} ({odds_text(l)}) · {l['matchup']}" for l in legs) +
            f"\n\nPays {american(par['dec'])} · fair chance {par['fair']:.0%} · value {par['ev']:+.1%}\n"
            f"💵 Paper stake ${par['stake']:.2f} → +${par['stake'] * (par['dec'] - 1):.2f} if it hits\n\n"
            "For fun: every leg is a +EV pick, but parlays miss most of the time. Keep it small.")


def help_text():
    return ("🏟 Sports Scout commands\n\n/today – bets placed today and still open\n/status – record, paper P&L, credits left\n"
            "/parlay – today's parlay\n/dashboard – the studio dashboard\n/help – this list\n\n"
            "Replies come on the next scan (within ~15 min).")


MENU = [("today", "Open bets"), ("status", "Record, P&L, credits"), ("parlay", "Today's parlay"),
        ("dashboard", "Studio dashboard"), ("help", "All commands")]


def status_text(st):
    rec = st.get("record", {})
    c = credits(st)
    roi = rec.get("pl", 0) / rec["staked"] if rec.get("staked") else 0
    return (f"📊 Sports Scout\nRecord {rec.get('wins', 0)}W–{rec.get('losses', 0)}L–{rec.get('pushes', 0)}P\n"
            f"Paper P&L {'+' if rec.get('pl', 0) >= 0 else '−'}${abs(rec.get('pl', 0)):.2f} on ${rec.get('staked', 0):.2f} bet ({roi:+.1%})\n"
            f"Bankroll ${BANKROLL + rec.get('pl', 0):.2f}\nOpen bets: {len(st.get('open', []))}\n"
            f"Odds API credits left this month: {c.get('remaining', '?')}" + (" (props ON)" if props_on(st) else " (props off: free plan)"))


def today_text(st):
    o = st.get("open", [])
    if not o:
        return "📭 No open bets. I'll text you when the panel finds one."
    return "📂 Open bets\n\n" + "\n".join(f"• {p['bet']} {odds_text(p)} on {p['book_name']} · ${p['stake']:.2f} · {when_text(p['start'])}" for p in o)


# ───────────────────────────── Telegram ──────────────────────────────────────

class Telegram:
    def __init__(self, token, st):
        self.token, self.st, self.out = token, st, []

    def api(self, method, **p):
        return http(f"https://api.telegram.org/bot{self.token}/{method}", body=p)

    def chat(self):
        return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or self.st.get("chat_id")

    def commands(self):
        try:
            res = self.api("getUpdates", offset=self.st.get("tg_offset", 0), timeout=0).get("result", [])
        except ApiError:
            return []
        cmds = []
        for u in res:
            self.st["tg_offset"] = u["update_id"] + 1
            msg = u.get("message") or {}
            cid = str((msg.get("chat") or {}).get("id", ""))
            if not cid:
                continue
            self.st.setdefault("chat_id", cid)
            if cid == str(self.chat()) and (msg.get("text") or "").startswith("/"):
                cmds.append(msg["text"].strip())
        return cmds

    def say(self, text):
        self.out.append(text)

    def flush(self, log):
        cid = self.chat()
        if not cid:
            if self.out:
                log.append("Telegram: no chat yet. Send your bot /start.")
            return
        for t in self.out:
            try:
                self.api("sendMessage", chat_id=cid, text=t[:4000], disable_web_page_preview=True)
                time.sleep(0.4)
            except ApiError as e:
                log.append(f"Telegram: send failed ({e})"); break
        self.out = []

    def sync_menu(self):
        sig = "|".join(c for c, _ in MENU)
        if self.st.get("menu_sig") != sig:
            try:
                self.api("setMyCommands", commands=[{"command": c, "description": d} for c, d in MENU])
                self.st["menu_sig"] = sig
            except ApiError:
                pass


# ───────────────────────────── one run ───────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, sort_keys=True)


def dashboard_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "easter2apply/sports-scout")
    o, r = repo.split("/", 1)
    return f"https://{o}.github.io/{r}/"


def run(log):
    st = load_state()
    st.setdefault("open", []); st.setdefault("closed", []); st.setdefault("parlays", [])
    now = time.time()
    st["last_run"] = now
    key = os.environ.get("ODDS_API_KEY", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg = Telegram(token, st) if token else None
    msgs = []

    if tg:
        tg.sync_menu()
        for full in tg.commands():
            cmd = full.split()[0].split("@")[0].lower()
            if cmd in ("/help", "/commands"):
                tg.say(help_text())
            elif cmd in ("/status", "/start"):
                tg.say(status_text(st))
            elif cmd == "/today":
                tg.say(today_text(st))
            elif cmd == "/parlay":
                par = next((p for p in reversed(st["parlays"]) if p.get("day") == dt.datetime.now(ET).strftime("%Y-%m-%d")), None)
                tg.say(par["text"] if par else "No parlay yet today. It goes out after 11 AM ET when there are 2+ good legs.")
            elif cmd == "/dashboard":
                tg.say(f"🏟 {dashboard_url()}")
        if not st.get("welcomed") and tg.chat():
            msgs.append("🏟 Sports Scout is live.\n\nI compare FanDuel, DraftKings and Kalshi to the sharpest line in betting (Pinnacle, "
                        "with its cut removed). When one of them pays more than a bet is really worth, the panel votes, and you get:\n"
                        "🏈 BET – the exact bet, book, odds and how much\n🎯 PROP – player props (paid Odds API plan only)\n"
                        "🎲 PARLAY – one a day, built from +EV picks (for fun)\n✅/❌ – results when games end\n\n"
                        "Everything is tracked on a $100 paper bankroll first. Tap Menu for commands.")
            st["welcomed"] = True

    settle(st, log, msgs)

    if not key:
        log.append("Odds: add the ODDS_API_KEY secret (free at the-odds-api.com) to start finding bets")
    else:
        sports_live = []
        for sport in SPORTS:
            try:
                evs = upcoming_events(sport, key)
            except ApiError as e:
                log.append(f"{SPORTS[sport][0]}: schedule unavailable ({e})"); continue
            if evs:
                sports_live.append((sport, evs))
        st["schedule"] = {s: len(e) for s, e in sports_live}
        all_cands = []
        for sport, evs in sports_live:
            due, gap = refresh_due(st, sport, len(sports_live))
            cache = st.setdefault("odds", {}).setdefault(sport, {})
            if due:
                try:
                    cache.update(games=fetch_odds(st, sport, key, log), fetched=time.time())
                    log.append(f"{SPORTS[sport][0]}: odds for {len(cache['games'])} games (next pull in ~{gap:.1f}h)")
                except ApiError as e:
                    log.append(f"{SPORTS[sport][0]}: odds unavailable ({e})")
            games = cache.get("games", [])
            age_h = (time.time() - cache.get("fetched", 0)) / 3600
            if not games or age_h > 3:
                continue
            cands = find_straights(st, sport, games, now)
            by_game = {}
            for c in cands:
                by_game.setdefault(c["game_id"], []).append(c)
            if due:                                   # Kalshi is free, but only worth checking on fresh odds
                cands += find_kalshi(SPORTS[sport][2], sport, by_game, games, log)
                if props_on(st) and daily_budget(st) > 40:
                    soon = sorted(evs, key=lambda e: e["commence_time"])[:PROP_GAMES_PER_SPORT]
                    for e in soon:
                        try:
                            cands += find_props(st, sport, e, fetch_props(st, sport, e["id"], key), now)
                        except ApiError as ex:
                            log.append(f"props {e.get('home_team')}: unavailable ({ex})")
            news = insider_news(st, sport, log)
            for c in cands:
                c["age_h"] = age_h
                c["votes"] = panel(c, news, age_h)
                c["ok"] = approved(c["votes"])
            all_cands += cands
        st["fair_hist"] = {k: v for k, v in st.get("fair_hist", {}).items() if now - v[1] < 36 * 3600}
        # shortlist + alerts
        good = sorted([c for c in all_cands if c["ev"] >= MIN_EV], key=lambda c: -c["ev"])
        st["board"] = [{k: c[k] for k in ("bet", "matchup", "sport", "book_name", "odds", "book", "fair", "ev", "ok", "start", "type")}
                       | {"votes": {n: [o, w] for n, (o, w) in c["votes"].items()}} for c in good[:20]]
        if good:
            top = good[0]
            st["last_vote"] = {"label": top["bet"], "sub": f"{top['matchup']} · {top['book_name']} {odds_text(top)}",
                               "approved": top["ok"], "when": now, "votes": {n: [o, w] for n, (o, w) in top["votes"].items()}}
        taken = {(p["game_id"], p["market"], p.get("player")) for p in st["open"]}
        new = 0
        for c in good:
            k = (c["game_id"], c["market"], c.get("player"))
            if not c["ok"] or k in taken or new >= MAX_NEW_PER_SCAN:
                continue
            if c["type"] == "straight" and any(p["game_id"] == c["game_id"] and p["type"] == "straight" for p in st["open"]):
                continue                          # one straight bet per game
            bank = BANKROLL + st.get("record", {}).get("pl", 0)
            c["stake"] = kelly_stake(c["fair"], c["dec"], bank)
            if c["stake"] <= 0:
                continue
            c["id"] = f"{c['game_id']}-{c['market']}-{c['outcome']}-{c.get('player', '')}-{int(now)}"
            pick = {k: c[k] for k in ("id", "type", "sport", "game_id", "home", "away", "start", "market", "outcome", "point", "bet",
                                      "matchup", "kind", "fair", "book", "book_name", "odds", "dec", "ev", "stake")}
            pick.update(player=c.get("player"), placed=now, votes={n: o for n, (o, _) in c["votes"].items()}, url=c.get("url"))
            st["open"].append(pick)
            taken.add(k)
            msgs.append(bet_message(c, c["votes"]))
            new += 1
        log.append(f"Panel: {len(good)} bets with +{MIN_EV:.0%} value or more, {new} new alert(s)")
        maybe_parlay(st, good, msgs, log)

    if tg:
        for m in msgs:
            tg.say(m)
        tg.flush(log)
    return st


def maybe_parlay(st, good, msgs, log):
    e = dt.datetime.now(ET)
    day = e.strftime("%Y-%m-%d")
    if e.hour < PARLAY_HOUR or any(p.get("day") == day for p in st["parlays"]):
        return
    for bk, name in BET_BOOKS.items():
        legs, games = [], set()
        for c in sorted([c for c in good if c["book"] == bk and c["ok"] and c["type"] == "straight"], key=lambda c: -c["ev"]):
            if c["game_id"] in games:
                continue                          # one leg per game (same-game legs are correlated)
            legs.append(c); games.add(c["game_id"])
            if len(legs) == 3:
                break
        if len(legs) < 2:
            continue
        d = math.prod(l["dec"] for l in legs)
        fair = math.prod(l["fair"] for l in legs)
        stake = round(max(MIN_BET, BANKROLL * 0.01), 2)
        par = {"day": day, "book_name": name, "dec": d, "fair": fair, "ev": fair * d - 1, "stake": stake,
               "title": " + ".join(l["bet"] for l in legs), "legs": [], "result": None}
        for l in legs:                            # legs are tracked as their own picks if the panel sent them
            match = next((p for p in st["open"] if p["game_id"] == l["game_id"] and p["market"] == l["market"]), None)
            if match:
                par["legs"].append(match["id"])
        par["text"] = parlay_message(par, legs)
        if len(par["legs"]) == len(legs):
            st["parlays"].append(par)
            st["parlays"] = st["parlays"][-60:]
            msgs.append(par["text"])
            log.append(f"Parlay: {par['title']}")
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/index.html")
    args = ap.parse_args()
    log = []
    try:
        st = run(log)
    except Exception as e:
        log.append(f"Run failed: {e}")
        st = load_state()
    errs = st.setdefault("errors", [])
    errs += [{"t": time.time(), "text": l[:200]} for l in log if any(w in l for w in ("unavailable", "failed", "add the"))]
    del errs[:-20]
    save_state(st)
    for l in log:
        print(l)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(studio.page(st, log, globals()))


if __name__ == "__main__":
    main()
