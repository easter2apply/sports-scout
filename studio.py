"""The Sports Scout dashboard: a 3D TV sports desk where the panel votes, plus the scoreboard of bets."""

import datetime as dt
import html
import json
import time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Each analyst: blazer, shirt, pants, skin, hair, extras. Business casual, TV sports desk.
LOOKS = {
    "The Anchor":   {"blazer": 0x1d2a44, "shirt": 0xf4f6fb, "tie": 0xb3263a, "pants": 0x23262e, "skin": 0x8d5a3b, "hair": 0x1b1410, "style": "short", "mic": True, "color": "#f4c84a"},
    "The Sharp":    {"blazer": 0x2c3444, "shirt": 0xdbe7f5, "tie": None, "pants": 0x3a3f4a, "skin": 0xf1c9a5, "hair": 0x6b4a2b, "style": "side", "glasses": True, "color": "#3ee6a1"},
    "The Shopper":  {"blazer": 0x7a2f3a, "shirt": 0xffffff, "tie": None, "pants": 0x2b2b33, "skin": 0xc68642, "hair": 0x2a1b12, "style": "bun", "color": "#ffb03b"},
    "Line Watcher": {"blazer": 0x3b5d4f, "shirt": 0xf2efe6, "tie": 0x1f2a44, "pants": 0x8a7a5c, "skin": 0xe0ac69, "hair": 0x111111, "style": "fade", "color": "#4cb7ff"},
    "The Insider":  {"blazer": 0x4a4f5a, "shirt": 0x1f2533, "tie": None, "pants": 0x1c1f26, "skin": 0x5c3a21, "hair": 0x0d0d0d, "style": "bald", "headset": True, "color": "#ff7ac8"},
    "The Skeptic":  {"blazer": 0xa08a66, "shirt": 0xe9eef5, "tie": None, "pants": 0x4a4034, "skin": 0xf3d3b5, "hair": 0x9a9a9a, "style": "side", "beard": True, "color": "#c9a7ff"},
    "The Clock":    {"blazer": 0x22344f, "shirt": 0xffffff, "tie": 0x2e7d5b, "pants": 0x2c2f38, "skin": 0xa86b45, "hair": 0x3a2414, "style": "long", "watch": True, "color": "#ff6b6b"},
}
CHATTER = {
    "The Anchor": ["Welcome back to the desk.", "Let's see what the board looks like.", "Panel, where's the value tonight?"],
    "The Sharp": ["Pinnacle moved early on this one.", "Fair price first, opinions second.", "If it's not +EV, it's entertainment."],
    "The Shopper": ["FanDuel's hanging a stale number again.", "Never take the first price you see.", "Half a point is worth a lot."],
    "Line Watcher": ["Watching that line tick.", "Steam usually means something.", "The market's talking. I'm listening."],
    "The Insider": ["Checking the injury wire.", "My sources say practice was light.", "Nothing new on the report yet."],
    "The Skeptic": ["Longshots are where models lie.", "Show me it beats the average too.", "I'll believe it when it cashes."],
    "The Clock": ["Kickoff's coming up.", "Odds go stale fast.", "Timing is half the bet."],
}


def _money(x, sign=True):
    s = f"${abs(x):,.2f}"
    return (("+" if x >= 0 else "−") + s) if sign else s


def _when(ts):
    t = dt.datetime.fromtimestamp(ts, ET)
    today = dt.datetime.now(ET).date()
    day = "Today" if t.date() == today else "Tomorrow" if (t.date() - today).days == 1 else t.strftime("%a %b %-d")
    return f"{day} {t.strftime('%-I:%M %p')}"


def _odds(p):
    if p.get("book") == "kalshi":
        return f"{p['odds']}¢"
    o = p["odds"]
    return f"+{o}" if o > 0 else str(o)


def page(st, log, cfg):
    now = time.time()
    rec = st.get("record", {"wins": 0, "losses": 0, "pushes": 0, "staked": 0.0, "pl": 0.0})
    bank = cfg["BANKROLL"] + rec.get("pl", 0)
    roi = rec.get("pl", 0) / rec["staked"] if rec.get("staked") else None
    lv = st.get("last_vote")
    panel = cfg["PANEL"]
    voters = [{"name": n, "vote": (lv["votes"].get(n, [None, ""])[0] if lv else None),
               "why": (lv["votes"].get(n, [None, ""])[1] if lv else cfg["ROLES"][n]), "role": cfg["ROLES"][n]} for n in panel]
    party = [p for p in st.get("parties", []) if now - p.get("t", 0) < 24 * 3600]
    data = {"voters": voters, "looks": LOOKS, "chatter": CHATTER, "approved": bool(lv and lv.get("approved")),
            "title": lv["label"] if lv else "Waiting for the next board", "sub": lv.get("sub", "") if lv else "",
            "when": lv["when"] if lv else 0, "party": party[-1] if party else None,
            "ticker": [f"{b['bet']} · {b['book_name']} {_odds(b)} · {b['ev']:+.1%}" for b in st.get("board", [])[:8]]}
    c = st.get("credits", {})
    seats = "".join(
        f"""<button class="seat" style="--c:{LOOKS[v['name']]['color']}" aria-label="{html.escape(v['name'])}: {html.escape(v['why'])}">
        <b>{html.escape(v['name'])}</b><span class="vote {'y' if v['vote'] else 'n' if v['vote'] is False else 'a'}">
        {'BET' if v['vote'] else 'PASS' if v['vote'] is False else '—'}</span><small>{html.escape(v['why'])}</small></button>"""
        for v in voters)
    opn = "".join(f"<tr><td>{html.escape(p['bet'])}<small>{html.escape(p['matchup'])} · {_when(p['start'])}</small></td>"
                  f"<td>{html.escape(p['book_name'])}<small>{_odds(p)}</small></td><td class='n'>${p['stake']:.2f}</td>"
                  f"<td class='n pos'>{p['ev']:+.1%}</td></tr>" for p in sorted(st.get("open", []), key=lambda p: p["start"])) \
        or "<tr><td colspan='4' class='empty'>No open bets. The panel only bets when a price beats fair value.</td></tr>"
    done = [p for p in reversed(st.get("closed", [])) if p.get("result") in ("win", "loss", "push")][:15]
    res = "".join(f"<tr><td>{html.escape(p['bet'])}<small>{html.escape(p['matchup'])}</small></td>"
                  f"<td>{ {'win': '✅ Won', 'loss': '❌ Lost', 'push': '➖ Push'}[p['result']] }</td>"
                  f"<td class='n {'pos' if p.get('pl', 0) >= 0 else 'neg'}'>{_money(p.get('pl', 0))}</td></tr>" for p in done) \
        or "<tr><td colspan='3' class='empty'>No finished bets yet.</td></tr>"
    board = "".join(f"<tr><td>{html.escape(b['bet'])}<small>{html.escape(b['matchup'])} · {_when(b['start'])}</small></td>"
                    f"<td>{html.escape(b['book_name'])} {_odds(b)}</td><td class='n'>{b['ev']:+.1%}</td>"
                    f"<td>{'✅ bet' if b['ok'] else '❌ passed'}</td></tr>" for b in st.get("board", [])[:12]) \
        or "<tr><td colspan='4' class='empty'>Nothing on the board with +3% value right now.</td></tr>"
    sched = ", ".join(f"{cfg['SPORTS'][s][0]} ({n})" for s, n in (st.get("schedule") or {}).items()) or "no games in the next 30 hours"
    upd = dt.datetime.fromtimestamp(st.get("last_run", now), ET).strftime("%a %b %-d, %-I:%M %p ET")
    plan = c.get("remaining", None)
    props = "on" if (c.get("remaining") or 0) + (c.get("used") or 0) >= 5000 else "off (free plan)"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="600"><title>Sports Scout</title>
<style>
:root{{--bg:#0b0d16;--card:#151a2b;--fg:#eef1fa;--muted:#9aa3c2;--line:#262d47;--pos:#45e39b;--neg:#ff6378;--acc:#f4c84a}}
@media (prefers-color-scheme: light){{:root:not([data-theme="dark"]){{--bg:#f3f4f9;--card:#fff;--fg:#151a2c;--muted:#5b6380;--line:#dde1ee;--pos:#13804f;--neg:#c42b40;--acc:#9a6a00}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;padding:16px 16px 40px}}
.wrap{{max-width:900px;margin:0 auto;display:grid;gap:16px}}
header h1{{margin:0;font-size:26px}}header p{{margin:2px 0 0;color:var(--muted);font-size:13px}}
.stage{{position:relative;border-radius:16px;overflow:hidden;background:#05060c;border:1px solid var(--line);aspect-ratio:16/11;max-height:560px;width:100%}}
.stage canvas{{display:block;width:100%;height:100%;touch-action:pan-y}}
.bub{{position:absolute;transform:translate(-50%,-100%);background:#fff;color:#111;font:600 12px/1.3 ui-sans-serif,system-ui;padding:6px 9px;border-radius:12px;
  max-width:170px;pointer-events:none;box-shadow:0 4px 14px rgba(0,0,0,.35);opacity:0;transition:opacity .25s}}
.bub:after{{content:"";position:absolute;left:50%;bottom:-6px;margin-left:-6px;border:6px solid transparent;border-top-color:#fff;border-bottom:0}}
.bub.on{{opacity:1}}.bub b{{display:block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--c,#555)}}
.lower{{position:absolute;left:12px;right:12px;bottom:10px;display:flex;gap:10px;align-items:flex-end;pointer-events:none}}
.lt{{background:linear-gradient(90deg,#0f1b3d,#14306b);border-left:5px solid #f4c84a;color:#fff;padding:7px 12px;border-radius:6px;box-shadow:0 6px 20px rgba(0,0,0,.4);max-width:78%}}
.lt b{{display:block;font-size:15px}}.lt small{{color:#cfe0ff;font-size:12px}}
.verdict{{margin-left:auto;font:800 14px/1 ui-sans-serif,system-ui;padding:8px 12px;border-radius:6px;color:#0b0d16;background:#9aa3c2;opacity:0;transition:opacity .3s}}
.verdict.on{{opacity:1}}.verdict.bet{{background:#45e39b}}.verdict.pass{{background:#ff6378;color:#fff}}
.banner{{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:#f4c84a;color:#1a1305;font:800 13px/1.3 ui-sans-serif,system-ui;padding:6px 14px;border-radius:99px;display:none;max-width:90%;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.seats{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}}
.seat{{all:unset;cursor:pointer;background:var(--card);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:10px;padding:8px;display:grid;gap:4px;text-align:center}}
.seat b{{font-size:12px}}.seat small{{font-size:11px;color:var(--muted);display:none}}.seat:focus small,.seat:hover small{{display:block}}
.vote{{font-weight:800;font-size:12px;border-radius:6px;padding:2px 0}}.vote.y{{background:#45e39b;color:#04120b}}.vote.n{{background:#ff6378;color:#fff}}.vote.a{{background:#2a3150;color:#c9d0ea}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
.k{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}}.k span{{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}}
.k b{{font-size:21px;font-variant-numeric:tabular-nums}}.k small{{display:block;color:var(--muted);font-size:12px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}}
h2{{margin:0 0 8px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
td small{{display:block;color:var(--muted);font-size:12px}}td.n{{text-align:right;font-variant-numeric:tabular-nums}}.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}
.empty{{color:var(--muted);text-align:center}}.fine{{color:var(--muted);font-size:12.5px}}
@media (max-width:640px){{.seats{{grid-template-columns:repeat(3,1fr)}}.kpis{{grid-template-columns:1fr 1fr}}.stage{{aspect-ratio:4/4.2}}.lt b{{font-size:13px}}}}
</style></head><body><div class="wrap">
<header><h1>🏟 Sports Scout</h1><p>Updated {upd} · NFL, college football, NBA, NHL · FanDuel, DraftKings, Kalshi · paper bankroll ${cfg['BANKROLL']:,.0f}</p></header>
<div class="stage" id="stage"><canvas id="cv"></canvas><div class="banner" id="banner"></div>
  <div class="lower"><div class="lt"><b id="ltTitle">{html.escape(data['title'])}</b><small>{html.escape(data['sub'] or 'The panel reviews the best price on the board')}</small></div>
  <div class="verdict" id="verdict"></div></div></div>
<div class="seats">{seats}</div>
<div class="kpis">
 <div class="k"><span>Bankroll</span><b>{_money(bank, False)}</b><small>started ${cfg['BANKROLL']:,.0f}</small></div>
 <div class="k"><span>Paper P&amp;L</span><b class="{'pos' if rec.get('pl', 0) >= 0 else 'neg'}">{_money(rec.get('pl', 0))}</b><small>{(f'{roi:+.1%} on money bet' if roi is not None else 'no finished bets yet')}</small></div>
 <div class="k"><span>Record</span><b>{rec.get('wins', 0)}–{rec.get('losses', 0)}–{rec.get('pushes', 0)}</b><small>wins–losses–pushes</small></div>
 <div class="k"><span>Odds credits</span><b>{plan if plan is not None else '—'}</b><small>left this month · props {props}</small></div>
</div>
<section><h2>Open bets</h2><table><tr><th>Bet</th><th>Book</th><th>Stake</th><th>Value</th></tr>{opn}</table></section>
<section><h2>On the board now</h2><table><tr><th>Bet</th><th>Price</th><th>Value</th><th>Panel</th></tr>{board}</table>
<p class="fine">Value = how much better the price is than the fair chance (Pinnacle's line with its cut removed). Games scanned: {html.escape(sched)}.</p></section>
<section><h2>Results</h2><table><tr><th>Bet</th><th>Result</th><th>P&amp;L</th></tr>{res}</table></section>
<details><summary class="fine">Scan log</summary><ul class="fine">{''.join(f'<li>{html.escape(l)}</li>' for l in log)}</ul></details>
<p class="fine">How it works: a bet is only sent when FanDuel, DraftKings or Kalshi pays at least +{cfg['MIN_EV']:.0%} more than the fair price, The Sharp says BET,
and 4 of 6 analysts agree. Stakes are a quarter of the Kelly amount, capped at {cfg['MAX_BET']:.0%} of the bankroll. Even good bets lose often; judge it over
hundreds of bets, not a weekend. Bet only what you can afford to lose. If gambling stops being fun, call 1-800-GAMBLER.</p>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>window.STUDIO = {json.dumps(data).replace('</', '<\\/')};</script>
<script>{STUDIO_JS}</script>
</body></html>"""


STUDIO_JS = r"""
(function () {
  var D = window.STUDIO, stage = document.getElementById('stage'), cv = document.getElementById('cv');
  if (!window.THREE) { stage.style.display = 'none'; return; }
  var small = window.innerWidth < 640;
  var R = new THREE.WebGLRenderer({ canvas: cv, antialias: true });
  R.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  R.shadowMap.enabled = !small;
  var S = new THREE.Scene(); S.background = new THREE.Color(0x070a14); S.fog = new THREE.Fog(0x070a14, 14, 30);
  var cam = new THREE.PerspectiveCamera(small ? 52 : 40, 1, 0.1, 100);
  function std(c, r, m) { return new THREE.MeshStandardMaterial({ color: c, roughness: r == null ? 0.7 : r, metalness: m || 0 }); }
  function M(g, m) { var x = new THREE.Mesh(g, m); x.castShadow = true; x.receiveShadow = true; return x; }
  function put(parent, mesh, x, y, z) { mesh.position.set(x, y, z); parent.add(mesh); return mesh; }

  // ── lights ──
  S.add(new THREE.HemisphereLight(0xbfd4ff, 0x1a1420, 0.55));
  var key = new THREE.SpotLight(0xfff1dd, 1.3, 30, 0.6, 0.5); key.position.set(0, 9, 7); key.castShadow = !small; S.add(key);
  var fill = new THREE.PointLight(0xfff4e6, 0.75, 16); fill.position.set(0, 2.6, 2.5); S.add(fill);
  var rim = new THREE.PointLight(0x4a7dff, 0.9, 20); rim.position.set(-6, 4, -4); S.add(rim);
  var rim2 = new THREE.PointLight(0xff6a3d, 0.6, 20); rim2.position.set(6, 4, -4); S.add(rim2);

  // ── studio set ──
  var floor = M(new THREE.CircleGeometry(16, 48), std(0x101528, 0.25, 0.4)); floor.rotation.x = -Math.PI / 2; S.add(floor);
  var ring = M(new THREE.RingGeometry(4.2, 4.35, 64), new THREE.MeshBasicMaterial({ color: 0x2f6bff })); ring.rotation.x = -Math.PI / 2; ring.position.set(0, 0.01, -1); S.add(ring);
  // curved anchor desk
  var deskMat = std(0x1b2238, 0.35, 0.3), topMat = std(0xe8ecf5, 0.25, 0.1);
  var CZ = -6.2, STEP = 0.24, SEAT_R = 3.5, R0 = 4.3, arc = STEP * 6 + 0.45;
  var desk = M(new THREE.CylinderGeometry(R0, R0, 0.95, 64, 1, true, -arc / 2, arc), deskMat); desk.position.set(0, 0.48, CZ); desk.material.side = THREE.DoubleSide; S.add(desk);
  var top = M(new THREE.RingGeometry(R0 - 0.6, R0 + 0.05, 64, 1, -Math.PI / 2 - arc / 2, arc), topMat); top.rotation.x = -Math.PI / 2; top.position.set(0, 0.96, CZ); S.add(top);
  // LED strip on the desk front
  var ledC = document.createElement('canvas'); ledC.width = 1024; ledC.height = 64; var lx = ledC.getContext('2d');
  var ledTex = new THREE.CanvasTexture(ledC);
  function drawLed(t) {
    lx.fillStyle = '#04112e'; lx.fillRect(0, 0, 1024, 64);
    lx.font = '800 38px ui-sans-serif, system-ui'; lx.fillStyle = '#f4c84a'; lx.textBaseline = 'middle';
    var msg = '  SPORTS SCOUT  ●  ' + (D.ticker.length ? D.ticker.join('  ●  ') : 'FINDING VALUE ON FANDUEL · DRAFTKINGS · KALSHI') + '  ●';
    var w = lx.measureText(msg).width, off = -((t * 90) % w);
    lx.fillText(msg, off, 34); lx.fillText(msg, off + w, 34); ledTex.needsUpdate = true;
  }
  var led = new THREE.Mesh(new THREE.CylinderGeometry(R0 + 0.01, R0 + 0.01, 0.22, 64, 1, true, -arc / 2, arc),
    new THREE.MeshBasicMaterial({ map: ledTex }));
  led.position.set(0, 0.62, CZ); S.add(led);
  // video wall
  var wallC = document.createElement('canvas'); wallC.width = 1024; wallC.height = 512; var wx = wallC.getContext('2d'); var wallTex = new THREE.CanvasTexture(wallC);
  function drawWall(state) {
    var g = wx.createLinearGradient(0, 0, 1024, 512); g.addColorStop(0, '#0b2a6b'); g.addColorStop(1, '#3a0d4f');
    wx.fillStyle = g; wx.fillRect(0, 0, 1024, 512);
    wx.strokeStyle = 'rgba(255,255,255,.08)'; for (var i = 0; i < 1024; i += 64) { wx.beginPath(); wx.moveTo(i, 0); wx.lineTo(i, 512); wx.stroke(); }
    wx.fillStyle = '#f4c84a'; wx.font = '800 34px ui-sans-serif, system-ui'; wx.fillText('ON THE BOARD', 50, 80);
    wx.fillStyle = '#fff'; wx.font = '800 58px ui-sans-serif, system-ui';
    var t = D.title, words = t.split(' '), line = '', y = 170;
    words.forEach(function (w) { if (wx.measureText(line + w).width > 920) { wx.fillText(line, 50, y); line = ''; y += 66; } line += w + ' '; });
    wx.fillText(line, 50, y);
    wx.fillStyle = '#cfe0ff'; wx.font = '600 32px ui-sans-serif, system-ui'; wx.fillText(D.sub || '', 50, y + 60);
    if (state) { wx.fillStyle = state === 'BET' ? '#45e39b' : '#ff6378'; wx.fillRect(50, 400, 300, 74); wx.fillStyle = '#0b0d16'; wx.font = '900 50px ui-sans-serif, system-ui'; wx.fillText(state === 'BET' ? '✓ BET IT' : '✗ PASS', 70, 455); }
    wallTex.needsUpdate = true;
  }
  drawWall(null);
  var wall = M(new THREE.PlaneGeometry(9, 4.5), new THREE.MeshBasicMaterial({ map: wallTex })); wall.position.set(0, 3.5, -8.6); S.add(wall);
  var wallFrame = M(new THREE.BoxGeometry(9.3, 4.8, 0.15), std(0x0d0f18, 0.4, 0.6)); wallFrame.position.set(0, 3.5, -8.72); S.add(wallFrame);
  [-1, 1].forEach(function (sd) {          // side screens with a pulsing field graphic
    var c = document.createElement('canvas'); c.width = 256; c.height = 384; var x = c.getContext('2d');
    x.fillStyle = '#0d3b1e'; x.fillRect(0, 0, 256, 384); x.strokeStyle = 'rgba(255,255,255,.6)'; x.lineWidth = 3;
    for (var i = 0; i <= 10; i++) { x.beginPath(); x.moveTo(20, 20 + i * 34.4); x.lineTo(236, 20 + i * 34.4); x.stroke(); }
    x.strokeRect(20, 20, 216, 344);
    var s = M(new THREE.PlaneGeometry(2.2, 3.3), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c) }));
    s.position.set(sd * 6.4, 2.9, -7.0); s.rotation.y = -sd * 0.55; S.add(s);
  });
  // lighting truss
  var truss = std(0x2a2f3c, 0.5, 0.6);
  put(S, M(new THREE.BoxGeometry(14, 0.12, 0.12), truss), 0, 6.6, -1.5);
  for (var i = -3; i <= 3; i++) { var can = put(S, M(new THREE.CylinderGeometry(0.14, 0.2, 0.35, 12), std(0x15171f, 0.4, 0.7)), i * 2, 6.35, -1); can.rotation.x = 0.5; }

  // ── people (business casual) ──
  var people = {}, bubbles = {}, NAMES = ['The Anchor'].concat(D.voters.map(function (v) { return v.name; }));
  var order = [D.voters[0], D.voters[1], D.voters[2], null, D.voters[3], D.voters[4], D.voters[5]];
  function person(name, L) {
    var g = new THREE.Group(), u = g.userData;
    var BL = std(L.blazer, 0.75), SH = std(L.shirt, 0.6), PA = std(L.pants, 0.8), SK = std(L.skin, 0.6), HA = std(L.hair, 0.85);
    // legs + shoes (mostly hidden by the desk, seen when they stand)
    [-1, 1].forEach(function (sd) {
      put(g, M(new THREE.CylinderGeometry(0.075, 0.07, 0.62, 10), PA), sd * 0.11, 0.33, 0);
      put(g, M(new THREE.BoxGeometry(0.13, 0.07, 0.26), std(0x3b2414, 0.5)), sd * 0.11, 0.03, 0.05);
    });
    var torso = new THREE.Group(); torso.position.y = 0.68; g.add(torso); u.torso = torso;
    put(torso, M(new THREE.BoxGeometry(0.5, 0.6, 0.3), BL), 0, 0.3, 0);                 // blazer
    var v = put(torso, M(new THREE.BoxGeometry(0.16, 0.42, 0.02), SH), 0, 0.38, 0.152);  // open shirt V
    if (L.tie) put(torso, M(new THREE.BoxGeometry(0.06, 0.36, 0.02), std(L.tie, 0.5)), 0, 0.36, 0.165);
    [-1, 1].forEach(function (sd) { var lap = put(torso, M(new THREE.BoxGeometry(0.1, 0.36, 0.03), std(L.blazer * 0 + L.blazer, 0.6)), sd * 0.12, 0.42, 0.15); lap.rotation.z = sd * 0.25; });
    put(torso, M(new THREE.BoxGeometry(0.07, 0.1, 0.02), new THREE.MeshBasicMaterial({ color: 0xffffff })), 0.15, 0.22, 0.156); // press badge
    put(torso, M(new THREE.BoxGeometry(0.004, 0.25, 0.004), std(0x1b5cff)), 0.15, 0.36, 0.156);
    var collar = put(torso, M(new THREE.CylinderGeometry(0.1, 0.12, 0.06, 12), SH), 0, 0.62, 0);
    // arms
    function arm(sd) {
      var sh = new THREE.Group(); sh.position.set(sd * 0.3, 0.55, 0); torso.add(sh);
      put(sh, M(new THREE.CylinderGeometry(0.065, 0.06, 0.32, 10), BL), 0, -0.16, 0);
      var el = new THREE.Group(); el.position.y = -0.32; sh.add(el);
      put(el, M(new THREE.CylinderGeometry(0.06, 0.055, 0.28, 10), BL), 0, -0.14, 0);
      put(el, M(new THREE.CylinderGeometry(0.058, 0.058, 0.03, 10), SH), 0, -0.27, 0);           // shirt cuff
      put(el, M(new THREE.SphereGeometry(0.055, 10, 8), SK), 0, -0.31, 0);
      sh.userData.el = el; return sh;
    }
    u.armL = arm(-1); u.armR = arm(1);
    u.armL.rotation.x = u.armR.rotation.x = -0.9; u.armL.userData.el.rotation.x = u.armR.userData.el.rotation.x = -0.6;
    // head
    var head = new THREE.Group(); head.position.y = 1.0; torso.add(head); u.head = head;
    put(head, M(new THREE.CylinderGeometry(0.06, 0.07, 0.1, 10), SK), 0, -0.2, 0);
    var skull = put(head, M(new THREE.SphereGeometry(0.2, 24, 18), SK), 0, 0, 0); skull.scale.set(1, 1.1, 1);
    [-1, 1].forEach(function (sd) {
      put(head, M(new THREE.SphereGeometry(0.025, 8, 6), std(0x1a1a1a, 0.3)), sd * 0.07, 0.03, 0.185);
      put(head, M(new THREE.BoxGeometry(0.07, 0.015, 0.02), HA), sd * 0.07, 0.09, 0.19);
      put(head, M(new THREE.SphereGeometry(0.04, 8, 6), SK), sd * 0.2, 0, 0);
    });
    u.mouth = put(head, M(new THREE.BoxGeometry(0.08, 0.015, 0.02), std(0x6b2c25, 0.5)), 0, -0.09, 0.19);
    if (L.style !== 'bald') {
      var cap = put(head, M(new THREE.SphereGeometry(0.21, 20, 12, 0, Math.PI * 2, 0, L.style === 'fade' ? 1.0 : 1.35), HA), 0, 0.03, -0.01);
      if (L.style === 'side') { cap.rotation.z = 0.12; }
      if (L.style === 'bun') put(head, M(new THREE.SphereGeometry(0.09, 12, 10), HA), 0, 0.18, -0.15);
      if (L.style === 'long') put(head, M(new THREE.BoxGeometry(0.4, 0.38, 0.12), HA), 0, -0.08, -0.13);
    }
    if (L.beard) put(head, M(new THREE.BoxGeometry(0.17, 0.07, 0.05), HA), 0, -0.14, 0.16);
    if (L.glasses) { [-1, 1].forEach(function (sd) { var r = put(head, M(new THREE.TorusGeometry(0.045, 0.008, 6, 16), std(0x111111, 0.3)), sd * 0.07, 0.03, 0.2); }); put(head, M(new THREE.BoxGeometry(0.05, 0.008, 0.008), std(0x111111)), 0, 0.03, 0.2); }
    if (L.headset) { var hb = put(head, M(new THREE.TorusGeometry(0.21, 0.015, 6, 20, Math.PI), std(0x222222)), 0, 0.02, 0); hb.rotation.z = 0; put(head, M(new THREE.CylinderGeometry(0.05, 0.05, 0.04, 12), std(0x222222)), 0.2, 0, 0).rotation.z = Math.PI / 2; put(head, M(new THREE.BoxGeometry(0.012, 0.012, 0.16), std(0x222222)), 0.17, -0.08, 0.1); }
    if (L.mic) { var mic = new THREE.Group(); put(mic, M(new THREE.CylinderGeometry(0.02, 0.025, 0.2, 10), std(0x1a1a1a, 0.3, 0.6)), 0, 0, 0); put(mic, M(new THREE.SphereGeometry(0.045, 12, 10), std(0x666a75, 0.4, 0.8)), 0, 0.12, 0); put(mic, M(new THREE.BoxGeometry(0.06, 0.05, 0.06), std(0x1b5cff)), 0, 0.03, 0); mic.position.set(0, -0.36, 0.04); u.armR.userData.el.add(mic); }
    if (L.watch) put(u.armL.userData.el, M(new THREE.CylinderGeometry(0.062, 0.062, 0.04, 12), std(0xd9b44a, 0.3, 0.8)), 0, -0.25, 0);
    // voting card (held low until the vote)
    var card = new THREE.Group(); var cc = document.createElement('canvas'); cc.width = 256; cc.height = 128;
    var cardTex = new THREE.CanvasTexture(cc); u.cardCtx = cc.getContext('2d'); u.cardTex = cardTex;
    put(card, new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.25), new THREE.MeshBasicMaterial({ map: cardTex, side: THREE.DoubleSide })), 0, 0, 0);
    card.position.set(0, -0.42, 0.06); u.armR.userData.el.add(card); u.card = card; card.visible = false;
    // seat
    var chair = put(g, M(new THREE.BoxGeometry(0.55, 0.9, 0.12), std(0x111520, 0.5)), 0, 0.95, -0.28);
    return g;
  }
  function setCard(u, vote) {
    var x = u.cardCtx; x.fillStyle = vote === true ? '#45e39b' : vote === false ? '#ff6378' : '#8a93ad'; x.fillRect(0, 0, 256, 128);
    x.fillStyle = vote === false ? '#fff' : '#0b0d16'; x.font = '900 72px ui-sans-serif, system-ui'; x.textAlign = 'center'; x.textBaseline = 'middle';
    x.fillText(vote === true ? 'BET' : vote === false ? 'PASS' : '—', 128, 68); u.cardTex.needsUpdate = true;
  }
  order.forEach(function (v, i) {
    var name = v ? v.name : 'The Anchor', L = D.looks[name];
    var th = (i - 3) * STEP, g = person(name, L);
    g.position.set(Math.sin(th) * SEAT_R, 0, CZ + Math.cos(th) * SEAT_R);
    g.rotation.y = th;
    g.userData.name = name; g.userData.vote = v ? v.vote : null; g.userData.why = v ? v.why : '';
    g.userData.phase = i * 1.3; g.userData.col = L.color;
    if (name === 'The Anchor') g.scale.setScalar(1.04);
    S.add(g); people[name] = g;
    var b = document.createElement('div'); b.className = 'bub'; b.style.setProperty('--c', L.color); stage.appendChild(b); bubbles[name] = b;
    // name plate on the desk
    var pc = document.createElement('canvas'); pc.width = 256; pc.height = 64; var px = pc.getContext('2d');
    px.fillStyle = '#0d1430'; px.fillRect(0, 0, 256, 64); px.fillStyle = L.color; px.fillRect(0, 0, 256, 6);
    px.fillStyle = '#fff'; px.font = '700 26px ui-sans-serif, system-ui'; px.textAlign = 'center'; px.fillText(name.toUpperCase(), 128, 44);
    var plate = new THREE.Mesh(new THREE.PlaneGeometry(0.75, 0.19), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(pc) }));
    plate.position.set(Math.sin(th) * (R0 - 0.12), 1.08, CZ + Math.cos(th) * (R0 - 0.12)); plate.rotation.set(-0.35, th, 0, 'YXZ'); S.add(plate);
  });

  // ── speech bubbles ──
  var V = new THREE.Vector3();
  function say(name, text, ms) {
    var b = bubbles[name]; if (!b) return;
    b.innerHTML = '<b>' + name + '</b>' + text.replace(/</g, '&lt;'); b.classList.add('on'); b._until = performance.now() + (ms || 3800);
  }
  function placeBubbles() {
    var w = stage.clientWidth, h = stage.clientHeight, now = performance.now();
    Object.keys(bubbles).forEach(function (n) {
      var b = bubbles[n]; if (b._until && now > b._until) { b.classList.remove('on'); b._until = 0; }
      if (!b.classList.contains('on')) return;
      people[n].userData.head.getWorldPosition(V); V.y += 0.38; V.project(cam);
      b.style.left = Math.max(80, Math.min(w - 80, (V.x + 1) / 2 * w)) + 'px'; b.style.top = ((1 - V.y) / 2 * h) + 'px';
    });
  }

  // ── the show: chatter → roll call → verdict → (party) ──
  var verdictEl = document.getElementById('verdict'), banner = document.getElementById('banner');
  var T0 = performance.now() / 1000, script = [], talker = null;
  function chatter(at) { var n = NAMES[Math.floor(Math.random() * NAMES.length)], lines = D.chatter[n]; script.push({ t: at, fn: function () { talker = n; say(n, lines[Math.floor(Math.random() * lines.length)], 3200); } }); }
  function rollCall(start) {
    script.push({ t: start, fn: function () { talker = 'The Anchor'; say('The Anchor', 'Panel, your call on: ' + D.title, 3400); verdictEl.className = 'verdict'; drawWall(null); } });
    D.voters.forEach(function (v, i) {
      script.push({ t: start + 3.6 + i * 2.6, fn: function () {
        var u = people[v.name].userData; setCard(u, v.vote); u.raise = performance.now() / 1000; talker = v.name;
        say(v.name, (v.vote === true ? 'BET. ' : v.vote === false ? 'PASS. ' : 'No call. ') + v.why, 3000);
      } });
    });
    var end = start + 3.6 + D.voters.length * 2.6 + 0.6;
    script.push({ t: end, fn: function () {
      var yes = D.voters.filter(function (v) { return v.vote === true; }).length;
      talker = 'The Anchor';
      say('The Anchor', D.approved ? 'The desk says BET IT, ' + yes + ' to ' + (D.voters.length - yes) + '.' : 'The desk passes on this one, ' + yes + ' to ' + (D.voters.length - yes) + '.', 4200);
      verdictEl.textContent = D.approved ? 'BET IT ✓' : 'PASS ✗'; verdictEl.className = 'verdict on ' + (D.approved ? 'bet' : 'pass');
      drawWall(D.approved ? 'BET' : 'PASS');
      Object.keys(people).forEach(function (n) { people[n].userData.raise = 0; });
    } });
    return end + 5;
  }
  function plan(from) {
    script = [];
    var t = from + 1;
    for (var i = 0; i < 3; i++) { chatter(t); t += 3.6; }
    if (D.voters.some(function (v) { return v.vote !== null; })) t = rollCall(t);
    if (D.party) {
      script.push({ t: t, fn: function () { startParty(); } }); t += 7;
    }
    script.push({ t: t + 2, fn: function () { plan(performance.now() / 1000 - T0); } });
  }
  // party: confetti + everyone stands and cheers
  var confetti = null, partyUntil = 0;
  function startParty() {
    partyUntil = performance.now() / 1000 + 6.5;
    banner.textContent = '🎉 CASHED: ' + D.party.title + ' ' + D.party.detail; banner.style.display = 'block';
    talker = 'The Anchor'; say('The Anchor', 'That one CASHED! ' + D.party.detail, 3600);
    setTimeout(function () { say('The Skeptic', 'Fine. That one was good.', 3000); }, 1500);
    setTimeout(function () { say('The Sharp', 'Value wins over time.', 3000); }, 2600);
    if (!confetti) {
      var n = 300, g = new THREE.BufferGeometry(), p = new Float32Array(n * 3), c = new Float32Array(n * 3), cols = [0xf4c84a, 0x45e39b, 0x4cb7ff, 0xff6b6b, 0xc9a7ff];
      for (var i = 0; i < n; i++) { p[i * 3] = (Math.random() - .5) * 10; p[i * 3 + 1] = 6 + Math.random() * 4; p[i * 3 + 2] = -2 + (Math.random() - .5) * 4; var col = new THREE.Color(cols[i % 5]); c[i * 3] = col.r; c[i * 3 + 1] = col.g; c[i * 3 + 2] = col.b; }
      g.setAttribute('position', new THREE.BufferAttribute(p, 3)); g.setAttribute('color', new THREE.BufferAttribute(c, 3));
      confetti = new THREE.Points(g, new THREE.PointsMaterial({ size: 0.09, vertexColors: true })); S.add(confetti);
    }
    confetti.visible = true;
  }
  plan(0);

  // ── camera orbit (drag) ──
  var yaw = 0, drag = null;
  cv.addEventListener('pointerdown', function (e) { drag = e.clientX; });
  window.addEventListener('pointerup', function () { drag = null; });
  window.addEventListener('pointermove', function (e) { if (drag != null) { yaw = Math.max(-0.5, Math.min(0.5, yaw + (e.clientX - drag) * 0.004)); drag = e.clientX; } });
  document.querySelectorAll('.seat').forEach(function (btn, i) {
    btn.addEventListener('click', function () { var v = D.voters[i]; say(v.name, v.why, 4500); talker = v.name; });
  });

  function resize() { var w = stage.clientWidth, h = stage.clientHeight; R.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); }
  window.addEventListener('resize', resize); resize();
  var pos = new THREE.Vector3();
  function frame() {
    var now = performance.now() / 1000, t = now - T0;
    while (script.length && script[0].t <= t) script.shift().fn();
    var sway = Math.sin(t * 0.15) * 0.12 + yaw;
    var dist = small ? 6.3 : 6.9;
    cam.position.set(Math.sin(sway) * dist, small ? 2.5 : 2.6, -2.6 + Math.cos(sway) * dist);
    cam.lookAt(0, small ? 1.65 : 1.85, -4.2);
    var party = now < partyUntil;
    if (!party && banner.style.display === 'block') { banner.style.display = 'none'; if (confetti) confetti.visible = false; }
    Object.keys(people).forEach(function (n) {
      var g = people[n], u = g.userData;
      u.torso.rotation.z = Math.sin(t * 0.9 + u.phase) * 0.02;
      // look at whoever is talking
      var target = talker && people[talker] && talker !== n ? people[talker].position.x - g.position.x : 0;
      u.head.rotation.y += ((Math.atan2(target, 3) * 0.9) - u.head.rotation.y) * 0.06;
      u.mouth.scale.y = talker === n && bubbles[n].classList.contains('on') ? 1 + Math.abs(Math.sin(t * 14)) * 2.5 : 1;
      var raising = u.raise && now - u.raise < 3.2;
      var wantR = party ? -2.6 : raising ? -2.3 : -0.9, wantL = party ? -2.6 : -0.9;
      u.armR.rotation.x += (wantR - u.armR.rotation.x) * 0.12; u.armL.rotation.x += (wantL - u.armL.rotation.x) * 0.12;
      u.card.visible = raising;
      g.position.y += ((party ? 0.18 + Math.abs(Math.sin(t * 6 + u.phase)) * 0.12 : 0) - g.position.y) * 0.2;
    });
    if (confetti && confetti.visible) {
      var p = confetti.geometry.attributes.position;
      for (var i = 0; i < p.count; i++) { var y = p.getY(i) - 0.035; if (y < 0) y = 7 + Math.random() * 2; p.setY(i, y); p.setX(i, p.getX(i) + Math.sin(t * 2 + i) * 0.004); }
      p.needsUpdate = true;
    }
    drawLed(t);
    R.render(S, cam); placeBubbles();
    requestAnimationFrame(frame);
  }
  frame();
})();
"""
