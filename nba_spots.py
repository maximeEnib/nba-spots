#!/usr/bin/env python3
"""Spots NBA : stats joueurs (ESPN) + cotes indicatives (The Odds API) -> nba_spots.html

Installation : pip install requests
Clé gratuite  : https://the-odds-api.com  (export ODDS_API_KEY=ta_cle)
Usage         : python nba_spots.py            (matchs restants jusqu'à J+1 inclus, heure de Paris)
                python nba_spots.py --days 0   (ce soir seulement) / --days 2 (jusqu'à J+2)
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


def load_logs_csv(teams, n=20):
    """Plan B : box scores déjà compilés (sportsdataverse, source ESPN) publiés sur GitHub, sans blocage."""
    import csv, io, requests
    now = datetime.now(PARIS)
    y = (now.year if now.month >= 10 else now.year - 1) + 1
    urls = [f"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/player_box_{y}.csv",
            f"https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/player_box/csv/player_box_{y}.csv"]
    text, err = None, ""
    for u in urls:
        try:
            r = requests.get(u, headers=HDR, timeout=120)
            err = f"HTTP {r.status_code} sur {u}"
            r.raise_for_status()
            text = r.content.decode("utf-8-sig")
            break
        except Exception as e:
            err = err or str(e)
    if text is None:
        raise RuntimeError(f"fichier de stats introuvable ({err})")
    rows = []
    for d in csv.DictReader(io.StringIO(text)):
        if str(d.get("season_type", "2")) not in ("2", "2.0"):
            continue
        if str(d.get("did_not_play", "")).lower() == "true":
            continue
        try:
            if float(d["minutes"]) <= 0:
                continue
            rows.append((d["game_date"], d["athlete_display_name"],
                         {"PTS": int(float(d["points"])), "REB": int(float(d["rebounds"])),
                          "AST": int(float(d["assists"]))}))
        except (ValueError, KeyError):
            continue
    if not rows:
        raise RuntimeError("fichier de stats vide")
    rows.sort(key=lambda r: r[0], reverse=True)
    for _, name, row in rows:
        LOGS.setdefault(norm(name), []).append(row)
    print(f"Plan B : {len(LOGS)} joueurs, dernier match du {rows[0][0]}", flush=True)


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
    lines = {"player_points": (10, 30), "player_rebounds": (3, 10), "player_assists": (2, 8)}
    for t in ("Celtics @ Knicks", "Lakers @ Nuggets", "Bucks @ Heat"):
        props = {}
        for i in "ABCD":
            name = f"Joueur {i} ({t.split()[0]})"
            LOGS[norm(name)] = [{"PTS": random.randint(5, 35), "REB": random.randint(1, 14),
                                 "AST": random.randint(0, 11)} for _ in range(20)]
            for mk, (lo, hi) in lines.items():
                line = random.randint(lo, hi) + .5
                for side in ("Over", "Under"):
                    props[(name, mk, side, line)] = (round(random.uniform(1.3, 2.2), 2), "Démo")
        out.append({"t": t, "h": "02:00", "spots": build_spots(props, {})})
    return out


def collect_logs(matches):
    logs = {}
    for m in matches:
        for sp in m["spots"]:
            j = sp["j"]
            if j not in logs:
                logs[j] = [[g["PTS"], g["REB"], g["AST"]] for g in LOGS.get(norm(j), [])[:20]]
    return logs


PAGE = r"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spots NBA</title><style>
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#e3e3e6;--acc:#1a56db;--accfg:#fff;--ok:#0a7d3b;--bad:#c62828}
@media(prefers-color-scheme:dark){:root{--bg:#121214;--fg:#f2f2f4;--mut:#9a9aa0;--line:#2c2c30;--acc:#5b8cff;--accfg:#0b1020;--ok:#4cd68a;--bad:#ff6b6b}}
*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;padding:12px;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}
h1{font-size:18px;margin:0 0 4px}h2{font-size:15px;margin:16px 0 6px}small{display:block;color:var(--mut);font-size:11px}
.row{display:flex;gap:6px;overflow-x:auto;padding-bottom:8px;align-items:center}.row button,.row label{flex:none}
button{background:none;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:inherit;text-align:left}
button.on{background:var(--acc);color:var(--accfg);border-color:var(--acc)}button.on small{color:inherit}
button.m{display:flex;justify-content:space-between;align-items:center;gap:8px;width:100%;margin:0 0 6px;padding:11px 12px}
button.add{padding:2px 9px}
input,select,textarea{background:none;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px;font:inherit}
.row input{width:80px}
.wrap{overflow-x:auto;margin-bottom:10px}table{width:100%;border-collapse:collapse}th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-size:12px;color:var(--mut)}td b{color:var(--ok)}p{color:var(--mut);font-size:12px}
.bet{border:1px solid var(--line);border-radius:10px;padding:10px;margin:0 0 8px}.bet .row{padding:6px 0 0}
.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:6px 0}.kpi div{border:1px solid var(--line);border-radius:10px;padding:8px;text-align:center}.kpi b{display:block;font-size:15px}
.pos{color:var(--ok)}.neg{color:var(--bad)}
.ov{position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:flex-end;justify-content:center;z-index:5}
.card{background:var(--bg);width:100%;max-width:520px;max-height:92vh;overflow:auto;padding:14px;border-radius:14px 14px 0 0}
.f label{display:block;font-size:12px;color:var(--mut);margin:8px 0 0}.f label input,.f label select{display:block;width:100%;margin-top:3px;font-size:16px;color:var(--fg)}
textarea{width:100%;font:12px monospace}
</style></head><body>
<h1>Spots NBA – __DAY__</h1>
<small>Mis à jour le __NOW__ __LINK__</small>
<div class="row" style="margin-top:8px"><button id="t1" onclick="go(1)">Spots</button><button id="t2" onclick="go(2)">Mes paris</button></div>
<div id="v1"><div class="row"><span id="wins"></span><label>Cote min <input id="min" type="number" step="0.05" value="1.5"></label></div>
<div id="list"></div>
<p>Proba = fréquence à laquelle la ligne a été battue sur les N derniers matchs (pas une prédiction). Cotes indicatives, meilleure cote trouvée parmi les bookmakers interrogés.</p></div>
<div id="v2" hidden></div>
<div class="ov" id="ov" hidden><div class="card f" id="card"></div></div>
<script>
const D=__DATA__;const K="nbaBets.v1";const $=s=>document.querySelector(s);
const STATS={"Points":[0],"Rebonds":[1],"Passes":[2],"Pts + Passes":[0,2],"Pts + Rebonds":[0,1],"Rebonds + Passes":[1,2],"Pts + Reb + Passes":[0,1,2]};
let view=1,cur=-1,W=10,MIN=1.5,SP=[],formMatch="",bets=load();
function load(){try{const a=JSON.parse(localStorage.getItem(K)||"[]");return Array.isArray(a)?a:[]}catch(e){return[]}}
function save(){try{localStorage.setItem(K,JSON.stringify(bets))}catch(e){}}
const esc=s=>String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
const eur=x=>(x<0?"−":"")+Math.abs(x).toFixed(2)+" €";
const pc=x=>x==null?"–":x+" %";
function calcP(j,stat,side,line,w){const lg=D.logs[j],c=STATS[stat];line=parseFloat(line);
if(!lg||!c||isNaN(line))return null;const g=lg.slice(0,w);if(!g.length)return null;
const h=g.filter(x=>{const v=c.reduce((a,i)=>a+x[i],0);return side=="Over"?v>line:v<line}).length;
return{p:Math.round(100*h/g.length),n:g.length}}
const profit=b=>b.r=="W"?b.st*(b.o-1):b.r=="L"?-b.st:0;
function go(v){view=v;render()}
function render(){
$("#t1").className=view==1?"on":"";$("#t2").className=view==2?"on":"";
$("#v1").hidden=view!=1;$("#v2").hidden=view!=2;
view==1?renderSpots():renderBets()}
const sp=m=>m.spots.filter(s=>s["p"+W]!=null&&s.o>=MIN).sort((a,b)=>b["p"+W]-a["p"+W]||b.o-a.o);
function renderSpots(){SP=[];
$("#wins").innerHTML=[5,10,20].map(n=>`<button class="${n==W?"on":""}" onclick="W=${n};render()">${n} matchs</button>`).join(" ");
if(!D.matches.length){$("#list").innerHTML="<p>Aucun match.</p>";return}
$("#list").innerHTML=D.matches.map((m,i)=>{const r=sp(m),o=i==cur;
let h=`<button class="m ${o?"on":""}" onclick="cur=${o?-1:i};render()"><span>${m.t}<small>${m.h}</small></span><span>${r.length} spots ${o?"▴":"▾"}</span></button>`;
if(o)h+=`<div class="wrap"><table><thead><tr><th>Joueur</th><th>Stat</th><th>Pari</th><th>Proba</th><th>Cote</th><th></th></tr></thead><tbody>${r.map(s=>{const k=SP.push(s)-1;return `<tr><td>${s.j}</td><td>${s.m}</td><td>${s.s=="Over"?"Plus de":"Moins de"} ${s.l}</td><td><b>${s["p"+W]} %</b><small>${s["n"+W]} matchs</small></td><td>${s.o.toFixed(2)}<small>${s.b}</small></td><td><button class="add" onclick="addSpot(${k},${i})">+</button></td></tr>`}).join("")||"<tr><td colspan=6>Aucun spot à cette cote.</td></tr>"}</tbody></table></div>`;
return h}).join("")}
function addSpot(k,i){const s=SP[k];openForm({j:s.j,m:s.m,s:s.s,l:s.l,o:s.o,w:W},D.matches[i].t)}
function openForm(i,match){formMatch=match||"";
const f=Object.assign({j:"",m:"Points",s:"Over",l:"",o:"",st:bets.length?bets[bets.length-1].st:5,w:W},i);
$("#card").innerHTML=`<h2>${esc(formMatch||"Pari manuel")}</h2>
<label>Joueur<input id="f_j" list="pl" value="${esc(f.j)}" oninput="autoP()"></label>
<datalist id="pl">${Object.keys(D.logs).map(k=>`<option value="${esc(k)}">`).join("")}</datalist>
<label>Stat (liste ou texte libre)<input id="f_m" list="sl" value="${esc(f.m)}" oninput="autoP()"></label>
<datalist id="sl">${Object.keys(STATS).map(k=>`<option value="${k}">`).join("")}</datalist>
<label>Pari<select id="f_s" onchange="autoP()"><option value="Over" ${f.s=="Over"?"selected":""}>Plus de</option><option value="Under" ${f.s=="Under"?"selected":""}>Moins de</option></select></label>
<label>Ligne<input id="f_l" type="number" step="0.5" inputmode="decimal" value="${f.l}" oninput="autoP()"></label>
<label>Cote<input id="f_o" type="number" step="0.01" inputmode="decimal" value="${f.o}"></label>
<label>Mise (€)<input id="f_st" type="number" step="0.5" inputmode="decimal" value="${f.st}"></label>
<label>Fenêtre de calcul<select id="f_w" onchange="autoP()">${[5,10,20].map(n=>`<option value="${n}" ${n==f.w?"selected":""}>${n} matchs</option>`).join("")}</select></label>
<label>Proba initiale (%) <small id="f_n"></small><input id="f_p" type="number" inputmode="decimal" value=""></label>
<div class="row" style="margin-top:12px"><button class="on" onclick="saveBet()">Enregistrer</button><button onclick="closeForm()">Annuler</button></div>`;
$("#ov").hidden=false;autoP()}
function autoP(){const c=calcP($("#f_j").value.trim(),$("#f_m").value.trim(),$("#f_s").value,$("#f_l").value,+$("#f_w").value);
$("#f_n").textContent=c?`(calculée sur ${c.n} matchs)`:"(à saisir si tu la connais)";if(c)$("#f_p").value=c.p}
function closeForm(){$("#ov").hidden=true}
function saveBet(){const j=$("#f_j").value.trim(),m=$("#f_m").value.trim(),l=parseFloat($("#f_l").value),o=parseFloat($("#f_o").value),st=parseFloat($("#f_st").value),p=parseFloat($("#f_p").value);
if(!j||!m||isNaN(l)||!(o>1)||!(st>0)){alert("Joueur, stat, ligne, cote (> 1) et mise sont requis.");return}
const c=calcP(j,m,$("#f_s").value,l,+$("#f_w").value);
bets.push({id:Date.now(),t:new Date().toISOString(),match:formMatch,j,m,s:$("#f_s").value,l,o,st,p:isNaN(p)?null:p,w:+$("#f_w").value,n:c?c.n:null,v:null,r:null});
save();closeForm();render()}
function settle(id){const b=bets.find(x=>x.id==id),v=parseFloat($("#v"+id).value);if(!b||isNaN(v))return;
b.v=v;b.r=v==b.l?"V":((b.s=="Over"?v>b.l:v<b.l)?"W":"L");save();render()}
function mark(id,r){const b=bets.find(x=>x.id==id);if(b){b.r=r;save();render()}}
function reopen(id){const b=bets.find(x=>x.id==id);if(b){b.r=null;b.v=null;save();render()}}
function del(id){if(confirm("Supprimer ce pari ?")){bets=bets.filter(x=>x.id!=id);save();render()}}
const bdesc=b=>`${b.j} · ${b.s=="Over"?"Plus de":"Moins de"} ${b.l} ${b.m}`;
const bmeta=b=>`cote ${b.o.toFixed(2)} · mise ${b.st} € · proba ${pc(b.p)}${b.n?" ("+b.n+" m.)":""}${b.match?" · "+b.match:""}`;
const pendCard=b=>`<div class="bet"><div>${esc(bdesc(b))}<small>${esc(bmeta(b))}</small></div>
<div class="row"><input id="v${b.id}" type="number" step="0.5" inputmode="decimal" placeholder="Valeur réelle"><button class="on" onclick="settle(${b.id})">OK</button></div>
<div class="row"><button onclick="mark(${b.id},'W')">Gagné</button><button onclick="mark(${b.id},'L')">Perdu</button><button onclick="mark(${b.id},'V')">Annulé</button><button onclick="del(${b.id})">Suppr.</button></div></div>`;
const doneRow=b=>`<div class="bet"><div>${b.r=="W"?"✅":b.r=="L"?"❌":"➖"} ${esc(bdesc(b))}<small>${esc(bmeta(b))}${b.v!=null?" · réel "+b.v:""} · <span class="${profit(b)>=0?"pos":"neg"}">${eur(profit(b))}</span></small></div>
<div class="row"><button onclick="reopen(${b.id})">Rouvrir</button><button onclick="del(${b.id})">Suppr.</button></div></div>`;
function grp(list,key,order){const m={};list.forEach(b=>{const k=key(b);(m[k]=m[k]||[]).push(b)});
const ks=order||Object.keys(m).sort();
return `<div class="wrap"><table><thead><tr><th></th><th>Paris</th><th>Réussite</th><th>Proba moy.</th><th>ROI</th></tr></thead><tbody>`+ks.filter(k=>m[k]).map(k=>{const g=m[k],w=g.filter(b=>b.r=="W").length,st=g.reduce((a,b)=>a+b.st,0),pf=g.reduce((a,b)=>a+profit(b),0),ps=g.filter(b=>b.p!=null),ap=ps.length?Math.round(ps.reduce((a,b)=>a+b.p,0)/ps.length):null;
return `<tr><td>${esc(k)}</td><td>${g.length}</td><td>${Math.round(100*w/g.length)} %</td><td>${pc(ap)}</td><td class="${pf>=0?"pos":"neg"}">${(100*pf/st).toFixed(0)} %</td></tr>`}).join("")+`</tbody></table></div>`}
const PB=["< 50 %","50–59 %","60–69 %","70–79 %","≥ 80 %","Proba inconnue"];
const pb=b=>b.p==null?PB[5]:b.p<50?PB[0]:b.p<60?PB[1]:b.p<70?PB[2]:b.p<80?PB[3]:PB[4];
const OB=["< 1,50","1,50–1,79","1,80–2,19","≥ 2,20"];
const ob=b=>b.o<1.5?OB[0]:b.o<1.8?OB[1]:b.o<2.2?OB[2]:OB[3];
function analysis(d){if(!d.length)return "<p>Règle quelques paris pour voir l’analyse.</p>";
return (d.length<30?`<p>Échantillon encore petit (${d.length} paris) : à lire avec prudence.</p>`:"")+
`<h3>Par proba initiale</h3>`+grp(d,pb,PB)+`<p>Si la réussite est nettement sous la proba moyenne, la proba affichée est trop optimiste.</p><h3>Par type de stat</h3>`+grp(d,b=>b.m)+`<h3>Par cote</h3>`+grp(d,ob,OB)+`<h3>Plus de / Moins de</h3>`+grp(d,b=>b.s=="Over"?"Plus de":"Moins de")}
function renderBets(){
const done=bets.filter(b=>b.r&&b.r!="V"),pend=bets.filter(b=>!b.r),all=bets.filter(b=>b.r);
const stk=done.reduce((a,b)=>a+b.st,0),pf=done.reduce((a,b)=>a+profit(b),0),w=done.filter(b=>b.r=="W").length;
$("#v2").innerHTML=`<div class="row"><button class="on" onclick="openForm({})">+ Pari manuel</button><button onclick="exp()">Exporter</button><button onclick="imp()">Importer</button></div><div id="io"></div>
<div class="kpi"><div><b>${done.length}</b><small>réglés</small></div><div><b class="${pf>=0?"pos":"neg"}">${eur(pf)}</b><small>gain net</small></div><div><b>${stk?(100*pf/stk).toFixed(1)+" %":"–"}</b><small>ROI</small></div><div><b>${done.length?Math.round(100*w/done.length)+" %":"–"}</b><small>réussite</small></div></div>
<h2>En attente (${pend.length})</h2>${pend.slice().reverse().map(pendCard).join("")||"<p>Aucun pari en attente.</p>"}
<h2>Analyse</h2>${analysis(done)}
<h2>Réglés (${all.length})</h2>${all.slice().reverse().map(doneRow).join("")}`}
function exp(){const t=JSON.stringify(bets);$("#io").innerHTML=`<textarea id="ta" rows="5" readonly>${esc(t)}</textarea><p>Copié si possible. Garde ce texte quelque part pour pouvoir restaurer tes paris.</p>`;try{navigator.clipboard.writeText(t)}catch(e){}}
function imp(){$("#io").innerHTML=`<textarea id="ta" rows="5" placeholder="Colle ici l’export"></textarea><button class="on" onclick="doImp()">Remplacer mes paris par ce contenu</button>`}
function doImp(){try{const a=JSON.parse($("#ta").value);if(!Array.isArray(a))throw 0;if(!confirm("Remplacer les "+bets.length+" paris actuels ?"))return;bets=a;save();render()}catch(e){alert("Contenu invalide.")}}
$("#min").oninput=e=>{MIN=parseFloat(e.target.value)||0;render()};render();
</script></body></html>"""


JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="jusqu'à J+N inclus (0 = ce soir seulement)")
    ap.add_argument("--regions", default="us")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default="nba_spots.html")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    now = datetime.now(PARIS)
    last = (now + timedelta(days=a.days)).date()
    label = f"{now:%d/%m} → {last:%d/%m}"
    if a.demo:
        matches = demo()
    else:
        key = os.environ.get("ODDS_API_KEY") or sys.exit("Définis ODDS_API_KEY (clé gratuite sur the-odds-api.com)")
        cache, matches = {}, []
        evs = sorted(get_events(key), key=lambda e: e["local"])
        print("Dates listées par l'API :", sorted({e["local"].date().isoformat() for e in evs}))
        # matchs pas encore commencés, d'ici la fin de J+N (heure de Paris)
        sel = [e for e in evs if e["local"] > now and e["local"].date() <= last]
        if not sel:
            fut = [e for e in evs if e["local"] > now]
            if fut:
                d = fut[0]["local"].date()
                sel = [e for e in fut if e["local"].date() == d]
                print("Rien dans la période, repli sur le", d)
        if sel:
            label = f'{sel[0]["local"]:%d/%m} → {sel[-1]["local"]:%d/%m}'
        print(f"{len(sel)} matchs retenus (environ {len(sel) * len(MARKETS)} crédits Odds API)")
        teams = {e["home_team"] for e in sel} | {e["away_team"] for e in sel}
        try:
            load_logs(teams)
        except RuntimeError as ex:
            print("ESPN direct indisponible :", ex, flush=True)
            LOGS.clear()
            try:
                load_logs_csv(teams)
            except RuntimeError as ex2:
                sys.exit(f"Stats indisponibles : {ex2}")
        for ev in sel:
            print("→", ev["away_team"], "@", ev["home_team"])
            spots = build_spots(get_props(key, ev, a.regions), cache)
            l = ev["local"]
            matches.append({"t": f'{ev["away_team"]} @ {ev["home_team"]}',
                            "h": f"{JOURS[l.weekday()]} {l:%d/%m %H:%M}", "spots": spots})
    repo = os.environ.get("GITHUB_REPOSITORY")
    link = (f'· <a href="https://github.com/{repo}/actions/workflows/nba.yml">Relancer</a>'
            if repo else "")
    html = PAGE.replace("__DAY__", label).replace(
        "__NOW__", now.strftime("%d/%m à %H:%M")).replace("__LINK__", link).replace(
        "__DATA__", json.dumps({"matches": matches, "logs": collect_logs(matches)}, ensure_ascii=False))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("OK →", a.out)


if __name__ == "__main__":
    main()
