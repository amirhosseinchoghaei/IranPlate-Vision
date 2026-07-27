import sqlite3, os, threading
from datetime import datetime, timedelta

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
            added    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            expire_at TEXT
        );
        CREATE TABLE IF NOT EXISTS access_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            plate       TEXT NOT NULL,
            camera_id   INTEGER,
            camera_name TEXT,
            role        TEXT,
            confidence  REAL,
            crop_b64    TEXT,
            ts          TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
    ''')
    
    # ارتقای ایمن جدول vehicles در صورت نبود ستون expire_at از قبل
    try:
        conn.execute('ALTER TABLE vehicles ADD COLUMN expire_at TEXT')
    except sqlite3.OperationalError:
        pass  # ستون از قبل وجود داشته است
        
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
    veh = _fetchone('SELECT * FROM vehicles WHERE plate=?', (plate,))
    if not veh:
        return None
        
    # چک کردن انقضای اعتبار زمانی
    if veh.get('expire_at'):
        try:
            expire_dt = datetime.strptime(veh['expire_at'], '%Y-%m-%d %H:%M:%S')
            if datetime.now() > expire_dt:
                veh['list'] = 'none'  # بازگشت به حالت عادی پس از اتمام مهلت
        except Exception:
            pass
            
    return veh

def vehicle_upsert(plate, label, list_type, note='', valid_days=None):
    expire_at = None
    if valid_days and str(valid_days).isdigit() and int(valid_days) > 0:
        expire_dt = datetime.now() + timedelta(days=int(valid_days))
        expire_at = expire_dt.strftime('%Y-%m-%d %H:%M:%S')

    existing = _fetchone('SELECT * FROM vehicles WHERE plate=?', (plate,))
    if existing:
        _run('UPDATE vehicles SET label=?,list=?,note=?,expire_at=? WHERE plate=?',
             (label, list_type, note, expire_at, plate))
    else:
        _run('INSERT INTO vehicles(plate,label,list,note,expire_at) VALUES(?,?,?,?,?)',
             (plate, label, list_type, note, expire_at))

def vehicle_delete(plate):
    _run('DELETE FROM vehicles WHERE plate=?', (plate,))

# ── Access log ────────────────────────────────────────────────────────────────
def log_add(plate, camera_id, camera_name, role, confidence, crop_b64=''):
    return _run(
        'INSERT INTO access_log(plate,camera_id,camera_name,role,confidence,crop_b64) VALUES(?,?,?,?,?,?)',
        (plate, camera_id, camera_name, role, confidence, crop_b64)
    )

def log_recent(limit=200):
    logs = _fetchall(
        'SELECT id,plate,camera_name,role,confidence,ts FROM access_log ORDER BY id DESC LIMIT ?',
        (limit,)
    )
    
    # دریافت لیست وضعیت خودروها جهت ست کردن دقیق وضعیت مجاز/غیرمجاز با چک انقضا
    vehicles = {v['plate']: v for v in vehicles_all()}
    
    now = datetime.now()
    for l in logs:
        v = vehicles.get(l['plate'])
        if v:
            is_expired = False
            if v.get('expire_at'):
                try:
                    expire_dt = datetime.strptime(v['expire_at'], '%Y-%m-%d %H:%M:%S')
                    if now > expire_dt:
                        is_expired = True
                except Exception:
                    pass
            
            l['list'] = 'none' if is_expired else v.get('list', 'none')
            l['label'] = v.get('label', '')
        else:
            l['list'] = 'none'
            l['label'] = ''
            
    return logs

def log_clear():
    _run('DELETE FROM access_log')

init_db()
