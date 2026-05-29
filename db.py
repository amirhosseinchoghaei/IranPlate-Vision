import sqlite3, os, threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'traffic.db')

_local = threading.local()

def get_conn():
    if not hasattr(_local, 'conn') or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute('PRAGMA journal_mode=WAL')
    return _local.conn

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS cameras (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL,
            url      TEXT NOT NULL,
            role     TEXT NOT NULL DEFAULT 'entry',  -- entry | exit | monitor
            enabled  INTEGER NOT NULL DEFAULT 1,
            created  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            plate    TEXT NOT NULL UNIQUE,
            label    TEXT,
            list     TEXT NOT NULL DEFAULT 'none',   -- none | white | black
            note     TEXT,
            added    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS access_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            plate      TEXT NOT NULL,
            camera_id  INTEGER,
            camera_name TEXT,
            role       TEXT,
            confidence REAL,
            crop_b64   TEXT,
            ts         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
    ''')
    conn.commit()
    conn.close()

def _fetchall(sql, params=()):
    c = get_conn()
    return [dict(r) for r in c.execute(sql, params).fetchall()]

def _fetchone(sql, params=()):
    c = get_conn()
    r = c.execute(sql, params).fetchone()
    return dict(r) if r else None

def _run(sql, params=()):
    c = get_conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur.lastrowid

# ── Cameras ──────────────────────────────────────────────────────────────────
def cameras_all():
    return _fetchall('SELECT * FROM cameras ORDER BY id')

def camera_get(cid):
    return _fetchone('SELECT * FROM cameras WHERE id=?', (cid,))

def camera_add(name, url, role='entry'):
    return _run('INSERT INTO cameras(name,url,role) VALUES(?,?,?)', (name, url, role))

def camera_update(cid, name, url, role, enabled):
    _run('UPDATE cameras SET name=?,url=?,role=?,enabled=? WHERE id=?',
         (name, url, role, int(enabled), cid))

def camera_delete(cid):
    _run('DELETE FROM cameras WHERE id=?', (cid,))

def camera_set_enabled(cid, enabled):
    _run('UPDATE cameras SET enabled=? WHERE id=?', (int(enabled), cid))

# ── Vehicles (whitelist / blacklist) ─────────────────────────────────────────
def vehicles_all():
    return _fetchall('SELECT * FROM vehicles ORDER BY added DESC')

def vehicle_get(plate):
    return _fetchone('SELECT * FROM vehicles WHERE plate=?', (plate,))

def vehicle_upsert(plate, label, list_type, note=''):
    existing = vehicle_get(plate)
    if existing:
        _run('UPDATE vehicles SET label=?,list=?,note=? WHERE plate=?',
             (label, list_type, note, plate))
    else:
        _run('INSERT INTO vehicles(plate,label,list,note) VALUES(?,?,?,?)',
             (plate, label, list_type, note))

def vehicle_delete(plate):
    _run('DELETE FROM vehicles WHERE plate=?', (plate,))

# ── Access log ────────────────────────────────────────────────────────────────
def log_add(plate, camera_id, camera_name, role, confidence, crop_b64=''):
    return _run(
        'INSERT INTO access_log(plate,camera_id,camera_name,role,confidence,crop_b64) VALUES(?,?,?,?,?,?)',
        (plate, camera_id, camera_name, role, confidence, crop_b64)
    )

def log_recent(limit=200):
    return _fetchall(
        'SELECT id,plate,camera_name,role,confidence,ts FROM access_log ORDER BY id DESC LIMIT ?',
        (limit,)
    )

def log_clear():
    _run('DELETE FROM access_log')

init_db()
