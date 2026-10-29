#!/usr/bin/env python3
"""Spots NBA : stats joueurs (ESPN) + cotes indicatives (The Odds API) -> nba_spots.html

Installation : pip install requests
Clé gratuite  : https://the-odds-api.com  (export ODDS_API_KEY=ta_cle)
Usage         : python nba_spots.py            (matchs à J+1, heure de Paris)
                python nba_spots.py --days 2   (autre jour)
                python nba_spots.py --demo     (fausses données, pour voir la page)
"""
import argparse, json, os, random, re, sys, time, unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://api.the-odds-api.com/v4/sports/basketball_nba"
MARKETS = {
    "player_points": ("Points", ("PTS",)),
    "player_rebounds": ("Rebonds", ("REB",)),
    "player_assists": ("Passes", ("AST",)),
    "player_points_assists": ("Pts + Passes", ("PTS", "AST")),
    "player_points_rebounds": ("Pts + Rebonds", ("PTS", "REB")),
    "player_rebounds_assists": ("Rebonds + Passes", ("REB", "AST")),
    "player_points_rebounds_assists": ("Pts + Reb + Passes", ("PTS", "REB", "AST")),
}
WINDOWS = (5, 10, 20)
PARIS = ZoneInfo("Europe/Paris")
STATS = {"ok": 0, "ko": 0}


def get_events(key):
    import requests
    r = requests.get(f"{API}/events", params={"apiKey": key}, timeout=30)
    r.raise_for_status()
    out = []
    for e in r.json():
        t = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")).astimezone(PARIS)
        e["local"] = t
        out.append(e)
    return out


def get_props(key, ev, regions):
    """Meilleure cote (décimale) par joueur / marché / sens / ligne."""
    import requests
    r = requests.get(f"{API}/events/{ev['id']}/odds", timeout=30, params={
        "apiKey": key, "regions": regions, "markets": ",".join(MARKETS), "oddsFormat": "decimal"})
    r.raise_for_status()
    best = {}
    for bk in r.json().get("bookmakers", []):
        for m in bk["markets"]:
            for o in m["outcomes"]:
                k = (o["description"], m["key"], o["name"], o["point"])
                if k not in best or o["price"] > best[k][0]:
                    best[k] = (o["price"], bk["title"])
    return best


HOSTS = ["https://site.api.espn.com/apis/site/v2/sports/basketball/nba",
         "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba"]
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/124.0 Safari/537.36",
       "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
       "Referer": "https://www.espn.com/", "Origin": "https://www.espn.com"}
LOGS = {}  # nom normalisé -> [{"PTS","REB","AST"}, ...] du plus récent au plus ancien


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[.'’\-]", "", s)
    return " ".join(w for w in s.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def tkey(name):
    n = name.lower().strip()
    return "los angeles clippers" if n == "la clippers" else n


def get_json(path, params=None):
    import requests
    err = ""
    for host in HOSTS + HOSTS[:1]:
        try:
            r = requests.get(host + path, params=params, headers=HDR, timeout=20)
            err = f"HTTP {r.status_code}, {r.headers.get('content-type', '?')}, début : {r.text[:120]!r}"
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if not err:
                err = str(e)
            time.sleep(1)
    raise RuntimeError(f"espn.com injoignable ({err})")


def load_logs(teams, n=20):
    """Remplit LOGS avec les box scores des n derniers matchs de chaque équipe (API publique ESPN)."""
    now = datetime.now(PARIS)
    y = now.year if now.month >= 10 else now.year - 1
    count, pick = {tkey(t): 0 for t in teams}, []
    day, stop, nreq = now.date(), date(y, 10, 1), 0
    while day >= stop and any(c < n for c in count.values()):  # on remonte jour par jour
        sb = get_json("/scoreboard", {"dates": day.strftime("%Y%m%d")})
        nreq += 1
        evs = [e for e in sb.get("events", [])
               if e["status"]["type"].get("completed") and e.get("season", {}).get("type", 2) == 2]
        for e in sorted(evs, key=lambda e: e["date"], reverse=True):
            hit = False
            for c in e["competitions"][0]["competitors"]:
                t = tkey(c["team"]["displayName"])
                if t in count and count[t] < n:
                    count[t] += 1
                    hit = True
            if hit:
                pick.append(e["id"])
        day -= timedelta(days=1)
        time.sleep(0.15)
    print(f"{nreq} jours lus, {len(pick)} box scores à lire", flush=True)
    for eid in pick:
        box = get_json("/summary", {"event": eid}).get("boxscore", {})
        for tm in box.get("players", []):
            for grp in tm.get("statistics", []):
                lab = grp.get("labels", [])
                idx = {k: lab.index(k) for k in ("MIN", "PTS", "REB", "AST") if k in lab}
                if len(idx) < 4:
                    continue
                for a in grp.get("athletes", []):
                    st = a.get("stats") or []
                    if a.get("didNotPlay") or len(st) < len(lab):
                        continue
                    try:
                        if float(st[idx["MIN"]]) <= 0:
                            continue
                        row = {k: int(st[idx[k]]) for k in ("PTS", "REB", "AST")}
                    except ValueError:
                        continue  # n'a pas joué
                    LOGS.setdefault(norm(a["athlete"]["displayName"]), []).append(row)
        time.sleep(0.2)
    if not LOGS:
        raise RuntimeError("aucune statistique récupérée")
    print(len(LOGS), "joueurs avec stats", flush=True)


def build_spots(props, cache):
    spots = []
    for (player, mk, side, line), (price, book) in props.items():
        log = LOGS.get(norm(player), [])[:20]
        if not log:
            continue
        label, comps = MARKETS[mk]
        vals = [sum(g[c] for c in comps) for g in log]
        row = {"j": player, "m": label, "s": side, "l": line, "o": round(price, 2), "b": book}
        for n in WINDOWS:
            w = vals[:n]
            hit = sum(v > line for v in w) if side == "Over" else sum(v < line for v in w)
            row[f"p{n}"] = round(100 * hit / len(w))
            row[f"n{n}"] = len(w)
        spots.append(row)
    return spots


def demo():
    random.seed(1)
    out = []
    for t in ("Celtics @ Knicks", "Lakers @ Nuggets", "Bucks @ Heat"):
        spots = []
        for p in ("Joueur A", "Joueur B", "Joueur C", "Joueur D"):
            for label, _ in MARKETS.values():
                for side in ("Over", "Under"):
                    r = {"j": p, "m": label, "s": side, "l": random.randint(4, 30) + .5,
                         "o": round(random.uniform(1.3, 2.2), 2), "b": "Démo"}
                    for n in WINDOWS:
                        r[f"p{n}"] = random.randint(10, 95)
                        r[f"n{n}"] = n
                    spots.append(r)
        out.append({"t": t, "h": "02:00", "spots": spots})
    return out


PAGE = r"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spots NBA</title><style>
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#e3e3e6;--acc:#1a56db;--accfg:#fff;--ok:#0a7d3b}
@media(prefers-color-scheme:dark){:root{--bg:#121214;--fg:#f2f2f4;--mut:#9a9aa0;--line:#2c2c30;--acc:#5b8cff;--accfg:#0b1020;--ok:#4cd68a}}
*{box-sizing:border-box}body{margin:0;padding:12px;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}
h1{font-size:18px;margin:0 0 8px}small{display:block;color:var(--mut);font-size:11px}
.row{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px}.row button,.row label{flex:none}
button{background:none;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:inherit;text-align:left}
button.on{background:var(--acc);color:var(--accfg);border-color:var(--acc)}button.on small{color:inherit}
input{width:70px;padding:6px;border:1px solid var(--line);border-radius:8px;background:none;color:var(--fg);font:inherit}
.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-size:12px;color:var(--mut)}td b{color:var(--ok)}p{color:var(--mut);font-size:12px}
</style></head><body>
<h1>Spots NBA – __DAY__</h1>
<small>Mis à jour le __NOW__ __LINK__</small>
<div class="row" id="tabs"></div>
<div class="row"><span id="wins"></span><label>Cote min <input id="min" type="number" step="0.05" value="1.5"></label></div>
<div class="wrap"><table><thead><tr><th>Joueur</th><th>Stat</th><th>Pari</th><th>Proba</th><th>Cote</th></tr></thead><tbody id="rows"></tbody></table></div>
<p>Proba = fréquence à laquelle la ligne a été battue sur les N derniers matchs (pas une prédiction). Cotes indicatives, meilleure cote trouvée parmi les bookmakers interrogés.</p>
<script>
const D=__DATA__;let cur=0,W=10,MIN=1.5;const $=s=>document.querySelector(s);
function render(){
$("#tabs").innerHTML=D.map((m,i)=>`<button class="${i==cur?"on":""}" onclick="cur=${i};render()">${m.t}<small>${m.h}</small></button>`).join("");
$("#wins").innerHTML=[5,10,20].map(n=>`<button class="${n==W?"on":""}" onclick="W=${n};render()">${n} matchs</button>`).join(" ");
const m=D[cur];if(!m){$("#rows").innerHTML="<tr><td colspan=5>Aucun match.</td></tr>";return}
const rows=m.spots.filter(s=>s["p"+W]!=null&&s.o>=MIN).sort((a,b)=>b["p"+W]-a["p"+W]||b.o-a.o);
$("#rows").innerHTML=rows.map(s=>`<tr><td>${s.j}</td><td>${s.m}</td><td>${s.s=="Over"?"Plus de":"Moins de"} ${s.l}</td><td><b>${s["p"+W]} %</b><small>${s["n"+W]} matchs</small></td><td>${s.o.toFixed(2)}<small>${s.b}</small></td></tr>`).join("")||"<tr><td colspan=5>Aucun spot à cette cote.</td></tr>";}
$("#min").oninput=e=>{MIN=parseFloat(e.target.value)||0;render()};render();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--regions", default="us")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default="nba_spots.html")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    day = (datetime.now(PARIS) + timedelta(days=a.days)).date()
    if a.demo:
        matches = demo()
    else:
        key = os.environ.get("ODDS_API_KEY") or sys.exit("Définis ODDS_API_KEY (clé gratuite sur the-odds-api.com)")
        cache, matches = {}, []
        evs = get_events(key)
        dates = sorted({e["local"].date() for e in evs})
        print("Dates listées par l'API :", [d.isoformat() for d in dates])
        if dates and day not in dates:
            day = min(dates, key=lambda d: abs((d - day).days))
            print("Rien à la date demandée, repli sur", day)
        today = [e for e in evs if e["local"].date() == day]
        teams = {e["home_team"] for e in today} | {e["away_team"] for e in today}
        try:
            load_logs(teams)
        except RuntimeError as ex:
            sys.exit(f"Stats indisponibles : {ex}")
        for ev in today:
            print("→", ev["away_team"], "@", ev["home_team"])
            spots = build_spots(get_props(key, ev, a.regions), cache)
            matches.append({"t": f'{ev["away_team"]} @ {ev["home_team"]}',
                            "h": ev["local"].strftime("%H:%M"), "spots": spots})
    repo = os.environ.get("GITHUB_REPOSITORY")
    link = (f'· <a href="https://github.com/{repo}/actions/workflows/nba.yml">Relancer</a>'
            if repo else "")
    html = PAGE.replace("__DAY__", day.strftime("%d/%m/%Y")).replace(
        "__NOW__", datetime.now(PARIS).strftime("%d/%m à %H:%M")).replace("__LINK__", link).replace(
        "__DATA__", json.dumps(matches, ensure_ascii=False))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("OK →", a.out)


if __name__ == "__main__":
    main()
