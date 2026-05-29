import os
import re
import json
import base64
import threading
import queue
import time
import cv2
import numpy as np
from flask import Flask, request, jsonify, render_template, send_from_directory, make_response, Response, stream_with_context
from PIL import Image
import db
import camera_manager as cm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))

with open(os.path.join(BASE_DIR, 'plate_data.json'), encoding='utf-8') as f:
    PLATE_DATA = json.load(f)

FA_TO_EN = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')

# ── Models: lazy-loaded in background threads ──
det_model = None
ocr_model = None
models_ready = False
models_error = None
_load_lock = threading.Lock()

def _load_models():
    global det_model, ocr_model, models_ready, models_error
    try:
        from ultralytics import YOLO
        from hezar.models import Model
        print("Loading models | در حال بارگذاری مدل‌ها...")
        det_model = YOLO(os.path.join(BASE_DIR, 'best.pt'))
        ocr_model = Model.load('hezarai/crnn-fa-license-plate-recognition-v2')
        models_ready = True
        print("Models are ready | مدل‌ها آماده‌اند.")
    except Exception as e:
        models_error = str(e)
        print(f"Model load error | خطا در بارگذاری مدل‌ها: {e}")

def _after_models_loaded():
    """Inject models into camera_manager and start RTSP workers."""
    while not models_ready and not models_error:
        time.sleep(0.5)
    if models_ready:
        cm.det_model = det_model
        cm.ocr_model = ocr_model
        cm.start_all()

threading.Thread(target=_load_models, daemon=True).start()
threading.Thread(target=_after_models_loaded, daemon=True).start()


def lookup_plate(letter, suffix_fa):
    suffix = suffix_fa.translate(FA_TO_EN)
    vehicle_type = None
    for t in PLATE_DATA['carplate_types']:
        if letter in t['letters']:
            vehicle_type = t
            break
    matches = []
    for province, cities in PLATE_DATA['carplates'].items():
        for city, codes in cities.items():
            for code, letters in codes.items():
                if code == suffix and ((not letters) or (letter in letters)):
                    matches.append({'province': province, 'city': city})
    if not matches:
        for province, cities in PLATE_DATA['carplates'].items():
            for city, codes in cities.items():
                for code, letters in codes.items():
                    if code == suffix:
                        matches.append({'province': province, 'city': city})
    return {'vehicle_type': vehicle_type, 'locations': matches[:3]}


@app.route('/fonts/<path:filename>')
def serve_font(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'fonts'), filename)

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

@app.route('/')
def menu():
    resp = make_response(render_template('menu.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/scan')
def index():
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/status')
def status():
    return jsonify({'ready': models_ready, 'error': models_error})

@app.route('/detect', methods=['POST'])
def detect():
    if not models_ready:
        msg = models_error or 'Models are still loading | مدل‌ها هنوز در حال بارگذاری هستند'
        return jsonify({'error': msg, 'loading': not bool(models_error)}), 503

    if 'image' not in request.files:
        return jsonify({'error': 'No image uploaded | تصویری ارسال نشده'}), 400

    img_bytes = request.files['image'].read()
    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        return jsonify({'error': 'Unsupported image format | فرمت تصویر پشتیبانی نمی‌شود'}), 400

    results = det_model.predict(source=img, conf=0.4, verbose=False)
    annotated = results[0].plot()

    plates_found = len(results[0].boxes)
    best_conf = 0.0
    ocr_text = ''
    best_crop = None

    for box in results[0].boxes:
        conf = float(box.conf[0])
        if conf > best_conf:
            best_conf = conf

        x1, y1, x2, y2 = map(int, box.xyxy[0])
        h, w = img.shape[:2]
        pad = 10
        x1 = max(0, x1 - pad); y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad); y2 = min(h, y2 + pad)

        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue

        pil_img = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        raw = ocr_model.predict(pil_img)

        if raw and len(raw) > 0:
            text = raw[0]['text']
            if text:
                ocr_text = text.strip()
                best_crop = crop

    plate_info = None
    if ocr_text:
        m = re.match(r'^([0-9۰-۹]{2})([؀-ۿ])([0-9۰-۹]{3})([0-9۰-۹]{2})$', ocr_text)
        if m:
            plate_info = lookup_plate(m.group(2), m.group(4))
            plate_info['prefix'] = m.group(1)
            plate_info['letter'] = m.group(2)
            plate_info['middle'] = m.group(3)
            plate_info['suffix'] = m.group(4)
            if plate_info['vehicle_type']:
                vt = plate_info['vehicle_type']
                plate_info['vehicle_type'] = {'type': vt['type'], 'id': vt['id'], 'bg': vt['bg'], 'color': vt['color']}

    _, buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 92])
    img_b64 = base64.b64encode(buf).decode('utf-8')

    crop_b64 = ''
    if best_crop is not None:
        _, cbuf = cv2.imencode('.jpg', best_crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        crop_b64 = base64.b64encode(cbuf).decode('utf-8')

    return jsonify({
        'image':        img_b64,
        'crop':         crop_b64,
        'plates_found': plates_found,
        'best_conf':    best_conf,
        'ocr_text':     ocr_text,
        'plate_info':   plate_info,
    })


@app.route('/cameras')
def cameras_page():
    resp = make_response(render_template('cameras.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

# ── Camera CRUD ───────────────────────────────────────────────────────────────
@app.route('/api/cameras', methods=['GET'])
def api_cameras_list():
    cams = db.cameras_all()
    status = cm.worker_status()
    for c in cams:
        c['running'] = status.get(c['id'], False)
    return jsonify(cams)

@app.route('/api/cameras', methods=['POST'])
def api_camera_add():
    d = request.get_json(force=True)
    name = d.get('name', '').strip()
    url  = d.get('url', '').strip()
    role = d.get('role', 'entry')
    if not name or not url:
        return jsonify({'error': 'name and url are required | name و url الزامی هستند'}), 400
    cid = cm.add_camera(name, url, role)
    return jsonify({'id': cid})

@app.route('/api/cameras/<int:cid>', methods=['PUT'])
def api_camera_update(cid):
    d = request.get_json(force=True)
    db.camera_update(cid, d['name'], d['url'], d['role'], d.get('enabled', 1))
    # Restart worker with updated settings
    cm._stop_worker(cid)
    cam = db.camera_get(cid)
    if cam['enabled']:
        cm._start_worker(cam)
    return jsonify({'ok': True})

@app.route('/api/cameras/<int:cid>', methods=['DELETE'])
def api_camera_delete(cid):
    cm.remove_camera(cid)
    return jsonify({'ok': True})

@app.route('/api/cameras/<int:cid>/toggle', methods=['POST'])
def api_camera_toggle(cid):
    d = request.get_json(force=True)
    cm.set_enabled(cid, bool(d.get('enabled', True)))
    return jsonify({'ok': True})

@app.route('/api/cameras/<int:cid>/snapshot')
def api_camera_snapshot(cid):
    snap = cm.get_snapshot(cid)
    if snap is None:
        return jsonify({'error': 'No snapshot available | تصویری موجود نیست'}), 404
    return jsonify({'image': snap})

# ── SSE event stream ──────────────────────────────────────────────────────────
@app.route('/api/events')
def api_events():
    q = cm.subscribe()
    def generate():
        try:
            # Send a heartbeat every 20s to keep connection alive
            while True:
                try:
                    evt = q.get(timeout=20)
                    yield f'data: {json.dumps(evt, ensure_ascii=False)}\n\n'
                except queue.Empty:
                    yield ': heartbeat\n\n'
        finally:
            cm.unsubscribe(q)
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

# ── Access Log ────────────────────────────────────────────────────────────────
@app.route('/api/log')
def api_log():
    limit = int(request.args.get('limit', 200))
    return jsonify(db.log_recent(limit))

@app.route('/api/log', methods=['DELETE'])
def api_log_clear():
    db.log_clear()
    return jsonify({'ok': True})

# ── Vehicles (Whitelist / Blacklist) ─────────────────────────────────────────
@app.route('/api/vehicles', methods=['GET'])
def api_vehicles_list():
    return jsonify(db.vehicles_all())

@app.route('/api/vehicles', methods=['POST'])
def api_vehicle_add():
    d = request.get_json(force=True)
    plate = d.get('plate', '').strip()
    if not plate:
        return jsonify({'error': 'plate is required | plate الزامی است'}), 400
    db.vehicle_upsert(plate, d.get('label', ''), d.get('list', 'white'), d.get('note', ''))
    return jsonify({'ok': True})

@app.route('/api/vehicles/<plate>', methods=['DELETE'])
def api_vehicle_delete(plate):
    db.vehicle_delete(plate)
    return jsonify({'ok': True})


if __name__ == '__main__':
    print("Server is ready: http://localhost:5000 | سرور آماده است")
    app.run(debug=False, host='0.0.0.0', port=5000)
