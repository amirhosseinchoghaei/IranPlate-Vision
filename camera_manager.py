"""
RTSP Camera Manager - Low-Latency RTSP with Original DB Structure Sync
"""
import threading, time, queue, base64, re, logging
import cv2
from PIL import Image
import jdatetime
import db

log = logging.getLogger(__name__)

# ── SSE event bus ─────────────────────────────────────────────────────────────
_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()

def subscribe():
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

# ── Model references (injected from app.py) ─────────────────────────────────
det_model = None
ocr_model = None

FA_TO_EN = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
EN_TO_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')

def _ocr_plate(crop_bgr):
    """Returns plate text string or empty string."""
    if ocr_model is None or crop_bgr is None or crop_bgr.size == 0:
        return ''
    try:
        # تبدیل BGR به RGB و بزرگ‌نمایی ۲ برابری جهت افزایش خوانایی مدل OCR
        rgb_img = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        ch, cw = rgb_img.shape[:2]
        if cw < 200:
            rgb_img = cv2.resize(rgb_img, (cw * 2, ch * 2), interpolation=cv2.INTER_CUBIC)

        pil = Image.fromarray(rgb_img)
        raw = ocr_model.predict(pil)
        if raw and len(raw) > 0:
            if isinstance(raw, list) and isinstance(raw[0], dict):
                return raw[0].get('text', '').strip()
            elif isinstance(raw, dict):
                return raw.get('text', '').strip()
            elif hasattr(raw[0], 'text'):
                return raw[0].text.strip()
    except Exception as e:
        log.debug('OCR error: %s', e)
    return ''

def _detect_plates(frame_bgr):
    """Returns list of (conf, crop_bgr, text)."""
    if det_model is None or frame_bgr is None:
        return []
    
    # حد آستانه YOLO به 0.35 کاهش یافت تا کادر پلاک‌ها را سریع‌تر شکار کند
    try:
        results = det_model.predict(source=frame_bgr, conf=0.35, verbose=False, device='cuda')
    except Exception:
        results = det_model.predict(source=frame_bgr, conf=0.35, verbose=False)

    out = []
    h, w = frame_bgr.shape[:2]
    for box in results[0].boxes:
        conf = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        
        # پدینگ درصدی هوشمند برای جلوگیری از بریدن حواشی پلاک
        pad_w = int((x2 - x1) * 0.08)
        pad_h = int((y2 - y1) * 0.12)

        x1 = max(0, x1 - pad_w); y1 = max(0, y1 - pad_h)
        x2 = min(w, x2 + pad_w); y2 = min(h, y2 + pad_h)
        
        crop = frame_bgr[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        text = _ocr_plate(crop)
        if text:
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
    return text.translate(FA_TO_EN).replace(' ', '').replace('\n', '').replace('\r', '').strip()

def _to_fa_display(norm_text):
    """نمایش پلاک فارسی بدون تغییر در کلید اصلی دیتابیس"""
    m = PLATE_RE.match(norm_text)
    if m:
        p1 = m.group(1).translate(EN_TO_FA)
        letter = m.group(2)
        p2 = m.group(3).translate(EN_TO_FA)
        prov = m.group(4).translate(EN_TO_FA)
        return f"{p1} {letter} {p2} {prov}"
    return norm_text.translate(EN_TO_FA)

# ── Plate state machine ───────────────────────────────────────────────────────
DETECT_INTERVAL = 1.0  # بررسی هر ۱ ثانیه‌یک‌بار جهت واکنش سریع‌تر
ABSENT_FRAMES   = 5
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
                # ۱. خالی کردن بافر FFMPEG شبکه تا همیشه تازه‌ترین فریم پردازش شود
                if not cap.grab():
                    log.warning('Camera %s: lost frame', self.cam['id'])
                    break

                now = time.time()
                if now - last_detect >= DETECT_INTERVAL:
                    # ۲. دکود فریم فقط زمان اجرای تشخیص
                    ret, frame = cap.retrieve()
                    if not ret or frame is None:
                        continue

                    snap = _frame_b64(frame)
                    with self._frame_lock:
                        self._latest_frame = snap

                    last_detect = now
                    self._process_frame(frame)
                else:
                    time.sleep(0.01)
        finally:
            cap.release()
            self._cap = None

    def _process_frame(self, frame):
        detections = _detect_plates(frame)

        seen_now: dict[str, tuple] = {}
        for conf, crop, text in detections:
            if not text:
                continue
            norm = _normalize(text)
            if not PLATE_RE.match(norm):
                continue
            if norm not in seen_now or conf > seen_now[norm][0]:
                seen_now[norm] = (conf, crop)

        for norm, (conf, crop) in seen_now.items():
            if norm not in self._states:
                self._states[norm] = _PlateState(crop, conf)
                self._register(norm, conf, crop)
            else:
                st = self._states[norm]
                st.absent_count = 0
                st.conf = conf
                st.crop = crop
                if st.status == 'gone':
                    st.status = 'present'
                    self._register(norm, conf, crop)

        for norm, st in list(self._states.items()):
            if norm in seen_now:
                continue
            if st.status == 'present':
                st.absent_count += 1
                if st.absent_count >= ABSENT_FRAMES:
                    st.status = 'gone'
                    log.debug('[cam %s] plate %s left scene', self.cam['id'], norm)
            elif st.status == 'gone':
                st.absent_count += 1
                if st.absent_count > ABSENT_FRAMES * 6:
                    del self._states[norm]

    def _register(self, norm, conf, crop):
        """Persist a new detection event in DB and broadcast it."""
        veh = db.vehicle_get(norm)
        vlist = veh['list'] if veh else 'none'
        label = veh['label'] if veh else ''

        display_plate = _to_fa_display(norm)
        now_shamsi = jdatetime.datetime.now().strftime("%H:%M:%S")

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
            'plate': display_plate,
            'raw_plate': norm,
            'label': label,
            'list': vlist,
            'camera_id': self.cam['id'],
            'camera_name': self.cam['name'],
            'role': self.cam['role'],
            'conf': round(conf, 3),
            'ts': now_shamsi,
        })
        log.info('[cam %s] NEW plate=%s conf=%.2f list=%s',
                 self.cam['id'], norm, conf, vlist)


# ── Manager ───────────────────────────────────────────────────────────────────
_workers: dict[int, CameraWorker] = {}
_mgr_lock = threading.Lock()

def start_all():
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
