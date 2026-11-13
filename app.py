"""Crop Monitor: server for the AI-enhanced IoT crop disease framework (prototype).

Receives readings from the ESP32 node, scores disease risk, applies the
intervention logic of Section 3.11 and serves the farmer web interface.
The MobileNet endpoint (/api/predict) stays disabled until a trained model is added.
"""
import os
import sqlite3
import time
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('CROPDB', os.path.join(HERE, 'crop.db'))

# Adjustable thresholds (kept in one place, as described in Section 4.3.5)
CFG = dict(humid_high=85, wet_high=60, temp_lo=20, temp_hi=30, soil_low=30, soil_excess=80)

app = Flask(__name__)


def now():
    return datetime.now().isoformat(timespec='seconds')


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sensor_readings(
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME,
        temperature REAL, humidity REAL, soil_moisture REAL, leaf_wetness REAL);
    CREATE TABLE IF NOT EXISTS predictions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, reading_id INTEGER, timestamp DATETIME,
        image_name TEXT, disease_label TEXT, confidence REAL, risk_level TEXT, action_taken TEXT,
        FOREIGN KEY(reading_id) REFERENCES sensor_readings(id));
    """)
    c.commit()
    c.close()


def risk_score(r, cfg=CFG):
    s = 0
    s += r['humidity'] >= cfg['humid_high']
    s += r['leaf_wetness'] >= cfg['wet_high']
    s += cfg['temp_lo'] <= r['temperature'] <= cfg['temp_hi']
    level = 'Low' if s <= 1 else 'Medium' if s == 2 else 'High'
    return int(s), level


def decide(disease, risk, soil, cfg=CFG):
    """Intervention logic, exactly as in the flowchart (Figure 3.5)."""
    if disease or risk == 'High':
        if soil >= cfg['soil_excess']:
            return dict(alert=True, sprayer=True, pump=False,
                        note='Alert farmer, run sprayer and pause irrigation')
        return dict(alert=True, sprayer=True, pump=False, note='Alert farmer and run sprayer')
    if soil < cfg['soil_low']:
        return dict(alert=False, sprayer=False, pump=True, note='Switch pump on')
    return dict(alert=False, sprayer=False, pump=False, note='Continue monitoring')


def latest_disease(c):
    row = c.execute('SELECT * FROM predictions WHERE image_name IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    if not row:
        return None
    age = (datetime.now() - datetime.fromisoformat(row['timestamp'])).total_seconds()
    if age < 1800 and row['disease_label'] != 'Healthy':
        return row['disease_label']
    return None


def alert_message(r):
    return (f"ALERT: {r['risk_level']} disease risk. Humidity {r['humidity']:.0f}%, "
            f"leaf wetness {r['leaf_wetness']:.0f}%, temperature {r['temperature']:.1f} C. "
            f"System action: {r['action_taken']}. Please inspect the crop for early symptoms.")


@app.post('/api/sensor')
def api_sensor():
    d = request.get_json(force=True)
    try:
        r = {k: float(d[k]) for k in ('temperature', 'humidity', 'soil_moisture', 'leaf_wetness')}
    except (KeyError, TypeError, ValueError):
        return jsonify(error='temperature, humidity, soil_moisture and leaf_wetness are required numbers'), 400
    ts = d.get('timestamp') or now()
    c = db()
    rid = c.execute('INSERT INTO sensor_readings(timestamp,temperature,humidity,soil_moisture,leaf_wetness) '
                    'VALUES(?,?,?,?,?)', (ts, r['temperature'], r['humidity'], r['soil_moisture'],
                                          r['leaf_wetness'])).lastrowid
    score, level = risk_score(r)
    dis = latest_disease(c)
    a = decide(dis is not None, level, r['soil_moisture'])
    c.execute('INSERT INTO predictions(reading_id,timestamp,image_name,disease_label,confidence,risk_level,'
              'action_taken) VALUES(?,?,?,?,?,?,?)', (rid, ts, None, dis, None, level, a['note']))
    c.commit()
    c.close()
    return jsonify(risk=level, score=score, disease=dis, **a)


@app.post('/api/test/disease')
def api_test_disease():
    """Test hook: injects a simulated model output so the decision logic can be tested without images."""
    if os.environ.get('CROP_TEST') != '1':
        return jsonify(error='disabled'), 403
    label = (request.get_json(force=True) or {}).get('label', 'Healthy')
    c = db()
    c.execute('INSERT INTO predictions(timestamp,image_name,disease_label,risk_level,action_taken) '
              'VALUES(?,?,?,?,?)', (now(), 'simulated_input', label, None, 'simulated model output'))
    c.commit()
    c.close()
    return jsonify(ok=True)


@app.post('/api/predict')
def api_predict():
    return jsonify(error='MobileNet model not loaded. Add the trained model file to enable diagnosis.'), 503


@app.get('/api/status')
def api_status():
    c = db()
    reading = c.execute('SELECT * FROM sensor_readings ORDER BY id DESC LIMIT 1').fetchone()
    cycle = c.execute('SELECT * FROM predictions WHERE image_name IS NULL ORDER BY id DESC LIMIT 1').fetchone()
    disease = c.execute('SELECT * FROM predictions WHERE image_name IS NOT NULL AND image_name != "simulated_input" '
                        'ORDER BY id DESC LIMIT 1').fetchone()
    al = c.execute("SELECT p.risk_level, p.action_taken, p.timestamp, r.temperature, r.humidity, r.leaf_wetness "
                   "FROM predictions p JOIN sensor_readings r ON r.id = p.reading_id "
                   "WHERE p.action_taken LIKE 'Alert%' ORDER BY p.id DESC LIMIT 1").fetchone()
    c.close()
    note = cycle['action_taken'] if cycle else None
    return jsonify(
        reading=dict(reading) if reading else None,
        cycle=dict(cycle) if cycle else None,
        disease=dict(disease) if disease else None,
        alert=dict(timestamp=al['timestamp'], message=alert_message(al)) if al else None,
        actuators=dict(pump=note == 'Switch pump on', sprayer=bool(note and note.startswith('Alert'))))


@app.get('/api/history')
def api_history():
    c = db()
    rows = c.execute("SELECT p.timestamp, p.risk_level, p.action_taken, r.temperature, r.humidity, "
                     "r.soil_moisture, r.leaf_wetness FROM predictions p JOIN sensor_readings r ON r.id = p.reading_id "
                     "ORDER BY p.id DESC LIMIT 60").fetchall()
    c.close()
    return jsonify([dict(x) for x in rows])


BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Crop Monitor</title>
<style>
:root{--teal:#0F6E56;--tealbg:#E1F5EE;--ink:#2C2C2A;--mute:#5F5E5A;--line:#D3D1C7;--bg:#F6F5F0}
*{box-sizing:border-box}
body{margin:0 auto;font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--ink);max-width:480px;min-height:100vh;padding-bottom:84px}
header{background:var(--tealbg);padding:18px 16px 12px;border-bottom:1px solid #9FE1CB}
h1{margin:0;font-size:20px;font-weight:600;color:#085041}
#stamp{font-size:12px;color:var(--mute)}
main{padding:6px 14px 14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.tile{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px;text-align:center}
.lab{font-size:12px;color:var(--mute)}
.val{font-size:24px;font-weight:600;margin-top:4px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:12px}
.card h2{margin:0 0 8px;font-size:12px;font-weight:500;color:var(--mute)}
.pills{display:flex;gap:8px}
.pill{flex:1;text-align:center;padding:8px 0;border-radius:20px;border:1px solid var(--line);font-size:13px;color:var(--mute);background:#fff}
.low.on{background:#EAF3DE;border-color:#3B6D11;color:#27500A;font-weight:600}
.medium.on{background:#FAEEDA;border-color:#854F0B;color:#633806;font-weight:600}
.high.on{background:#FCEBEB;border-color:#A32D2D;color:#791F1F;font-weight:600}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.chip{padding:6px 12px;border-radius:16px;font-size:13px;font-weight:600;background:#F1EFE8;color:var(--mute)}
.chip.on{background:var(--tealbg);color:#085041;box-shadow:inset 0 0 0 1px var(--teal)}
.alert{border-color:#A32D2D;background:#FCEBEB}
.alert h2{color:#791F1F}
.small{font-size:13px;color:var(--mute);margin-top:8px;line-height:1.5}
.btn{display:block;width:100%;padding:12px;border-radius:10px;border:1px solid var(--teal);background:#fff;color:#085041;font-size:15px;font-weight:600;text-align:center}
.btn.main{background:var(--teal);color:#fff}
.drop{border:1.5px dashed #888780;border-radius:12px;background:#F1EFE8;height:190px;display:flex;align-items:center;justify-content:center;color:var(--mute);font-size:14px;margin-top:12px;overflow:hidden}
.drop img{max-width:100%;max-height:100%}
.item{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-top:8px;display:flex;justify-content:space-between;gap:8px}
.item.al{border-color:#E24B4A;background:#FFF7F7}
.item b{font-size:13px}
.item .m{font-size:12px;color:var(--mute);margin-top:3px;line-height:1.45}
.tag{font-size:12px;font-weight:600;padding:3px 10px;border-radius:12px;height:fit-content}
.tag.low{background:#EAF3DE;color:#27500A}.tag.medium{background:#FAEEDA;color:#633806}.tag.high{background:#FCEBEB;color:#791F1F}
.filters{display:flex;gap:8px;margin-top:12px}
.f{flex:1;text-align:center;padding:7px 0;border-radius:18px;border:1px solid var(--line);background:#fff;font-size:13px;color:var(--mute)}
.f.on{background:var(--tealbg);border-color:var(--teal);color:#085041;font-weight:600}
nav{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:480px;display:flex;gap:8px;padding:10px 14px 14px;background:#fff;border-top:1px solid var(--line)}
nav a{flex:1;text-align:center;padding:10px 0;border-radius:10px;border:1px solid var(--line);color:var(--mute);text-decoration:none;font-size:13px}
nav a.on{background:var(--tealbg);border-color:var(--teal);color:#085041;font-weight:600}
</style></head><body>
<header><h1>{{title}}</h1><span id="stamp">&nbsp;</span></header>
<main>{{body|safe}}</main>
<nav><a href="/" class="{{'on' if active=='d'}}">Dashboard</a><a href="/diagnose" class="{{'on' if active=='g'}}">Diagnose</a><a href="/history" class="{{'on' if active=='h'}}">History</a></nav>
<script>
const $=id=>document.getElementById(id);
const fmtT=ts=>new Date(ts).toLocaleString('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const num=(v,u)=>v==null?'--':(Math.round(v*10)/10)+u;
{{script|safe}}
</script></body></html>"""

DASH_BODY = """
<div class="grid">
<div class="tile"><div class="lab">Temperature</div><div class="val" id="t">--</div></div>
<div class="tile"><div class="lab">Humidity</div><div class="val" id="h">--</div></div>
<div class="tile"><div class="lab">Soil moisture</div><div class="val" id="s">--</div></div>
<div class="tile"><div class="lab">Leaf wetness</div><div class="val" id="w">--</div></div>
</div>
<div class="card"><h2>Disease risk level</h2><div class="pills">
<div class="pill low" id="p-low">Low</div><div class="pill medium" id="p-medium">Medium</div><div class="pill high" id="p-high">High</div></div></div>
<div class="card"><h2>Latest leaf diagnosis</h2><div id="dis" class="small" style="margin:0">No image diagnosed yet</div></div>
<div class="card"><h2>Field equipment</h2><div class="row"><span class="chip" id="pump">Pump OFF</span><span class="chip" id="spr">Sprayer OFF</span></div>
<div class="small" id="note">Waiting for data</div></div>
<div class="card alert" id="alert-card" style="display:none"><h2>Latest alert</h2><div id="al-msg" style="font-size:14px;line-height:1.5"></div><div class="small" id="al-time"></div></div>
"""
DASH_JS = """
async function load(){
 const s=await (await fetch('/api/status')).json();
 if(s.reading){$('t').textContent=num(s.reading.temperature,' °C');$('h').textContent=num(s.reading.humidity,' %');
  $('s').textContent=num(s.reading.soil_moisture,' %');$('w').textContent=num(s.reading.leaf_wetness,' %');
  $('stamp').textContent='Last reading: '+fmtT(s.reading.timestamp);}
 ['low','medium','high'].forEach(k=>$('p-'+k).classList.toggle('on',!!s.cycle&&s.cycle.risk_level.toLowerCase()===k));
 if(s.disease){$('dis').innerHTML='<b>'+s.disease.disease_label+'</b><br>Confidence: '+Math.round(s.disease.confidence*100)+'%';}
 $('pump').textContent='Pump '+(s.actuators.pump?'ON':'OFF');$('pump').classList.toggle('on',s.actuators.pump);
 $('spr').textContent='Sprayer '+(s.actuators.sprayer?'ON':'OFF');$('spr').classList.toggle('on',s.actuators.sprayer);
 $('note').textContent=s.cycle?s.cycle.action_taken:'Waiting for data';
 const lastIsAlert=s.cycle&&s.cycle.action_taken.startsWith('Alert');
 $('alert-card').style.display=(s.alert&&lastIsAlert)?'block':'none';
 if(s.alert){$('al-msg').textContent=s.alert.message;$('al-time').textContent=fmtT(s.alert.timestamp);}
}
load();setInterval(load,3000);
"""
DIAG_BODY = """
<div class="drop" id="drop"><span id="ph">No leaf photo selected</span></div>
<input type="file" id="file" accept="image/*" capture="environment" style="display:none">
<div class="row" style="margin-top:12px"><label class="btn" style="flex:1" for="file">Take or choose photo</label></div>
<button class="btn main" id="go" style="margin-top:12px">Diagnose</button>
<div class="card"><h2>Result</h2><div id="res" class="small" style="margin:0">Choose a leaf photo, then press Diagnose.</div></div>
"""
DIAG_JS = """
$('file').onchange=e=>{const f=e.target.files[0];if(!f)return;const u=URL.createObjectURL(f);$('drop').innerHTML='<img src="'+u+'">';};
$('go').onclick=async()=>{const f=$('file').files[0];if(!f){$('res').textContent='Please choose a leaf photo first.';return;}
 const fd=new FormData();fd.append('image',f);$('res').textContent='Analysing...';
 const r=await fetch('/api/predict',{method:'POST',body:fd});const j=await r.json();
 $('res').innerHTML=r.ok?'<b>'+j.label+'</b><br>Confidence: '+Math.round(j.confidence*100)+'%<br>'+(j.advice||''):j.error;};
"""
HIST_BODY = """
<div class="filters"><div class="f on" id="f-all">All</div><div class="f" id="f-alert">Alerts only</div><div class="f" id="f-pump">Irrigation</div></div>
<div id="list"></div>
"""
HIST_JS = """
let rows=[],mode='all';
function draw(){
 const out=rows.filter(r=>mode==='all'||(mode==='alert'&&r.action_taken.startsWith('Alert'))||(mode==='pump'&&r.action_taken.includes('pump')));
 $('list').innerHTML=out.map(r=>'<div class="item'+(r.action_taken.startsWith('Alert')?' al':'')+'"><div><b>'+fmtT(r.timestamp)+'</b>'
 +'<div class="m">'+num(r.temperature,' °C')+' · '+num(r.humidity,'% RH')+' · soil '+num(r.soil_moisture,'%')+' · leaf '+num(r.leaf_wetness,'%')+'</div>'
 +'<div class="m">'+r.action_taken+'</div></div><span class="tag '+r.risk_level.toLowerCase()+'">'+r.risk_level+'</span></div>').join('');
}
['all','alert','pump'].forEach(m=>{$('f-'+m).onclick=()=>{mode=m;['all','alert','pump'].forEach(x=>$('f-'+x).classList.toggle('on',x===m));draw();};});
fetch('/api/history').then(r=>r.json()).then(j=>{rows=j;draw();});
"""


def page(title, body, js, active):
    return render_template_string(BASE, title=title, body=body, script=js, active=active)


@app.get('/')
def home():
    return page('Crop monitor', DASH_BODY, DASH_JS, 'd')


@app.get('/diagnose')
def diagnose():
    return page('Diagnose a leaf', DIAG_BODY, DIAG_JS, 'g')


@app.get('/history')
def history():
    return page('History', HIST_BODY, HIST_JS, 'h')


init_db()

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 5000)), threaded=True)
