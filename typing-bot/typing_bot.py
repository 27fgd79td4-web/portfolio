#!/usr/bin/env python3
"""
Typing Bot - Monkeytype, 10FastFingers va boshqa yozish o'yinlarida matnni
siz tanlagan tezlik (WPM) va aniqlikda avtomatik yozib beradi.

Ishlash tartibi:
  * Kompyuterdagi brauzerni o'zi topadi (Chrome -> Edge -> Playwright Chromium).
  * Sahifadagi so'zlarni to'g'ridan-to'g'ri DOM'dan o'qiydi (OCR emas),
    shuning uchun xato o'qish bo'lmaydi.
  * Tugmalarni brauzerning o'z kiritish tizimi (CDP) orqali bosadi, shuning
    uchun sichqoncha yoki boshqa oynalarga ta'sir qilmaydi.

Boshqaruv:  oynadagi tugmalar yoki brauzer ichida  F8 = boshlash,  F9 = to'xtatish.
"""
import argparse
import os
import queue
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, Error as PWError
except ImportError:  # launcher odatda o'rnatadi, lekin qo'lda ishga tushirilsa
    print("[x] 'playwright' o'rnatilmagan:  pip install playwright")
    sys.exit(1)

PROFILE_DIR = Path.home() / ".typing-bot-profile"

# --------------------------------------------------------------------------
# Saytlar. Har bir sayt JS funksiyasi orqali joriy holatni qaytaradi:
#   mode="word":   {done, ready, id, word, typed, bad}  - faol so'z (Monkeytype)
#   mode="stream": {done, ready, text}                  - butun matn, ofsetni bot o'zi kuzatadi
# --------------------------------------------------------------------------
MONKEYTYPE_JS = r"""() => {
  const res = document.querySelector('#result');
  if (res && !res.classList.contains('hidden') && res.offsetParent !== null) return {done: true};
  const w = document.querySelector('#words .word.active');
  if (!w) return {ready: false};
  const letters = [...w.querySelectorAll('letter')];
  const real = letters.filter(l => !l.classList.contains('extra'));
  const typed = letters.filter(l => l.classList.contains('correct') || l.classList.contains('incorrect')
                                  || l.classList.contains('extra')).length;
  const bad = letters.some(l => l.classList.contains('incorrect') || l.classList.contains('extra'));
  return {ready: true, id: w.dataset.wordindex + ':' + real.map(l => l.textContent).join(''),
          word: real.map(l => l.textContent).join(''), typed, bad};
}"""

TENFF_JS = r"""() => {
  const box = document.querySelector('[data-testid="word-box-words"]');
  const tb = document.querySelector('[data-testid="TypingBox-root"]');
  const timeUp = !!tb && /(^|\n)00:00(\n|$)/.test(tb.innerText);
  if (!box) return {done: true};
  const text = box.textContent;
  return {ready: text.trim().length > 0, done: timeUp, text};
}"""

SELECTION_JS = r"""() => ({ready: !!window.__tbSel, text: window.__tbSel || ''})"""

SITES = {
    "Monkeytype": {
        "url": "https://monkeytype.com/",
        "mode": "word",
        "js": MONKEYTYPE_JS,
        "focus": "#wordsInput",
        "click": "#wordsWrapper",
        "dismiss": 'button:has-text("reject non-essential")',
    },
    "10FastFingers": {
        "url": "https://10fastfingers.com/typing-test/english/",
        "mode": "stream",
        "js": TENFF_JS,
        "focus": '[data-testid="Typing-box-input"]',
        "click": None,
        "dismiss": '[data-testid="InactiveBanner"]',
    },
    "Universal (belgilangan matn)": {
        "url": "about:blank",
        "mode": "stream",
        "js": SELECTION_JS,
        "focus": None,
        "click": None,
        "once": True,
    },
}

# Brauzer ichidagi yordamchi: F8/F9 tugmalari va oxirgi belgilangan matnni eslab qolish
INIT_JS = r"""
(() => {
  if (window.__tbInit) return; window.__tbInit = true;
  document.addEventListener('keydown', e => {
    if (e.key === 'F8' || e.key === 'F9') {
      e.preventDefault(); e.stopPropagation();
      if (window.__tbHotkey) window.__tbHotkey(e.key);
    }
  }, true);
  document.addEventListener('selectionchange', () => {
    const s = String(window.getSelection() || '').replace(/\s+/g, ' ').trim();
    if (s.length > 1) window.__tbSel = s;
  });
})();
"""

# QWERTY qo'shni tugmalar - "xato" qilish uchun tasodifiy qo'shni harf
NEIGHBORS = {
    'q': 'wa', 'w': 'qes', 'e': 'wrd', 'r': 'etf', 't': 'ryg', 'y': 'tuh', 'u': 'yij',
    'i': 'uok', 'o': 'ipl', 'p': 'ol', 'a': 'qsz', 's': 'awdz', 'd': 'sefx', 'f': 'drgc',
    'g': 'fthv', 'h': 'gyjb', 'j': 'hukn', 'k': 'jilm', 'l': 'kop', 'z': 'asx', 'x': 'zsdc',
    'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn', 'n': 'bhjm', 'm': 'njk',
}


class Stopped(Exception):
    pass


class Settings:
    """GUI va ishchi oqim o'rtasida bo'lishiladigan sozlamalar."""

    def __init__(self, wpm=120, accuracy=100, fix_errors=True, jitter=25):
        self.wpm = wpm
        self.accuracy = accuracy      # foizda, 100 = hech qanday xato yo'q
        self.fix_errors = fix_errors  # xatoni Backspace bilan tuzatish
        self.jitter = jitter          # tezlikning tasodifiy tebranishi, %
        self.max_speed = False


class Typist:
    """Belgilarni berilgan ritmda bosadi."""

    def __init__(self, page, settings, stop_event):
        self.page = page
        self.s = settings
        self.stop = stop_event
        self.next_at = time.perf_counter()
        self.chars = 0
        self.errors = 0
        self.t0 = None

    def _wait_turn(self):
        if self.stop.is_set():
            raise Stopped
        if self.s.max_speed:
            return
        # 1 so'z = 5 belgi  ->  belgi oralig'i = 12 / WPM soniya
        base = 12.0 / max(1, self.s.wpm)
        j = self.s.jitter / 100.0
        self.next_at += base * random.uniform(1 - j, 1 + j)
        now = time.perf_counter()
        if self.next_at < now - 0.25:   # kechikish to'planib qolmasin
            self.next_at = now
        delay = self.next_at - now
        if delay > 0:
            time.sleep(delay)

    def key(self, ch):
        self._wait_turn()
        if self.t0 is None:
            self.t0 = time.perf_counter()
        if ch == ' ':
            self.page.keyboard.press('Space')
        else:
            self.page.keyboard.type(ch)
        self.chars += 1
        self.t_last = time.perf_counter()

    def backspace(self, n=1):
        for _ in range(n):
            self._wait_turn()
            self.page.keyboard.press('Backspace')

    def char(self, ch):
        """Bitta belgi; sozlangan aniqlikka qarab ba'zan xato qiladi."""
        if (not self.s.max_speed and self.s.accuracy < 100 and ch.lower() in NEIGHBORS
                and random.random() * 100 > self.s.accuracy):
            wrong = random.choice(NEIGHBORS[ch.lower()])
            self.key(wrong.upper() if ch.isupper() else wrong)
            self.errors += 1
            if self.s.fix_errors:
                self.backspace()
                self.key(ch)
            return
        self.key(ch)

    def text(self, s):
        for ch in s:
            self.char(ch)

    def stats(self):
        if not self.t0:
            return ""
        dt = max(0.001, self.t_last - self.t0)
        return f"{self.chars} belgi, ~{self.chars / 5 / dt * 60:.0f} WPM, {self.errors} xato"


class Bot:
    """Barcha Playwright amallari shu klassning bitta oqimida bajariladi."""

    def __init__(self, settings, log, headless=False, executable=None):
        self.s = settings
        self.log = log
        self.headless = headless
        self.executable = executable
        self.cmds = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.site = None
        self.ctx = None
        self.page = None
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()

    # ---- tashqi API (istalgan oqimdan) ----
    def open_site(self, name):
        self.cmds.put(("open", name))

    def start(self):
        self.stop_event.clear()
        self.cmds.put(("start", None))

    def stop_typing(self):
        self.stop_event.set()

    def quit(self):
        self.stop_event.set()
        self.cmds.put(("quit", None))

    # ---- ishchi oqim ----
    def _main(self):
        try:
            with sync_playwright() as pw:
                self.pw = pw
                while True:
                    try:
                        cmd, arg = self.cmds.get(timeout=0.1)
                    except queue.Empty:
                        self._pump()
                        continue
                    if cmd == "quit":
                        break
                    try:
                        if cmd == "open":
                            self._open(arg)
                        elif cmd == "start":
                            self._run()
                        elif cmd == "shot":
                            self.page.wait_for_timeout(1500)
                            self.page.screenshot(path=arg)
                            self.log(f"Skrinshot: {arg}")
                    except Stopped:
                        self.log("To'xtatildi.")
                    except PWError as e:
                        self.log(f"Brauzer xatosi: {str(e).splitlines()[0]}")
                    finally:
                        self.running = False
                if self.ctx:
                    try:
                        self.ctx.close()
                    except Exception:
                        pass
        except Exception as e:  # noqa: BLE001
            self.log(f"Xato: {e}")

    def _pump(self):
        # Brauzer hodisalari (F8/F9) ishlov berilishi uchun
        if self.page and not self.page.is_closed():
            try:
                self.page.wait_for_timeout(1)
            except PWError:
                pass

    def _launch(self):
        """Kompyuterga mos brauzerni topib ochadi."""
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=self.headless, no_viewport=True, args=["--start-maximized"])
        proxy = os.environ.get("HTTPS_PROXY")
        if proxy and self.headless:   # faqat test muhiti uchun
            opts["proxy"] = {"server": proxy}
            opts["ignore_https_errors"] = True
        attempts = []
        if self.executable:
            attempts.append(("berilgan brauzer", dict(executable_path=self.executable)))
        attempts += [("Google Chrome", dict(channel="chrome")),
                     ("Microsoft Edge", dict(channel="msedge")),
                     ("Chromium", {})]
        for name, extra in attempts:
            try:
                ctx = self.pw.chromium.launch_persistent_context(str(PROFILE_DIR / name.replace(' ', '_')),
                                                                 **opts, **extra)
                self.log(f"Brauzer: {name}")
                return ctx
            except PWError:
                continue
        self.log("Brauzer topilmadi, Chromium yuklab olinmoqda (~150 MB, bir marta)...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
        ctx = self.pw.chromium.launch_persistent_context(str(PROFILE_DIR / "Chromium"), **opts)
        self.log("Brauzer: Chromium")
        return ctx

    def _ensure_page(self):
        if self.ctx is None or self.page is None or self.page.is_closed():
            if self.ctx is not None:
                try:
                    self.ctx.close()
                except Exception:
                    pass
            self.ctx = self._launch()
            self.ctx.add_init_script(INIT_JS)
            self.ctx.expose_binding("__tbHotkey", lambda src, key: self._hotkey(key))
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _hotkey(self, key):
        if key == "F9":
            self.stop_typing()
        elif key == "F8" and not self.running:
            self.start()

    def _open(self, name):
        self._ensure_page()
        self.site = name
        url = SITES[name]["url"]
        if url != "about:blank":
            self.log(f"{name} ochilmoqda...")
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.bring_to_front()
        self.log("Tayyor. Testni tanlang va 'Boshlash' (yoki brauzerda F8) ni bosing.")

    def _focus(self, site):
        if site.get("dismiss"):   # cookie oynasi fokusni o'g'irlaydi
            btn = self.page.locator(site["dismiss"]).first
            try:
                if btn.is_visible():
                    btn.click(timeout=2000)
                    self.page.wait_for_timeout(500)
            except PWError:
                pass
        if site.get("click"):
            try:
                self.page.click(site["click"], timeout=3000, position={"x": 5, "y": 5})
            except PWError:
                pass
        if site.get("focus"):
            try:
                self.page.focus(site["focus"], timeout=3000)
            except PWError:
                pass

    def _wait_ready(self, js, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.stop_event.is_set():
                raise Stopped
            st = self.page.evaluate(js)
            if st.get("ready"):
                return st
            time.sleep(0.1)
        return {}

    def _run(self):
        if self.page is None or self.page.is_closed() or not self.site:
            self.log("Avval saytni oching.")
            return
        site = SITES[self.site]
        self.running = True
        self.page.evaluate(INIT_JS)  # agar sahifa init skriptdan oldin ochilgan bo'lsa
        st = self._wait_ready(site["js"])
        if not st.get("ready"):
            self.log("So'zlar topilmadi. Test sahifasi ochiqligini tekshiring"
                     + (" yoki matnni sichqoncha bilan belgilang." if site.get("once") else "."))
            return
        if site.get("once"):
            self.log("Belgilangan matn yoziladi. 3 soniyada kerakli maydonga bosing!")
            for _ in range(30):
                if self.stop_event.is_set():
                    raise Stopped
                time.sleep(0.1)
        else:
            self._focus(site)
        typist = Typist(self.page, self.s, self.stop_event)
        self.log("Yozilmoqda... (to'xtatish: F9)")
        if site["mode"] == "word":
            self._run_word(site, typist)
        else:
            self._run_stream(site, typist)
        self.log(f"Tugadi: {typist.stats()}")

    def _run_word(self, site, t):
        last_id = None
        idle_since = time.time()
        while True:
            st = self.page.evaluate(site["js"])
            if st.get("done"):
                return
            if not st.get("ready") or st["id"] == last_id:
                if time.time() - idle_since > 3:
                    return  # test tugagan yoki sahifa o'zgargan
                if self.stop_event.is_set():
                    raise Stopped
                time.sleep(0.02)
                continue
            idle_since = time.time()
            word = st["word"]
            if st["bad"]:
                t.backspace(st["typed"])
                start = 0
            else:
                start = st["typed"]
            t.text(word[start:])
            t.key(' ')
            last_id = st["id"]

    def _run_stream(self, site, t):
        pos = 0
        idle_since = time.time()
        while True:
            st = self.page.evaluate(site["js"])
            if st.get("done") and pos > 0:   # yozishdan oldingi "00:00" ni hisobga olmaymiz
                return
            text = st.get("text", "")
            if pos >= len(text):
                if site.get("once") or time.time() - idle_since > 3:
                    return
                time.sleep(0.05)
                continue
            idle_since = time.time()
            # keyingi so'zni (bo'sh joy bilan birga) yozamiz, keyin holatni qayta tekshiramiz
            nxt = text.find(' ', pos)
            end = len(text) if nxt < 0 else nxt + 1
            t.text(text[pos:end])
            pos = end


# --------------------------------------------------------------------------
# Grafik oyna (tkinter - Python bilan birga keladi)
# --------------------------------------------------------------------------
def run_gui(args):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Typing Bot")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    logq = queue.Queue()
    settings = Settings(wpm=args.wpm, accuracy=args.accuracy)
    bot = Bot(settings, logq.put, executable=args.browser)

    pad = dict(padx=10, pady=4)
    frm = ttk.Frame(root, padding=10)
    frm.grid()

    ttk.Label(frm, text="Sayt:").grid(row=0, column=0, sticky="w", **pad)
    site_var = tk.StringVar(value=args.site)
    ttk.Combobox(frm, textvariable=site_var, values=list(SITES), state="readonly",
                 width=28).grid(row=0, column=1, columnspan=2, sticky="we", **pad)

    wpm_var = tk.IntVar(value=settings.wpm)
    wpm_lbl = ttk.Label(frm, text=f"Tezlik: {settings.wpm} WPM", width=20)
    wpm_lbl.grid(row=1, column=0, sticky="w", **pad)

    def on_wpm(v):
        settings.wpm = int(float(v))
        wpm_lbl.config(text=f"Tezlik: {settings.wpm} WPM")
    ttk.Scale(frm, from_=20, to=400, variable=wpm_var, command=on_wpm,
              length=220).grid(row=1, column=1, columnspan=2, **pad)

    acc_var = tk.DoubleVar(value=settings.accuracy)
    acc_lbl = ttk.Label(frm, text=f"Aniqlik: {settings.accuracy:.0f}%", width=20)
    acc_lbl.grid(row=2, column=0, sticky="w", **pad)

    def on_acc(v):
        settings.accuracy = round(float(v))
        acc_lbl.config(text=f"Aniqlik: {settings.accuracy}%")
    ttk.Scale(frm, from_=85, to=100, variable=acc_var, command=on_acc,
              length=220).grid(row=2, column=1, columnspan=2, **pad)

    fix_var = tk.BooleanVar(value=True)
    max_var = tk.BooleanVar(value=False)

    def on_checks():
        settings.fix_errors = fix_var.get()
        settings.max_speed = max_var.get()
    ttk.Checkbutton(frm, text="Xatolarni tuzatish", variable=fix_var,
                    command=on_checks).grid(row=3, column=0, sticky="w", **pad)
    ttk.Checkbutton(frm, text="Maksimal tezlik", variable=max_var,
                    command=on_checks).grid(row=3, column=1, sticky="w", **pad)

    ttk.Button(frm, text="1. Saytni ochish",
               command=lambda: bot.open_site(site_var.get())).grid(row=4, column=0, sticky="we", **pad)
    ttk.Button(frm, text="2. Boshlash (F8)", command=bot.start).grid(row=4, column=1, sticky="we", **pad)
    ttk.Button(frm, text="To'xtatish (F9)", command=bot.stop_typing).grid(row=4, column=2, sticky="we", **pad)

    log = tk.Text(frm, height=8, width=58, state="disabled", wrap="word")
    log.grid(row=5, column=0, columnspan=3, **pad)

    def poll():
        while True:
            try:
                msg = logq.get_nowait()
            except queue.Empty:
                break
            log.config(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.config(state="disabled")
        root.after(100, poll)

    def on_close():
        bot.quit()
        root.after(300, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    logq.put("Salom! 1) Saytni tanlab oching  2) Boshlash ni bosing.")
    poll()
    root.mainloop()


def run_cli(args):
    """Oynasiz rejim: saytni ochadi va darhol yozadi (sinov uchun)."""
    settings = Settings(wpm=args.wpm, accuracy=args.accuracy)
    settings.max_speed = args.max
    done = threading.Event()

    def log(msg):
        print(msg, flush=True)
        if msg.startswith(("Tugadi", "So'zlar topilmadi", "Brauzer xatosi", "To'xtatildi")):
            done.set()

    bot = Bot(settings, log, headless=args.headless, executable=args.browser)
    bot.open_site(args.site)
    time.sleep(1)
    while bot.page is None:
        time.sleep(0.2)
    time.sleep(args.delay)
    bot.start()
    done.wait()
    if args.screenshot:
        bot.cmds.put(("shot", args.screenshot))
        time.sleep(3)
    bot.quit()
    bot.thread.join(5)


def main():
    ap = argparse.ArgumentParser(description="Yozish o'yinlari uchun avto-yozuvchi")
    ap.add_argument("--site", default="Monkeytype", choices=list(SITES))
    ap.add_argument("--wpm", type=int, default=120)
    ap.add_argument("--accuracy", type=int, default=100)
    ap.add_argument("--browser", help="Brauzer .exe yo'li (ixtiyoriy)")
    ap.add_argument("--auto", action="store_true", help="Oynasiz: ochib darhol yozish")
    ap.add_argument("--max", action="store_true", help="--auto bilan: maksimal tezlik")
    ap.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--delay", type=float, default=3, help=argparse.SUPPRESS)
    ap.add_argument("--screenshot", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.auto:
        run_cli(args)
    else:
        run_gui(args)


if __name__ == "__main__":
    main()
