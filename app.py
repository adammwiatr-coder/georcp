import sqlite3, os, datetime, uuid
from functools import wraps
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, Response

app = Flask(__name__)
DB_PATH = 'data/georcp.db'

# --- KONFIGURACJA LOGOWANIA ADMINA ---
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'ZmienToHasloWProdukcji123!'

def check_auth(username, password):
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD

def authenticate():
    return Response(
    'Wymagane logowanie do panelu administratora.\n', 401,
    {'WWW-Authenticate': 'Basic realm="Panel Admina"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def init_db():
    if not os.path.exists('data'): os.makedirs('data')
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS workers (id INTEGER PRIMARY KEY, name TEXT, token TEXT UNIQUE, rate_work REAL, rate_travel REAL)')
    conn.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, worker_token TEXT, event_type TEXT, timestamp DATETIME, lat TEXT, lon TEXT)')
    conn.commit()
    conn.close()

init_db()

# --- INTERFEJS PRACOWNIKA ---
USER_UI = """
<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style> .btn-xl { padding: 25px; font-size: 1.3rem; width: 100%; border-radius: 15px; font-weight: bold; } </style></head>
<body class="bg-dark text-white container py-4 text-center">
    <h3 class="mb-5">Pracownik:<br><span class="text-info">{{name}}</span></h3>
    
    <div class="row g-3 mb-4">
        <div class="col-6">
            <button class="btn btn-warning btn-xl shadow action-btn" onclick="send(this, 'Start Dojazd')">START<br>DOJAZD</button>
        </div>
        <div class="col-6">
            <button class="btn btn-success btn-xl shadow action-btn" onclick="send(this, 'Start Budowa')">START<br>BUDOWA</button>
        </div>
    </div>
    
    <div class="row mt-5">
        <div class="col-12">
            <button class="btn btn-danger btn-xl shadow py-4 action-btn" onclick="send(this, 'Stop')">🛑 ZAKOŃCZ<br>AKTUALNĄ PRACĘ</button>
        </div>
    </div>

    <script>
        function send(btn, type) {
            // Blokada przycisków i info wizualne
            const allBtns = document.querySelectorAll('.action-btn');
            const originalText = btn.innerHTML;
            allBtns.forEach(b => b.disabled = true);
            btn.innerHTML = '⏳ CZEKAJ...';

            const gpsOptions = {
                enableHighAccuracy: true,
                timeout: 10000,       // Max 10 sekund szukania GPS
                maximumAge: 60000     // Akceptuj wynik z ostatnich 60 sekund
            };

            navigator.geolocation.getCurrentPosition(p => {
                fetch('/log', {method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({token:"{{token}}", type:type, lat:p.coords.latitude, lon:p.coords.longitude})})
                .then(res => res.json()).then(d => {
                    if(d.status === 'error') {
                        alert("⚠️ " + d.msg); 
                    } else {
                        alert("✅ " + d.msg); 
                    }
                })
                .catch(err => alert("Błąd połączenia z serwerem!"))
                .finally(() => {
                    btn.innerHTML = originalText;
                    allBtns.forEach(b => b.disabled = false);
                });
            }, (err) => {
                alert("BŁĄD GPS: " + err.message + ". Upewnij się, że lokalizacja w telefonie jest włączona i udostępniona przeglądarce!");
                btn.innerHTML = originalText;
                allBtns.forEach(b => b.disabled = false);
            }, gpsOptions);
        }
    </script></body></html>
"""

@app.route('/user/<token>')
def user_page(token):
    conn = sqlite3.connect(DB_PATH)
    worker = conn.execute("SELECT name FROM workers WHERE token=?", (token,)).fetchone()
    conn.close()
    if not worker: return "Błędny link!", 404
    return render_template_string(USER_UI, name=worker[0], token=token)

@app.route('/log', methods=['POST'])
def log_event():
    d = request.json
    token = d['token']
    event_type = d['type']
    
    conn = sqlite3.connect(DB_PATH)
    last_log = conn.execute("SELECT event_type FROM logs WHERE worker_token=? ORDER BY id DESC LIMIT 1", (token,)).fetchone()
    
    active_state = None
    if last_log and "Start" in last_log[0]:
        active_state = last_log[0].replace("Start ", "")
        
    now = datetime.datetime.now()
    
    if event_type == "Stop":
        if active_state is None:
            conn.close()
            return jsonify({'msg': 'BŁĄD: Nie masz żadnej aktywnej pracy do zakończenia!', 'status': 'error'})
        else:
            actual_stop_event = f"Stop {active_state}"
            conn.execute("INSERT INTO logs (worker_token, event_type, timestamp, lat, lon) VALUES (?,?,?,?,?)",
                         (token, actual_stop_event, now, d['lat'], d['lon']))
            conn.commit()
            conn.close()
            return jsonify({'msg': f'Zakończono pomyślnie: {active_state}', 'status': 'success'})

    elif "Start" in event_type:
        new_state = event_type.replace("Start ", "")
        if active_state == new_state:
            conn.close()
            return jsonify({'msg': f'BŁĄD: Jesteś już w trakcie statusu "{new_state}"!', 'status': 'error'})

        if active_state is not None:
            conn.execute("INSERT INTO logs (worker_token, event_type, timestamp, lat, lon) VALUES (?,?,?,?,?)",
                         (token, f"Stop {active_state}", now, d['lat'], d['lon']))
            now = now + datetime.timedelta(seconds=1)

        conn.execute("INSERT INTO logs (worker_token, event_type, timestamp, lat, lon) VALUES (?,?,?,?,?)",
                     (token, event_type, now, d['lat'], d['lon']))
                     
        conn.commit()
        conn.close()
        return jsonify({'msg': f'Rozpoczęto: {new_state}', 'status': 'success'})

@app.route('/admin/edit_log', methods=['POST'])
@requires_auth
def edit_log():
    start_id = request.form.get('start_id')
    stop_id = request.form.get('stop_id')
    new_date = request.form.get('date')
    new_start_str = f"{new_date} {request.form.get('start')}:00.000000"
    new_stop_str = f"{new_date} {request.form.get('stop')}:00.000000"
    
    start_dt = datetime.datetime.strptime(new_start_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
    stop_dt = datetime.datetime.strptime(new_stop_str.split('.')[0], '%Y-%m-%d %H:%M:%S')

    if start_dt >= stop_dt:
        return render_template_string("<script>alert('BŁĄD: Czas startu musi być wcześniejszy niż czas stopu!'); window.history.back();</script>")

    conn = sqlite3.connect(DB_PATH)
    worker_token = conn.execute("SELECT worker_token FROM logs WHERE id=?", (start_id,)).fetchone()[0]

    raw_logs = conn.execute("SELECT id, event_type, timestamp FROM logs WHERE worker_token=? AND timestamp LIKE ? ORDER BY timestamp ASC, id ASC", (worker_token, new_date + '%')).fetchall()
    
    sessions = []
    current_start = None
    for l_id, e_type, ts in raw_logs:
        ts_dt = datetime.datetime.strptime(ts.split('.')[0], '%Y-%m-%d %H:%M:%S')
        if "Start" in e_type:
            current_start = {'id': l_id, 'time': ts_dt}
        elif "Stop" in e_type and current_start:
            sessions.append({'start_id': current_start['id'], 'stop_id': l_id, 'start_time': current_start['time'], 'stop_time': ts_dt})
            current_start = None

    for s in sessions:
        if str(s['start_id']) == str(start_id):
            continue
        if start_dt < s['stop_time'] and stop_dt > s['start_time']:
            conn.close()
            return render_template_string(f"<script>alert('BŁĄD: Podane godziny nakładają się na inną zapisaną sesję!'); window.history.back();</script>")

    conn.execute("UPDATE logs SET timestamp = ? WHERE id = ?", (new_start_str, start_id))
    conn.execute("UPDATE logs SET timestamp = ? WHERE id = ?", (new_stop_str, stop_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('admin_panel'))

@app.route('/admin/edit_worker', methods=['POST'])
@requires_auth
def edit_worker():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE workers SET name=?, rate_work=?, rate_travel=? WHERE id=?", 
                 (request.form.get('name'), request.form.get('rate_work'), request.form.get('rate_travel'), request.form.get('worker_id')))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('admin_panel'))

@app.route('/admin/delete_worker/<int:w_id>', methods=['POST'])
@requires_auth
def delete_worker(w_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM workers WHERE id=?", (w_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('admin_panel'))

# --- PANEL ADMINA ---
@app.route('/admin', methods=['GET', 'POST'])
@requires_auth
def admin_panel():
    conn = sqlite3.connect(DB_PATH)
    
    if request.method == 'POST' and 'new_name' in request.form:
        conn.execute("INSERT INTO workers (name, token, rate_work, rate_travel) VALUES (?,?,?,?)", 
                     (request.form.get('new_name'), str(uuid.uuid4())[:8], float(request.form.get('rate_work', 0) or 0), float(request.form.get('rate_travel', 0) or 0)))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))

    filter_worker = request.args.get('worker', '')
    filter_type = request.args.get('filter_type', 'month')
    filter_val = request.args.get('filter_val', '')

    now_dt = datetime.datetime.now()
    if not filter_val:
        if filter_type == 'month': filter_val = now_dt.strftime('%Y-%m')
        elif filter_type == 'day': filter_val = now_dt.strftime('%Y-%m-%d')
        elif filter_type == 'week': filter_val = now_dt.strftime('%G-W%V')

    workers = conn.execute("SELECT * FROM workers").fetchall()
    
    if filter_type == 'day' or filter_type == 'month':
        raw_logs = conn.execute("SELECT id, worker_token, event_type, timestamp, lat, lon FROM logs WHERE timestamp LIKE ? ORDER BY timestamp ASC, id ASC", (filter_val + '%',)).fetchall()
    elif filter_type == 'week':
        try:
            start_of_week = datetime.datetime.strptime(filter_val + '-1', "%G-W%V-%u")
            end_of_week = start_of_week + datetime.timedelta(days=7)
            raw_logs = conn.execute("SELECT id, worker_token, event_type, timestamp, lat, lon FROM logs WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp ASC, id ASC", 
                                    (start_of_week.strftime('%Y-%m-%d'), end_of_week.strftime('%Y-%m-%d'))).fetchall()
        except ValueError:
            raw_logs = []
    
    report = []
    active_sessions = {}
    summary = {'total_cost': 0, 'travel_cost': 0, 'work_cost': 0, 'total_time': 0, 'travel_time': 0, 'work_time': 0}

    for log_id, token, e_type, ts, lat, lon in raw_logs:
        w_data = next(((w[1], w[3], w[4], w[2]) for w in workers if w[2] == token), None)
        if not w_data: continue
        w_name, r_work, r_travel, w_token = w_data
        if filter_worker and w_token != filter_worker: continue

        ts_dt = datetime.datetime.strptime(ts.split('.')[0], '%Y-%m-%d %H:%M:%S')
        mode = "Budowa" if "Budowa" in e_type else "Dojazd"
        key = f"{token}_{mode}"

        if "Start" in e_type:
            active_sessions[key] = {'time': ts_dt, 'lat': lat, 'lon': lon, 'start_id': log_id}
        elif "Stop" in e_type and key in active_sessions:
            s = active_sessions.pop(key)
            duration = (ts_dt - s['time']).total_seconds() / 3600
            rate = r_work if mode == "Budowa" else r_travel
            cost = duration * rate
            
            summary['total_cost'] += cost
            summary['total_time'] += duration
            if mode == "Dojazd":
                summary['travel_cost'] += cost
                summary['travel_time'] += duration
            else:
                summary['work_cost'] += cost
                summary['work_time'] += duration
            
            report.append({
                'start_id': s['start_id'], 'stop_id': log_id,
                'raw_date': s['time'].strftime('%Y-%m-%d'),
                'date': s['time'].strftime('%d.%m.%Y'),
                'worker': w_name, 'type': mode,
                'start': s['time'].strftime('%H:%M'), 'end': ts_dt.strftime('%H:%M'),
                'hours': round(duration, 2), 'cost': round(cost, 2),
                'map_start': f"https://www.google.com/maps?q={s['lat']},{s['lon']}" if s['lat'] else "#",
                'map_end': f"https://www.google.com/maps?q={lat},{lon}" if lat else "#"
            })

    conn.close()
    
    return render_template_string("""
    <!DOCTYPE html><html><head><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
    <body class="container py-4 bg-light">
        <h2>Panel Administratora</h2>
        
        <div class="row mb-4 mt-3 text-center">
            <div class="col-md-6">
                <div class="card shadow-sm border-primary">
                    <div class="card-header bg-primary text-white">Koszty razem: <strong>{{ "{:,.2f}".format(summary.total_cost) }} zł</strong></div>
                    <div class="card-body py-2">Koszty dojazdów: {{ "{:,.2f}".format(summary.travel_cost) }} zł | Koszty pracy: {{ "{:,.2f}".format(summary.work_cost) }} zł</div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card shadow-sm border-success">
                    <div class="card-header bg-success text-white">Czas pracy razem: <strong>{{ "{:,.2f}".format(summary.total_time) }} rbh</strong></div>
                    <div class="card-body py-2">Czas dojazdów: {{ "{:,.2f}".format(summary.travel_time) }} rbh | Czas pracy: {{ "{:,.2f}".format(summary.work_time) }} rbh</div>
                </div>
            </div>
        </div>

        <div class="row g-3 mb-4">
            <div class="col-md-4">
                <div class="card card-body shadow-sm">
                    <h5>Filtry</h5>
                    <form method="get">
                        <select name="worker" class="form-select mb-2">
                            <option value="">Wszyscy pracownicy</option>
                            {% for w in workers %}<option value="{{w[2]}}" {% if filter_worker == w[2] %}selected{% endif %}>{{w[1]}}</option>{% endfor %}
                        </select>
                        
                        <div class="input-group mb-2">
                            <select id="f_type" name="filter_type" class="form-select" style="max-width: 120px;" onchange="updateInputType()">
                                <option value="month" {% if filter_type == 'month' %}selected{% endif %}>Miesiąc</option>
                                <option value="week" {% if filter_type == 'week' %}selected{% endif %}>Tydzień</option>
                                <option value="day" {% if filter_type == 'day' %}selected{% endif %}>Dzień</option>
                            </select>
                            <input type="month" id="f_val" name="filter_val" class="form-control" value="{{filter_val}}">
                        </div>

                        <button class="btn btn-dark w-100">Pokaż raport</button>
                    </form>
                </div>
            </div>
            <div class="col-md-8">
                <div class="card card-body shadow-sm">
                    <h5>Dodaj nowego pracownika</h5>
                    <form method="post" class="row g-2">
                        <div class="col-md-6"><input name="new_name" class="form-control" placeholder="Imię Nazwisko" required></div>
                        <div class="col-md-3"><input name="rate_work" class="form-control" placeholder="Budowa zł/h"></div>
                        <div class="col-md-3"><input name="rate_travel" class="form-control" placeholder="Dojazd zł/h"></div>
                        <div class="col-12"><button class="btn btn-primary w-100">Zapisz pracownika</button></div>
                    </form>
                </div>
            </div>
        </div>

        <table class="table table-hover bg-white shadow-sm border align-middle text-center">
            <thead class="table-dark">
                <tr><th>Data</th><th>Pracownik</th><th>Typ</th><th>Czas</th><th>h</th><th>Suma</th><th>GPS</th><th>Akcje</th></tr>
            </thead>
            <tbody>
                {% for r in report %}
                <tr>
                    <td>{{r.date}}</td><td>{{r.worker}}</td><td>{{r.type}}</td>
                    <td>{{r.start}} - {{r.end}}</td><td>{{r.hours}}</td><td>{{r.cost}} zł</td>
                    <td>
                        <a href="{{r.map_start}}" target="_blank" class="text-decoration-none">📍 Start</a><br>
                        <a href="{{r.map_end}}" target="_blank" class="text-decoration-none">📍 Stop</a>
                    </td>
                    <td><button class="btn btn-sm btn-warning" onclick="openLogModal({{r.start_id}}, {{r.stop_id}}, '{{r.raw_date}}', '{{r.start}}', '{{r.end}}')">EDYTUJ</button></td>
                </tr>
                {% endfor %}
                <tr class="table-secondary fw-bold">
                    <td colspan="4" class="text-end">SUMA:</td>
                    <td>{{ "{:,.2f}".format(summary.total_time) }}</td>
                    <td>{{ "{:,.2f}".format(summary.total_cost) }} zł</td>
                    <td colspan="2"></td>
                </tr>
            </tbody>
        </table>

        <div class="mt-5 mb-5">
            <h5>Zarządzanie pracownikami (Linki):</h5>
            <table class="table table-bordered bg-white">
                <tbody>
                    {% for w in workers %}
                    <tr>
                        <td class="align-middle"><strong>{{w[1]}}</strong> (Budowa: {{w[3]}}zł, Dojazd: {{w[4]}}zł)</td>
                        <td class="align-middle"><code>{{ url_for('user_page', token=w[2], _external=True) }}</code></td>
                        <td class="text-end">
                            <button class="btn btn-sm btn-warning" onclick="openWorkerModal({{w[0]}}, '{{w[1]}}', {{w[3]}}, {{w[4]}})">EDYTUJ</button>
                            <form action="/admin/delete_worker/{{w[0]}}" method="post" class="d-inline" onsubmit="return confirm('Czy na pewno chcesz usunąć pracownika {{w[1]}}? Ta akcja jest nieodwracalna!');">
                                <button type="submit" class="btn btn-sm btn-danger">USUŃ</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <div id="logModal" class="modal" style="display:none; position:fixed; top:10%; left:50%; transform:translate(-50%, 0); background:white; padding:20px; border:1px solid #ccc; box-shadow:0 0 15px rgba(0,0,0,0.5); z-index:1000; border-radius:10px; width:400px;">
            <h4>Edycja czasu pracy</h4>
            <form action="/admin/edit_log" method="post">
                <input type="hidden" name="start_id" id="m_start_id">
                <input type="hidden" name="stop_id" id="m_stop_id">
                <label>Data:</label><input type="date" name="date" id="m_date" class="form-control mb-2" required>
                <label>Czas Start:</label><input type="time" name="start" id="m_start" class="form-control mb-2" required>
                <label>Czas Stop:</label><input type="time" name="stop" id="m_stop" class="form-control mb-3" required>
                <button type="submit" class="btn btn-success w-100 mb-2">Zapisz zmiany</button>
                <button type="button" class="btn btn-secondary w-100" onclick="document.getElementById('logModal').style.display='none'">Anuluj</button>
            </form>
        </div>

        <div id="workerModal" class="modal" style="display:none; position:fixed; top:10%; left:50%; transform:translate(-50%, 0); background:white; padding:20px; border:1px solid #ccc; box-shadow:0 0 15px rgba(0,0,0,0.5); z-index:1000; border-radius:10px; width:400px;">
            <h4>Edycja Pracownika</h4>
            <form action="/admin/edit_worker" method="post">
                <input type="hidden" name="worker_id" id="w_id">
                <label>Imię i Nazwisko:</label><input type="text" name="name" id="w_name" class="form-control mb-2" required>
                <label>Stawka Budowa (zł/h):</label><input type="number" step="0.01" name="rate_work" id="w_rate_work" class="form-control mb-2" required>
                <label>Stawka Dojazd (zł/h):</label><input type="number" step="0.01" name="rate_travel" id="w_rate_travel" class="form-control mb-3" required>
                <button type="submit" class="btn btn-success w-100 mb-2">Zapisz</button>
                <button type="button" class="btn btn-secondary w-100" onclick="document.getElementById('workerModal').style.display='none'">Anuluj</button>
            </form>
        </div>

        <script>
            function updateInputType() {
                let typeSel = document.getElementById('f_type').value;
                let inputVal = document.getElementById('f_val');
                if (typeSel === 'month') { inputVal.type = 'month'; }
                else if (typeSel === 'week') { inputVal.type = 'week'; }
                else if (typeSel === 'day') { inputVal.type = 'date'; }
            }
            window.onload = function() { updateInputType(); };

            function openLogModal(startId, stopId, date, start, stop) {
                document.getElementById('m_start_id').value = startId;
                document.getElementById('m_stop_id').value = stopId;
                document.getElementById('m_date').value = date;
                document.getElementById('m_start').value = start;
                document.getElementById('m_stop').value = stop;
                document.getElementById('logModal').style.display = 'block';
            }
            function openWorkerModal(id, name, rw, rt) {
                document.getElementById('w_id').value = id;
                document.getElementById('w_name').value = name;
                document.getElementById('w_rate_work').value = rw;
                document.getElementById('w_rate_travel').value = rt;
                document.getElementById('workerModal').style.display = 'block';
            }
        </script>
    </body></html>
    """, workers=workers, report=report, filter_worker=filter_worker, filter_type=filter_type, filter_val=filter_val, summary=summary)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)