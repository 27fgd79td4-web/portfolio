#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Virtual Shadow Projection — virtual soya proyeksiyasi
=====================================================

Noutbuk veb-kamerasi orqali foydalanuvchi siluetini real vaqtda ajratib olib,
virtual 3D xona devorlari, poli va shiftiga xuddi foydalanuvchi o'sha xonada
turgandek realistik yumshoq soya tushiradi.

Asosiy g'oya (fizika):
    Nuqtaviy chiroq tushirgan soya aynan "chiroq nuqtai nazaridan ko'rinadigan
    siluet" bilan bir xil. Shuning uchun veb-kamerani virtual chiroq deb
    hisoblaymiz: kamera ko'rgan siluet maskasini proyektor (diaproyektor)
    kabi 3D xonaning har bir yuzasiga (orqa devor, pol, shift, chap va o'ng
    devor) markaziy proyeksiya qilamiz. Har bir yuza uchun bu akslantirish
    aniq bitta 3x3 gomografiya (homography) bo'ladi, shuning uchun hisob juda
    arzon: kadrga atigi 5 ta cv2.warpPerspective.

Quvur (pipeline):
    1. Kamera kadri  -> MediaPipe Selfie Segmentation -> siluet maskasi (0..1)
       (alohida oqimda - asosiy renderni bloklamaydi)
    2. Moslashuvchan EMA filtr -> kadrlar orasida silliq interpolyatsiya
    3. Maska + "oyoq" davomi -> burchakli Gaussian blur (yumshoq penumbra)
    4. Proyektor: spot-konus * (1 - soya) -> har bir yuzaga gomografiya
    5. Lambert (cos) + masofa bo'yicha so'nish -> yorug'lik xaritasi
    6. Fon * (ambient + chiroq * yorug'lik) -> ekranga

Ishga tushirish:
    python virtual_shadow.py                  # protsedural 3D xona, to'liq ekran
    python virtual_shadow.py --room garaj.jpg # o'z xona/garaj rasmingiz
    python virtual_shadow.py --demo           # kamerasiz sinov rejimi

Boshqaruv tugmalari ekranda (H tugmasi) va README.md da.
"""

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

try:
    import mediapipe as mp
except Exception:  # mediapipe o'rnatilmagan bo'lsa ham skript ishlaydi (MOG2/demo)
    mp = None


WINDOW_NAME = "Virtual Shadow Projection"
TASKS_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
                   "selfie_segmenter_landscape/float16/latest/selfie_segmenter_landscape.tflite")

FACE_BACK, FACE_FLOOR, FACE_CEIL, FACE_LEFT, FACE_RIGHT = range(5)


# ---------------------------------------------------------------------------
# Yordamchi funksiyalar
# ---------------------------------------------------------------------------

def smoothstep(e0, e1, x):
    """0..1 oralig'ida silliq S-egri chiziq (qirralarni yumshoq kesish uchun)."""
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def normalize(v):
    return v / (np.linalg.norm(v) + 1e-9)


def cover_resize(img, w, h):
    """Rasmni proporsiyasini buzmasdan (w, h) ni to'liq qoplaydigan qilib kesadi."""
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    nw, nh = int(round(iw * s)), int(round(ih * s))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return img[y0:y0 + h, x0:x0 + w].copy()


def draw_text(img, text, org, scale=0.55, color=(235, 235, 235), thick=1):
    """Soyali (o'qilishi oson) matn."""
    x, y = org
    for dx, dy in ((1, 1), (2, 2)):
        cv2.putText(img, text, (x + dx, y + dy), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


# ---------------------------------------------------------------------------
# 1-bosqich: Segmentatsiya (odam siluetini fondan ajratish)
# ---------------------------------------------------------------------------

class TasksSegmenter:
    """MediaPipe Tasks API (mediapipe >= 0.10). Model avtomatik yuklab olinadi (~250 KB)."""
    name = "MediaPipe Selfie Segmenter"

    def __init__(self):
        path = self._ensure_model()
        opts = mp.tasks.vision.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=path),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            output_confidence_masks=True,
            output_category_mask=False,
        )
        self.seg = mp.tasks.vision.ImageSegmenter.create_from_options(opts)
        self.last_ts = 0

    @staticmethod
    def _ensure_model():
        cache = os.path.join(os.path.expanduser("~"), ".cache", "virtual_shadow")
        os.makedirs(cache, exist_ok=True)
        path = os.path.join(cache, "selfie_segmenter_landscape.tflite")
        if not os.path.exists(path) or os.path.getsize(path) < 10000:
            print("[i] Segmentatsiya modeli yuklab olinmoqda...")
            tmp = path + ".part"
            with urllib.request.urlopen(TASKS_MODEL_URL, timeout=30) as r, open(tmp, "wb") as f:
                f.write(r.read())
            os.replace(tmp, path)
        return path

    def __call__(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        ts = max(int(time.monotonic() * 1000), self.last_ts + 1)  # qat'iy o'suvchi vaqt belgisi
        self.last_ts = ts
        res = self.seg.segment_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        # Selfie modelida oxirgi maska = "odam" ehtimolligi
        return np.asarray(res.confidence_masks[-1].numpy_view(), dtype=np.float32).squeeze()

    def close(self):
        self.seg.close()


class LegacySegmenter:
    """Eski mediapipe.solutions API (mediapipe <= 0.10.14 versiyalarida mavjud)."""
    name = "MediaPipe SelfieSegmentation (legacy)"

    def __init__(self):
        self.seg = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)

    def __call__(self, frame_bgr):
        res = self.seg.process(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        return res.segmentation_mask.astype(np.float32)


class Mog2Segmenter:
    """Zaxira usul: fon ayirish (MediaPipe bo'lmasa). Sifati pastroq - harakat talab qiladi."""
    name = "OpenCV MOG2 (zaxira)"

    def __init__(self):
        self.sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=32, detectShadows=False)

    def __call__(self, frame_bgr):
        small = cv2.resize(frame_bgr, (320, 240))
        fg = self.sub.apply(small, learningRate=0.002)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        return fg.astype(np.float32) / 255.0


def create_segmenter(backend):
    order = {"auto": ["tasks", "legacy", "mog2"], "mediapipe": ["tasks", "legacy"],
             "tasks": ["tasks"], "legacy": ["legacy"], "mog2": ["mog2"]}[backend]
    for b in order:
        try:
            if b == "tasks" and mp is not None and hasattr(mp, "tasks"):
                return TasksSegmenter()
            if b == "legacy" and mp is not None and hasattr(mp, "solutions"):
                return LegacySegmenter()
            if b == "mog2":
                print("[!] MediaPipe ishlamadi - MOG2 zaxira segmentatsiyasi ishlatiladi.")
                return Mog2Segmenter()
        except Exception as e:  # tarmoq yo'q, model yuklanmadi va h.k.
            print(f"[!] '{b}' segmentatori ishga tushmadi: {e}")
    raise RuntimeError("Hech bir segmentatsiya usuli ishga tushmadi. `pip install mediapipe` ni tekshiring.")


# ---------------------------------------------------------------------------
# Kadr manbalari (veb-kamera yoki demo) - alohida oqimda ishlaydi
# ---------------------------------------------------------------------------

class WebcamSource:
    def __init__(self, index, backend, mask_w):
        api = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(index, api)
        if not self.cap.isOpened():
            raise RuntimeError(f"Kamera #{index} ochilmadi")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("Kameradan kadr o'qib bo'lmadi")
        h, w = frame.shape[:2]
        self.mask_size = (mask_w, int(round(mask_w * h / w)))
        self.segment = create_segmenter(backend)
        self.name = self.segment.name

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            return None, None
        frame = cv2.flip(frame, 1)  # oyna effekti: o'ng qo'l -> soyaning o'ng tomoni
        prob = self.segment(frame)
        prob = cv2.resize(prob, self.mask_size, interpolation=cv2.INTER_AREA)
        # Yumshoq chegara: shovqinni kesadi, lekin qirrani anti-alias holda qoldiradi
        mask = smoothstep(0.3, 0.7, prob).astype(np.float32)
        return frame, mask

    def release(self):
        self.cap.release()
        if hasattr(self.segment, "close"):
            self.segment.close()


class DemoSource:
    """Kamerasiz sinov: qo'l silkitib yuradigan sintetik siluet."""
    name = "DEMO (sintetik siluet)"

    def __init__(self, mask_w):
        self.mask_size = (mask_w, int(round(mask_w * 3 / 4)))
        self.t0 = time.monotonic()

    def read(self):
        time.sleep(1 / 30)  # ~30 FPS kamera kabi
        t = time.monotonic() - self.t0
        w, h = self.mask_size
        S = 4  # 4x katta chizib kichraytiramiz -> silliq (anti-alias) qirralar
        W, H = w * S, h * S
        c = np.zeros((H, W), np.uint8)
        cx = int(W * (0.5 + 0.2 * math.sin(t * 0.6)))
        head_y = int(H * (0.30 + 0.02 * math.sin(t * 2.0)))
        r = int(H * 0.11)
        cv2.circle(c, (cx, head_y), r, 255, -1, cv2.LINE_AA)
        cv2.rectangle(c, (cx - r // 2, head_y), (cx + r // 2, head_y + int(r * 1.6)), 255, -1)
        sh_y = head_y + int(r * 1.5)
        cv2.ellipse(c, (cx, H), (int(W * 0.17), H - sh_y), 0, 180, 360, 255, -1, cv2.LINE_AA)
        cv2.rectangle(c, (cx - int(W * 0.17), (sh_y + H) // 2), (cx + int(W * 0.17), H), 255, -1)
        th = int(W * 0.045)
        # chap qo'l - silkitish, o'ng qo'l - yon tomonda
        ang = math.radians(-60 + 45 * math.sin(t * 4.0))
        el = (cx - int(W * 0.16), sh_y + int(r * 0.4))
        hand = (el[0] - int(r * 1.3), el[1] - int(r * 1.8 * math.cos(ang)))
        cv2.line(c, (cx - int(W * 0.12), sh_y + r // 3), el, 255, th, cv2.LINE_AA)
        cv2.line(c, el, hand, 255, th, cv2.LINE_AA)
        cv2.circle(c, hand, int(th * 0.8), 255, -1, cv2.LINE_AA)
        cv2.line(c, (cx + int(W * 0.13), sh_y + r // 3), (cx + int(W * 0.22), H), 255, th, cv2.LINE_AA)
        mask = cv2.resize(c, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        frame = cv2.cvtColor((mask * 200 + 30).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        return frame, mask

    def release(self):
        pass


class CaptureWorker(threading.Thread):
    """Kamera + segmentatsiya fon oqimida: render sekin kameraga bog'lanib qolmaydi."""

    def __init__(self, source):
        super().__init__(daemon=True)
        self.source = source
        self.lock = threading.Lock()
        self.mask = None
        self.preview = None
        self.seq = 0
        self.fps = 0.0
        self.running = True

    def run(self):
        last = time.monotonic()
        while self.running:
            frame, mask = self.source.read()
            if mask is None:
                time.sleep(0.01)
                continue
            now = time.monotonic()
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / max(now - last, 1e-3))
            last = now
            with self.lock:
                self.mask, self.preview = mask, frame
                self.seq += 1

    def latest(self):
        with self.lock:
            return self.seq, self.mask, self.preview

    def stop(self):
        self.running = False
        self.join(timeout=1.0)
        self.source.release()


# ---------------------------------------------------------------------------
# 2-bosqich: Silliqlash (moslashuvchan EMA / "One-Euro" uslubidagi filtr)
# ---------------------------------------------------------------------------

class AdaptiveMaskFilter:
    """
    Har render kadrida smoothed maskani so'nggi kamera maskasiga yaqinlashtiradi.
      - tinch turganda: kichik alpha -> titroq (flicker) yo'qoladi
      - tez harakatda:  katta alpha  -> kechikish (lag) sezilmaydi
    Render 60 FPS, kamera ~30 FPS bo'lsa ham, oraliq kadrlar silliq interpolyatsiya bo'ladi.
    """

    def __init__(self, base=0.22, gain=8.0):
        self.base, self.gain = base, gain
        self.state = None

    def update(self, target):
        if target is None:
            return self.state
        if self.state is None or self.state.shape != target.shape:
            self.state = target.copy()
            return self.state
        motion = float(cv2.mean(cv2.absdiff(target, self.state))[0])
        alpha = min(1.0, self.base + self.gain * motion)
        cv2.addWeighted(target, alpha, self.state, 1.0 - alpha, 0.0, dst=self.state)
        return self.state


# ---------------------------------------------------------------------------
# 3D xona geometriyasi
# ---------------------------------------------------------------------------

class Room:
    """
    Kamera (tomoshabin ko'zi) koordinata boshida, +Z ichkariga, +Y yuqoriga.
    Xona ekran parametrlaridan tiklanadi (bitta nuqtali perspektiva):
        vp    - yo'qolish nuqtasi (normallangan 0..1) = kameraning bosh nuqtasi
        wall  - orqa devor to'rtburchagi (xl, yt, xr, yb), normallangan
        focal - fokus masofasi / ekran kengligi
        room_h- xona balandligi (metr) -> masshtab
    """

    def __init__(self, vp, wall, focal, aspect, room_h=3.0, z_front=0.3):
        self.vp, self.wall, self.focal, self.aspect = vp, wall, focal, aspect
        self.z_front = z_front
        cx, cy = vp[0], vp[1] / aspect           # ekran kengligi = 1 birlik
        xl, yt, xr, yb = wall[0], wall[1] / aspect, wall[2], wall[3] / aspect
        f = focal
        self.D = f * room_h / max(yb - yt, 1e-3)  # orqa devorgacha masofa
        s = self.D / f
        self.xl, self.xr = (xl - cx) * s, (xr - cx) * s
        self.yt, self.yb = -(yt - cy) * s, -(yb - cy) * s
        # Tekisliklar: n . X = c, n xona ichiga qaragan
        self.planes = [
            (np.array([0.0, 0.0, -1.0]), -self.D),   # orqa devor
            (np.array([0.0, 1.0, 0.0]), self.yb),    # pol
            (np.array([0.0, -1.0, 0.0]), -self.yt),  # shift
            (np.array([1.0, 0.0, 0.0]), self.xl),    # chap devor
            (np.array([-1.0, 0.0, 0.0]), -self.xr),  # o'ng devor
        ]

    def K(self, w, h):
        """Ekran kamerasi matritsasi (y pastga qaragan piksel koordinatalari uchun)."""
        f = self.focal * w
        return np.array([[f, 0, self.vp[0] * w], [0, -f, self.vp[1] * h], [0, 0, 1]], np.float64)

    def project(self, X, w, h):
        p = self.K(w, h) @ np.asarray(X, np.float64)
        return p[:2] / p[2]

    def face_corners(self):
        """Har bir yuzaning 3D burchaklari (tekstura (0,0),(1,0),(1,1),(0,1) tartibida)."""
        xl, xr, yt, yb, D, zf = self.xl, self.xr, self.yt, self.yb, self.D, self.z_front
        return {
            FACE_BACK: [(xl, yt, D), (xr, yt, D), (xr, yb, D), (xl, yb, D)],
            FACE_FLOOR: [(xl, yb, D), (xr, yb, D), (xr, yb, zf), (xl, yb, zf)],
            FACE_CEIL: [(xl, yt, zf), (xr, yt, zf), (xr, yt, D), (xl, yt, D)],
            FACE_LEFT: [(xl, yt, zf), (xl, yt, D), (xl, yb, D), (xl, yb, zf)],
            FACE_RIGHT: [(xr, yt, D), (xr, yt, zf), (xr, yb, zf), (xr, yb, D)],
        }

    def raycast(self, w, h):
        """Har bir piksel uchun: qaysi yuzaga tushadi va 3D nuqtasi (vektorlashtirilgan)."""
        K = self.K(w, h)
        xs = (np.arange(w, dtype=np.float32) + 0.5 - K[0, 2]) / K[0, 0]
        ys = (np.arange(h, dtype=np.float32) + 0.5 - K[1, 2]) / K[1, 1]
        dx, dy = np.meshgrid(xs, ys)
        d = np.stack([dx, dy, np.ones_like(dx)], axis=-1)
        best_t = np.full((h, w), np.inf, np.float32)
        face = np.zeros((h, w), np.uint8)
        for k, (n, c) in enumerate(self.planes):
            nd = d @ n.astype(np.float32)
            with np.errstate(divide="ignore", invalid="ignore"):
                t = np.where(nd < -1e-6, c / nd, np.inf).astype(np.float32)
            better = t < best_t
            best_t[better] = t[better]
            face[better] = k
        best_t[~np.isfinite(best_t)] = self.D
        return face, d * best_t[..., None]


# ---------------------------------------------------------------------------
# Protsedural xona teksturalari (rasm berilmaganda)
# ---------------------------------------------------------------------------

def _noise(rng, h, w, cell_h, cell_w, amp):
    n = rng.normal(0, 1, (max(2, h // cell_h), max(2, w // cell_w))).astype(np.float32)
    return cv2.GaussianBlur(cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 2) * amp


def make_wall_texture(rng, w, h):
    base = np.array([168, 186, 204], np.float32)  # BGR: iliq bej/qaymoqrang
    tex = base + _noise(rng, h, w, 24, 24, 4.0)[..., None] + _noise(rng, h, w, 2, 2, 1.5)[..., None]
    bb = int(h * 0.955)                            # plintus (baseboard)
    tex[bb:] = (222, 226, 230)
    tex[bb:bb + 2] = (140, 150, 160)
    return np.clip(tex, 0, 255).astype(np.uint8)


def make_floor_texture(rng, w, h, planks=12):
    tex = np.zeros((h, w, 3), np.float32)
    pw = w / planks
    grain = _noise(rng, h, w, 48, 1, 9.0)          # uzunasiga cho'zilgan yog'och tolasi
    for i in range(planks):
        x0, x1 = int(i * pw), int((i + 1) * pw)
        y = -rng.uniform(0, h * 0.4)
        while y < h:
            seg = rng.uniform(h * 0.25, h * 0.55)
            y0, y1 = int(max(y, 0)), int(min(y + seg, h))
            tone = rng.uniform(0.8, 1.15)
            tex[y0:y1, x0:x1] = np.array([62, 98, 142], np.float32) * tone
            tex[y0:min(y0 + 2, h), x0:x1] *= 0.55    # taxta ulanish chizig'i
            y += seg
        tex[:, x0:min(x0 + 2, w)] *= 0.45           # taxtalar orasidagi tirqish
    tex += grain[..., None]
    return np.clip(tex, 0, 255).astype(np.uint8)


def make_ceiling_texture(rng, w, h):
    tex = np.array([226, 229, 232], np.float32) + _noise(rng, h, w, 32, 32, 2.5)[..., None]
    return np.clip(tex, 0, 255).astype(np.uint8)


def render_procedural_room(room, w, h, face, pts):
    """Teksturalarni har bir yuzaga perspektiv bilan joylashtiradi + burchak soyasi (AO)."""
    rng = np.random.default_rng(7)
    ppm = 140  # tekstura: piksel / metr
    W, H, L = room.xr - room.xl, room.yt - room.yb, room.D - room.z_front

    def sz(a, b):
        return min(2048, max(64, int(a * ppm))), min(2048, max(64, int(b * ppm)))

    texs = {
        FACE_BACK: make_wall_texture(rng, *sz(W, H)),
        FACE_FLOOR: make_floor_texture(rng, *sz(W, L)),
        FACE_CEIL: make_ceiling_texture(rng, *sz(W, L)),
        FACE_LEFT: make_wall_texture(rng, *sz(L, H)),
        FACE_RIGHT: make_wall_texture(rng, *sz(L, H)),
    }
    out = np.zeros((h, w, 3), np.uint8)
    for k, corners in room.face_corners().items():
        tex = texs[k]
        th, tw = tex.shape[:2]
        src = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]])
        dst = np.float32([room.project(c, w, h) for c in corners])
        Hm = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(tex, Hm, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        out[face == k] = warped[face == k]
    # Ambient occlusion: yuzalar tutashgan burchaklar biroz qorong'iroq
    ao = np.ones((h, w), np.float32)
    for k, (n, c) in enumerate(room.planes):
        dist = pts @ n.astype(np.float32) - c
        f = 1.0 - 0.38 * np.exp(-np.maximum(dist, 0) / 0.45)
        ao *= np.where(face == k, 1.0, f)
    return (out.astype(np.float32) * ao[..., None]).clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 3-4 bosqich: Soya proyektori (siluet -> yumshoq soya -> 3D yuzalar)
# ---------------------------------------------------------------------------

class ShadowProjector:
    """
    Virtual chiroq = veb-kamera. Maskani chiroqdan chiqqan nurlar bo'ylab
    xonaning har bir tekisligiga markaziy proyeksiya qiladi.
    Tekislik (n, c) uchun: X = L + (c - n.L)/(n.d) * d  ->  gomogen ko'rinishda
        X_h = A d,  A = [[L n^T + (c - n.L) I], [n^T]]   (4x3)
    va ekran gomografiyasi:  H = P_screen @ A @ R_light @ K_light^-1
    """

    def __init__(self, mask_size, hfov_deg=55.0, cone_deg=62.0):
        self.mask_w, self.mask_h = mask_size
        self.cone = math.radians(cone_deg)
        self.set_fov(hfov_deg)

    def set_fov(self, hfov_deg):
        self.hfov = float(np.clip(hfov_deg, 20.0, 110.0))
        self.fl = (self.mask_w / 2) / math.tan(math.radians(self.hfov) / 2)
        # Kengaytirilgan proyektor sohasi: butun yorug'lik konusini qamrab oladi
        half = int(math.ceil(math.tan(self.cone) * self.fl)) + 2
        half = max(half, self.mask_w // 2 + 4, self.mask_h // 2 + 4)
        self.half = half
        self.size = 2 * half
        self.pad_l = half - self.mask_w // 2
        self.pad_t = half - self.mask_h // 2
        u = np.arange(self.size, dtype=np.float32) + 0.5 - half
        uu, vv = np.meshgrid(u, u)
        ang = np.arctan(np.sqrt(uu ** 2 + vv ** 2) / self.fl)
        # Spot-chiroq: yumshoq qirrali konus + markazda biroz yorqinroq "hotspot"
        spot = 1.0 - smoothstep(0.55 * self.cone, self.cone, ang)
        spot *= 0.82 + 0.18 * (1.0 - smoothstep(0.0, 0.6 * self.cone, ang))
        self.spot = spot.astype(np.float32)
        self.K_inv = np.linalg.inv(np.array([[self.fl, 0, half], [0, self.fl, half], [0, 0, 1]]))

    def build_occluder(self, mask, legs=True, taper=0.4):
        """Maskani proyektor sohasiga joylaydi va kadr pastidan "oyoq" davomini qo'shadi."""
        occ = np.zeros((self.size, self.size), np.float32)
        mh, mw = mask.shape
        y0, x0 = self.pad_t, self.pad_l
        occ[y0:y0 + mh, x0:x0 + mw] = mask
        n = self.size - (y0 + mh)
        if legs and n > 0:
            # Kamera odatda faqat beldan yuqorini ko'radi. Soya "havoda osilib"
            # qolmasligi uchun pastki qatorni polgacha, asta toraytirib cho'zamiz.
            bottom = mask[-3:].mean(axis=0)
            wsum = float(bottom.sum())
            if wsum > 1.0:
                cxb = float((bottom * np.arange(mw)).sum() / wsum)
                s = 1.0 - taper * np.linspace(0.0, 1.0, n, dtype=np.float32)
                xs = np.arange(mw, dtype=np.float32)
                map_x = (cxb + (xs[None, :] - cxb) / s[:, None]).astype(np.float32)
                map_y = np.zeros_like(map_x)
                ext = cv2.remap(bottom[None, :].astype(np.float32), map_x, map_y, cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                occ[y0 + mh:, x0:x0 + mw] = ext
        return occ

    def light_image(self, mask, softness_deg, strength, legs=True):
        """Proyektor "slaydi": spot * (1 - yumshoq soya)."""
        occ = self.build_occluder(mask, legs)
        # Burchak bo'yicha blur -> uzoqdagi yuzalarda penumbra kengroq (fizik jihatdan to'g'ri)
        sigma = max(0.5, math.tan(math.radians(softness_deg)) * self.fl)
        occ = cv2.GaussianBlur(occ, (0, 0), sigma)
        return self.spot * (1.0 - strength * occ)

    def light_frame(self, L, target):
        """Chiroq -> dunyo aylantirish matritsasi (proyektor "odam" nuqtasiga qaraydi)."""
        fwd = normalize(target - L)
        up = np.array([0.0, 1.0, 0.0])
        right = normalize(np.cross(up, fwd))
        down = np.cross(right, fwd)
        return fwd, np.stack([right, down, fwd], axis=1)

    def homographies(self, room, L, target, w, h):
        _, R = self.light_frame(L, target)
        M = R @ self.K_inv
        P = room.K(w, h) @ np.hstack([np.eye(3), np.zeros((3, 1))])
        Hs = []
        for n, c in room.planes:
            A = np.vstack([np.outer(L, n) + (c - n @ L) * np.eye(3), n[None, :]])
            Hs.append(P @ A @ M)
        return Hs


# ---------------------------------------------------------------------------
# Asosiy ilova
# ---------------------------------------------------------------------------

DEFAULT_PROCEDURAL = {"vp": [0.5, 0.42], "wall": [0.22, 0.16, 0.78, 0.70], "focal": 0.62}
DEFAULT_PHOTO = {"vp": [0.5, 0.45], "wall": [0.30, 0.25, 0.70, 0.70], "focal": 0.8}


class App:
    def __init__(self, args):
        self.args = args
        self.rw, self.rh = args.width, args.height
        self.lw, self.lh = int(self.rw * args.light_scale), int(self.rh * args.light_scale)
        self.photo = None
        self.calib_path = None
        self.calibrated = True
        if args.room:
            self.photo = cv2.imread(args.room)
            if self.photo is None:
                sys.exit(f"[x] Rasm o'qilmadi: {args.room}")
            self.photo = cover_resize(self.photo, self.rw, self.rh)
            self.calib_path = os.path.splitext(args.room)[0] + ".calib.json"
            if os.path.exists(self.calib_path):
                with open(self.calib_path, "r", encoding="utf-8") as f:
                    self.geo = json.load(f)
            else:
                self.geo = dict(DEFAULT_PHOTO)
                self.calibrated = False
        else:
            self.geo = dict(DEFAULT_PROCEDURAL)

        # Yorug'lik parametrlari (--room uchun fotoning o'z yorug'ligi bor -> ambient yuqoriroq)
        amb = args.ambient if args.ambient is not None else (0.55 if self.photo is not None else 0.30)
        key = args.key if args.key is not None else (0.65 if self.photo is not None else 1.05)
        self.ambient = np.array([1.00, 0.96, 0.93], np.float32) * amb   # BGR, biroz sovuq
        self.key = np.array([0.80, 0.93, 1.00], np.float32) * key       # BGR, iliq chiroq
        self.softness = args.softness
        self.strength = args.strength
        self.show_hud = True
        self.show_mask = False
        self.legs = True

        # Kadr manbai
        source = None
        if not args.demo:
            try:
                source = WebcamSource(args.camera, args.backend, args.mask_width)
            except Exception as e:
                print(f"[!] {e}. Demo rejimiga o'tildi (--demo).")
        if source is None:
            source = DemoSource(args.mask_width)
        self.source_name = source.name
        self.worker = CaptureWorker(source)
        self.projector = ShadowProjector(source.mask_size, hfov_deg=args.shadow_size)
        self.filter = AdaptiveMaskFilter()
        self.last_seq = -1
        self.mask = np.zeros((source.mask_size[1], source.mask_size[0]), np.float32)
        self.build_scene()

    # -- sahna ------------------------------------------------------------
    def build_scene(self):
        self.room = Room(self.geo["vp"], self.geo["wall"], self.geo["focal"], self.rw / self.rh)
        face_r, pts_r = self.room.raycast(self.rw, self.rh)
        if self.photo is not None:
            self.bg = self.photo.astype(np.float32)
        else:
            self.bg = render_procedural_room(self.room, self.rw, self.rh, face_r, pts_r).astype(np.float32)
        self.face, self.pts = self.room.raycast(self.lw, self.lh)
        self.face_masks = [self.face == k for k in range(5)]
        self.normals = np.stack([n for n, _ in self.room.planes]).astype(np.float32)[self.face]
        self.reset_light()

    def reset_light(self):
        r = self.room
        self.L = np.array([0.55, 0.15, -0.4])                         # chiroq (ko'zga nisbatan, metr)
        self.person = np.array([0.0, r.yb + 1.45, min(2.2, r.D * 0.4)])  # virtual odam turgan nuqta
        self.geom_dirty = True

    def update_geometry_light(self):
        """Lambert (cos) + masofa so'nishi. Faqat chiroq siljiganda qayta hisoblanadi."""
        L = self.L.astype(np.float32)
        V = L[None, None, :] - self.pts
        dist = np.linalg.norm(V, axis=-1) + 1e-6
        cos = np.clip((self.normals * V).sum(-1) / dist, 0.0, 1.0)
        fwd, _ = self.projector.light_frame(self.L, self.person)
        depth = (-(V) @ fwd.astype(np.float32))
        r0 = float(np.linalg.norm(self.person - self.L)) * 2.2
        g = cos * (1.0 / (1.0 + (dist / r0) ** 2)) * (depth > 0.05)
        T = np.array([self.person[0], self.person[1], self.room.D])     # normallash nuqtasi
        dT = float(np.linalg.norm(T - self.L))
        ref = (self.room.D - self.L[2]) / dT / (1.0 + (dT / r0) ** 2)
        self.geom = np.clip(g / ref, 0.0, 1.6).astype(np.float32)
        self.geom_dirty = False

    # -- render -----------------------------------------------------------
    def render(self):
        seq, target, preview = self.worker.latest()
        self.mask = self.filter.update(target if target is not None else self.mask)
        self.preview = preview
        if self.geom_dirty:
            self.update_geometry_light()

        slide = self.projector.light_image(self.mask, self.softness, self.strength, self.legs)
        Hs = self.projector.homographies(self.room, self.L, self.person, self.lw, self.lh)
        light = np.zeros((self.lh, self.lw), np.float32)
        for k, Hm in enumerate(Hs):
            warped = cv2.warpPerspective(slide, Hm, (self.lw, self.lh), flags=cv2.INTER_LINEAR,
                                         borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            np.copyto(light, warped, where=self.face_masks[k])
        light *= self.geom
        light = cv2.resize(light, (self.rw, self.rh), interpolation=cv2.INTER_LINEAR)

        gain = self.ambient[None, None, :] + light[..., None] * self.key[None, None, :]
        out = cv2.multiply(self.bg, gain)
        return np.clip(out, 0, 255).astype(np.uint8)

    def overlay(self, img, fps):
        if self.show_mask and self.preview is not None:
            pw = self.rw // 5
            ph = int(pw * self.preview.shape[0] / self.preview.shape[1])
            pv = cv2.resize(self.preview, (pw, ph))
            m = cv2.resize(self.mask, (pw, ph))[..., None]
            pv = (pv * (0.35 + 0.65 * m) + np.array([0, 60, 0]) * m).clip(0, 255).astype(np.uint8)
            img[10:10 + ph, self.rw - pw - 10:self.rw - 10] = pv
        if not self.show_hud:
            return
        lines = [
            f"{fps:5.1f} FPS | kamera {self.worker.fps:4.1f} FPS | {self.source_name}",
            "WASD: chiroq  Z/X: chuqurlik  +/-: soya o'lchami  [/]: yumshoqlik  ,/.: quyuqlik",
            "L: oyoq davomi  M: kamera  F: to'liq ekran  P: skrinshot  R: reset  H: yashirish  Q: chiqish",
        ]
        if self.photo is not None:
            lines.append("C: xona rasmini kalibrlash" + ("" if self.calibrated else "  <-- HALI KALIBRLANMAGAN!"))
        for i, t in enumerate(lines):
            draw_text(img, t, (14, 26 + 22 * i), 0.5)

    # -- kalibrlash (o'z rasmingiz uchun) --------------------------------
    CALIB_STEPS = [
        "1/6: Orqa devorning YUQORI-CHAP burchagi",
        "2/6: Orqa devorning YUQORI-O'NG burchagi",
        "3/6: Orqa devorning PASTKI-O'NG burchagi",
        "4/6: Orqa devorning PASTKI-CHAP burchagi",
        "5/6: Pol va CHAP devor tutashgan chiziqdagi istalgan nuqta (pastroqda)",
        "6/6: Pol va O'NG devor tutashgan chiziqdagi istalgan nuqta (pastroqda)",
    ]

    def calibrate(self):
        pts = []

        def on_mouse(ev, x, y, flags, _):
            if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 6:
                pts.append((float(x), float(y)))
            elif ev == cv2.EVENT_RBUTTONDOWN and pts:
                pts.pop()

        cv2.setMouseCallback(WINDOW_NAME, on_mouse)
        while len(pts) < 6:
            img = self.photo.copy()
            for i, p in enumerate(pts):
                cv2.circle(img, (int(p[0]), int(p[1])), 7, (0, 255, 255), 2, cv2.LINE_AA)
                draw_text(img, str(i + 1), (int(p[0]) + 9, int(p[1]) - 9), 0.6, (0, 255, 255))
            if len(pts) >= 2:
                for a, b in [(0, 1), (1, 2), (2, 3), (3, 0)][:len(pts) - 1]:
                    cv2.line(img, tuple(map(int, pts[a])), tuple(map(int, pts[b])), (0, 255, 255), 1, cv2.LINE_AA)
            draw_text(img, "KALIBRLASH - " + self.CALIB_STEPS[len(pts)], (14, 30), 0.65, (0, 255, 255), 2)
            draw_text(img, "Sichqoncha chap tugmasi: nuqta | o'ng tugma: bekor qilish | Esc: chiqish", (14, 58), 0.5)
            cv2.imshow(WINDOW_NAME, img)
            if (cv2.waitKey(20) & 0xFF) == 27:
                cv2.setMouseCallback(WINDOW_NAME, lambda *a: None)
                return
        cv2.setMouseCallback(WINDOW_NAME, lambda *a: None)

        w, h = self.rw, self.rh
        tl, tr, br, bl, pl, pr = [np.array(p) for p in pts]
        xl, xr = (tl[0] + bl[0]) / 2 / w, (tr[0] + br[0]) / 2 / w
        yt, yb = (tl[1] + tr[1]) / 2 / h, (bl[1] + br[1]) / 2 / h
        # Yo'qolish nuqtasi = pol-devor qirralari chiziqlarining kesishmasi
        a1, a2 = np.cross([*bl, 1], [*pl, 1]), np.cross([*br, 1], [*pr, 1])
        vp = np.cross(a1, a2)
        if abs(vp[2]) > 1e-6:
            vx, vy = vp[0] / vp[2] / w, vp[1] / vp[2] / h
        else:
            vx, vy = (xl + xr) / 2, (yt + yb) / 2
        vx = float(np.clip(vx, xl + 0.01, xr - 0.01))
        vy = float(np.clip(vy, yt + 0.01, yb - 0.01))
        self.geo = {"vp": [vx, vy], "wall": [xl, yt, xr, yb], "focal": self.geo.get("focal", 0.8)}
        with open(self.calib_path, "w", encoding="utf-8") as f:
            json.dump(self.geo, f, indent=2)
        print(f"[i] Kalibrlash saqlandi: {self.calib_path}")
        self.calibrated = True
        self.build_scene()

    # -- tugmalar ---------------------------------------------------------
    def handle_key(self, key):
        k = chr(key).lower() if 0 <= key < 256 else ""
        step = 0.15
        moves = {"a": (0, -step), "d": (0, step), "w": (1, step), "s": (1, -step), "z": (2, step), "x": (2, -step)}
        if key in (27,) or k == "q":
            return False
        if k in moves:
            i, dv = moves[k]
            self.L[i] += dv
            self.L[2] = min(self.L[2], self.room.z_front - 0.05)  # chiroq xonaning oldida qoladi
            self.L[1] = float(np.clip(self.L[1], self.room.yb + 0.2, self.room.yt - 0.1))
            self.geom_dirty = True
        elif k in ("+", "="):
            self.projector.set_fov(self.projector.hfov + 4)
        elif k in ("-", "_"):
            self.projector.set_fov(self.projector.hfov - 4)
        elif k == "]":
            self.softness = min(self.softness + 0.3, 8.0)
        elif k == "[":
            self.softness = max(self.softness - 0.3, 0.1)
        elif k == ".":
            self.strength = min(self.strength + 0.05, 1.0)
        elif k == ",":
            self.strength = max(self.strength - 0.05, 0.1)
        elif k == "h":
            self.show_hud = not self.show_hud
        elif k == "m":
            self.show_mask = not self.show_mask
        elif k == "l":
            self.legs = not self.legs
        elif k == "r":
            self.reset_light()
            self.projector.set_fov(self.args.shadow_size)
            self.softness, self.strength = self.args.softness, self.args.strength
        elif k == "f":
            full = cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN) == cv2.WINDOW_FULLSCREEN
            cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_NORMAL if full else cv2.WINDOW_FULLSCREEN)
        elif k == "p":
            name = time.strftime("shadow_%Y%m%d_%H%M%S.png")
            cv2.imwrite(name, self.last_frame)
            print(f"[i] Skrinshot: {name}")
        elif k == "c" and self.photo is not None:
            self.calibrate()
        return True

    # -- asosiy sikl ------------------------------------------------------
    def run(self):
        a = self.args
        self.worker.start()
        if not a.no_window:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
            if a.windowed:
                cv2.resizeWindow(WINDOW_NAME, self.rw, self.rh)
            else:
                cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            if self.photo is not None and not self.calibrated:
                print("[i] Xona rasmi hali kalibrlanmagan - C tugmasini bosing.")
        frame_time = 1.0 / a.fps
        fps, n, t_start = 0.0, 0, time.monotonic()
        # Birinchi maskani kutamiz (kamera "isinishi")
        while self.worker.latest()[1] is None and time.monotonic() - t_start < 5:
            time.sleep(0.02)
        try:
            while True:
                t0 = time.monotonic()
                img = self.render()
                self.last_frame = img.copy()
                self.overlay(img, fps)
                n += 1
                if a.no_window:
                    if a.frames and n >= a.frames:
                        break
                    continue
                cv2.imshow(WINDOW_NAME, img)
                wait = max(1, int((frame_time - (time.monotonic() - t0)) * 1000))
                key = cv2.waitKey(wait)
                if key != -1 and not self.handle_key(key & 0xFF):
                    break
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
                dt = time.monotonic() - t0
                fps = 0.9 * fps + 0.1 / max(dt, 1e-3)
                if a.frames and n >= a.frames:
                    break
        finally:
            elapsed = time.monotonic() - t_start
            if a.output:
                cv2.imwrite(a.output, self.last_frame)
                print(f"[i] Oxirgi kadr saqlandi: {a.output}")
            print(f"[i] {n} kadr, o'rtacha {n / max(elapsed, 1e-3):.1f} FPS")
            self.worker.stop()
            cv2.destroyAllWindows()


def parse_args():
    p = argparse.ArgumentParser(description="Virtual Shadow Projection - veb-kamera orqali virtual xonaga soya")
    p.add_argument("--room", help="Xona/garaj rasmi (jpg/png). Berilmasa - protsedural 3D xona")
    p.add_argument("--camera", type=int, default=0, help="Kamera indeksi (standart: 0)")
    p.add_argument("--backend", default="auto", choices=["auto", "mediapipe", "tasks", "legacy", "mog2"],
                   help="Segmentatsiya usuli")
    p.add_argument("--demo", action="store_true", help="Kamerasiz sintetik siluet bilan sinash")
    p.add_argument("--width", type=int, default=1280, help="Render kengligi (oyna ekranga cho'ziladi)")
    p.add_argument("--height", type=int, default=720, help="Render balandligi")
    p.add_argument("--light-scale", type=float, default=0.5, help="Yorug'lik xaritasi o'lchami (0.25-1)")
    p.add_argument("--mask-width", type=int, default=224, help="Siluet maskasi kengligi (piksel)")
    p.add_argument("--fps", type=int, default=60, help="Maksimal render FPS")
    p.add_argument("--shadow-size", type=float, default=55.0, help="Soya o'lchami = proyektor FOV (gradus): kattaroq = soya kattaroq")
    p.add_argument("--softness", type=float, default=1.2, help="Soya yumshoqligi (penumbra, gradus)")
    p.add_argument("--strength", type=float, default=0.92, help="Soya quyuqligi 0..1")
    p.add_argument("--ambient", type=float, default=None, help="Atrof yorug'ligi")
    p.add_argument("--key", type=float, default=None, help="Chiroq kuchi")
    p.add_argument("--windowed", action="store_true", help="To'liq ekran emas, oynada ochish")
    p.add_argument("--frames", type=int, default=0, help="N kadrdan keyin chiqish (test uchun)")
    p.add_argument("--output", help="Chiqishda oxirgi kadrni saqlash (PNG)")
    p.add_argument("--no-window", action="store_true", help="Oynasiz ishlash (test/benchmark)")
    return p.parse_args()


if __name__ == "__main__":
    App(parse_args()).run()
