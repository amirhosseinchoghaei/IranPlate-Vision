"""
RTSP Camera Manager
Each camera runs in a dedicated thread to capture frames, detect plates,
and push events to clients over SSE.
"""
import threading, time, queue, base64, re, logging
import cv2
from PIL import Image
import db

log = logging.getLogger(__name__)

# ── SSE event bus ─────────────────────────────────────────────────────────────
_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()

def subscribe():
    """Returns a queue that receives SSE event dicts."""
    q = queue.Queue(maxsize=64)
    with _sub_lock:
        _subscribers.append(q)
    return q

def unsubscribe(q):
    with _sub_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass

def _broadcast(event: dict):
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)

# ── Model references (injected from app.py after models load) ─────────────────
det_model = None
ocr_model = None

FA_TO_EN = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')

def _ocr_plate(crop_bgr):
    """Returns plate text string or empty string."""
    if ocr_model is None:
        return ''
    try:
        pil = Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))
        raw = ocr_model.predict(pil)
        if raw and raw[0].get('text'):
            return raw[0]['text'].strip()
    except Exception as e:
        log.debug('OCR error: %s', e)
    return ''

def _detect_plates(frame_bgr):
    """Returns list of (conf, crop_bgr, text)."""
    if det_model is None:
        return []
    results = det_model.predict(source=frame_bgr, conf=0.4, verbose=False)
    out = []
    h, w = frame_bgr.shape[:2]
    for box in results[0].boxes:
        conf = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        pad = 10
        x1 = max(0, x1 - pad); y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad); y2 = min(h, y2 + pad)
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        text = _ocr_plate(crop)
        out.append((conf, crop, text))
    return out

def _crop_b64(crop_bgr):
    _, buf = cv2.imencode('.jpg', crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()

def _frame_b64(frame_bgr, max_w=640):
    h, w = frame_bgr.shape[:2]
    if w > max_w:
        frame_bgr = cv2.resize(frame_bgr, (max_w, int(h * max_w / w)))
    _, buf = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return base64.b64encode(buf).decode()

PLATE_RE = re.compile(r'^([0-9۰-۹]{2})([؀-ۿ])([0-9۰-۹]{3})([0-9۰-۹]{2})$')

def _normalize(text):
    return text.translate(FA_TO_EN).replace(' ', '')

# ── Plate state machine ───────────────────────────────────────────────────────
# Each plate can be in one of these states:
#   'present'  — currently visible and already registered
#   'gone'     — missed for N consecutive checks and treated as left
#   (missing)  — not present in state dict yet (new plate)
#
# Registration happens once when a plate appears for the first time
# (or after it has fully left and appears again).
#
# ABSENT_FRAMES defines how many missed checks are needed before a plate
# is marked as gone. With DETECT_INTERVAL=2s and ABSENT_FRAMES=5, it is
# considered gone after about 10 seconds.

DETECT_INTERVAL = 2.0
ABSENT_FRAMES   = 5    # Consecutive missed checks before marking as gone
RECONNECT_WAIT  = 5.0

class _PlateState:
    __slots__ = ('status', 'absent_count', 'crop', 'conf')
    def __init__(self, crop, conf):
        self.status = 'present'
        self.absent_count = 0
        self.crop = crop
        self.conf = conf

class CameraWorker(threading.Thread):
    def __init__(self, cam: dict):
        super().__init__(daemon=True, name=f"cam-{cam['id']}")
        self.cam = cam
        self._stop_evt = threading.Event()
        self._cap = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        # plate_norm → _PlateState
        self._states: dict[str, _PlateState] = {}

    def stop(self):
        self._stop_evt.set()

    def get_snapshot(self):
        with self._frame_lock:
            return self._latest_frame

    def run(self):
        while not self._stop_evt.is_set():
            try:
                self._run_capture()
            except Exception as e:
                log.warning('Camera %s error: %s — reconnecting in %ss',
                            self.cam['id'], e, RECONNECT_WAIT)
            if not self._stop_evt.is_set():
                time.sleep(RECONNECT_WAIT)

    def _run_capture(self):
        url = self.cam['url']
        log.info('Connecting to camera %s: %s', self.cam['id'], url)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            log.warning('Cannot open %s', url)
            return
        self._cap = cap
        last_detect = 0.0
        try:
            while not self._stop_evt.is_set():
                ret, frame = cap.read()
                if not ret:
                    log.warning('Camera %s: lost frame', self.cam['id'])
                    break

                snap = _frame_b64(frame)
                with self._frame_lock:
                    self._latest_frame = snap

                now = time.time()
                if now - last_detect >= DETECT_INTERVAL:
                    last_detect = now
                    self._process_frame(frame)
        finally:
            cap.release()
            self._cap = None

    def _process_frame(self, frame):
        detections = _detect_plates(frame)

        # Valid plates detected in this frame
        seen_now: dict[str, tuple] = {}  # norm → (conf, crop)
        for conf, crop, text in detections:
            if not text:
                continue
            norm = _normalize(text)
            if not PLATE_RE.match(norm):
                continue
            # If the same plate appears multiple times, keep highest confidence
            if norm not in seen_now or conf > seen_now[norm][0]:
                seen_now[norm] = (conf, crop)

        # Plates observed in this frame
        for norm, (conf, crop) in seen_now.items():
            if norm not in self._states:
                # New plate: register once
                self._states[norm] = _PlateState(crop, conf)
                self._register(norm, conf, crop)
            else:
                st = self._states[norm]
                st.absent_count = 0
                st.conf = conf
                st.crop = crop
                if st.status == 'gone':
                    # Plate has re-entered: register again
                    st.status = 'present'
                    self._register(norm, conf, crop)

        # Plates not seen in this frame
        for norm, st in list(self._states.items()):
            if norm in seen_now:
                continue
            if st.status == 'present':
                st.absent_count += 1
                if st.absent_count >= ABSENT_FRAMES:
                    st.status = 'gone'
                    log.debug('[cam %s] plate %s left scene', self.cam['id'], norm)
            elif st.status == 'gone':
                # Memory cleanup after prolonged absence
                st.absent_count += 1
                if st.absent_count > ABSENT_FRAMES * 6:  # ~60 seconds
                    del self._states[norm]

    def _register(self, norm, conf, crop):
        """Persist a new detection event in DB and broadcast it."""
        veh = db.vehicle_get(norm)
        vlist = veh['list'] if veh else 'none'
        label = veh['label'] if veh else ''

        db.log_add(
            plate=norm,
            camera_id=self.cam['id'],
            camera_name=self.cam['name'],
            role=self.cam['role'],
            confidence=conf,
            crop_b64=_crop_b64(crop),
        )
        _broadcast({
            'type': 'detection',
            'plate': norm,
            'label': label,
            'list': vlist,
            'camera_id': self.cam['id'],
            'camera_name': self.cam['name'],
            'role': self.cam['role'],
            'conf': round(conf, 3),
            'ts': time.strftime('%H:%M:%S'),
        })
        log.info('[cam %s] NEW plate=%s conf=%.2f list=%s',
                 self.cam['id'], norm, conf, vlist)


# ── Manager ───────────────────────────────────────────────────────────────────
_workers: dict[int, CameraWorker] = {}
_mgr_lock = threading.Lock()

def start_all():
    """Start workers for all enabled cameras from DB."""
    for cam in db.cameras_all():
        if cam['enabled']:
            _start_worker(cam)

def _start_worker(cam: dict):
    cid = cam['id']
    with _mgr_lock:
        if cid in _workers:
            return
        w = CameraWorker(cam)
        _workers[cid] = w
        w.start()
        log.info('Started worker for camera %s', cid)

def _stop_worker(cid: int):
    with _mgr_lock:
        w = _workers.pop(cid, None)
    if w:
        w.stop()
        log.info('Stopped worker for camera %s', cid)

def add_camera(name, url, role='entry'):
    cid = db.camera_add(name, url, role)
    cam = db.camera_get(cid)
    _start_worker(cam)
    return cid

def remove_camera(cid: int):
    _stop_worker(cid)
    db.camera_delete(cid)

def set_enabled(cid: int, enabled: bool):
    db.camera_set_enabled(cid, enabled)
    cam = db.camera_get(cid)
    if enabled:
        _start_worker(cam)
    else:
        _stop_worker(cid)

def get_snapshot(cid: int):
    with _mgr_lock:
        w = _workers.get(cid)
    return w.get_snapshot() if w else None

def worker_status():
    with _mgr_lock:
        return {cid: w.is_alive() for cid, w in _workers.items()}
