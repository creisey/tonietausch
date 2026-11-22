#!/usr/bin/env python3
# TonieTausch – lokaler Server (macOS & Windows)
import os, re, io, csv, sys, json, time, glob, stat, shutil, threading, subprocess, tempfile
import urllib.request, urllib.parse, plistlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)
DATA = os.path.join(ROOT, "daten")
BACKUP = os.path.join(DATA, "originale")
CUSTOM = os.path.join(DATA, "eigene")
WORK = os.path.join(DATA, "arbeit")
CARDBAK = os.path.join(DATA, "kartensicherungen")
TRASH = os.path.join(DATA, "geloescht")
LIB = os.path.join(DATA, "bibliothek.json")
TJ = os.path.join(DATA, "tonies.json")
TJ_URL = "https://raw.githubusercontent.com/toniebox-reverse-engineering/tonies-json/release/tonies.json"
BIN = os.path.dirname(sys.executable)
TT = os.path.join(BIN, "tonietoolbox.exe" if IS_WIN else "tonietoolbox")
PORT = 8765
HEX8 = re.compile(r"^[0-9A-Fa-f]{8}$")
LOCK = threading.RLock()
TONIES = {}
TONIE_LIST = []
CHAP = {}
SERVER = None
SKIP = {".Spotlight-V100", ".Trashes", ".fseventsd", ".DS_Store", ".TemporaryItems",
        "System Volume Information", "$RECYCLE.BIN"}
FAT32_MAX = 32 * 1024 ** 3
NOWIN = {"creationflags": 0x08000000} if IS_WIN else {}

for d in (DATA, BACKUP, CUSTOM, WORK, CARDBAK, TRASH):
    os.makedirs(d, exist_ok=True)

if IS_WIN:
    import ctypes
    K32 = ctypes.windll.kernel32
    K32.SetErrorMode(0x0001 | 0x8000)
    K32.GetFileAttributesW.restype = ctypes.c_uint32

# ---------- tonies.json ----------
def read_tonies():
    global TONIES, TONIE_LIST
    idx = {}
    try:
        with open(TJ, encoding="utf-8") as f:
            TONIE_LIST = json.load(f)
            for t in TONIE_LIST:
                for a in (t.get("audio_id") or []):
                    if a:
                        idx[str(a)] = t
    except Exception:
        pass
    TONIES = idx

def refresh_tonies():
    try:
        if os.path.exists(TJ) and time.time() - os.path.getmtime(TJ) < 7 * 86400:
            return
        with urllib.request.urlopen(TJ_URL, timeout=60) as r:
            data = r.read()
        json.loads(data)
        with open(TJ + ".tmp", "wb") as f:
            f.write(data)
        os.replace(TJ + ".tmp", TJ)
        read_tonies()
    except Exception as e:
        print("tonies.json konnte nicht geladen werden:", e, flush=True)

# ---------- Tonie-Header ----------
def varint(b, i):
    r = s = 0
    while True:
        c = b[i]; i += 1
        r |= (c & 0x7F) << s; s += 7
        if not c & 0x80:
            return r, i

def parse_header(b):
    h = {"hash": None, "audio_id": None, "pages": []}
    i = 0
    try:
        while i < len(b):
            key, i = varint(b, i)
            fld, wt = key >> 3, key & 7
            if wt == 0:
                v, i = varint(b, i)
                if fld == 3:
                    h["audio_id"] = v
                elif fld == 4:
                    h["pages"].append(v)
            elif wt == 2:
                l, i = varint(b, i)
                v = b[i:i + l]; i += l
                if fld == 1:
                    h["hash"] = v.hex()
                elif fld == 4:
                    j = 0
                    while j < len(v):
                        c, j = varint(v, j)
                        h["pages"].append(c)
            elif wt == 5:
                i += 4
            elif wt == 1:
                i += 8
            else:
                break
    except IndexError:
        pass
    return h

def read_header(path):
    try:
        with open(path, "rb") as f:
            n = int.from_bytes(f.read(4), "big")
            if n <= 0 or n > 8192:
                return None
            b = f.read(n)
    except OSError:
        return None
    h = parse_header(b)
    return {"hash": h["hash"], "audio_id": h["audio_id"]} if h["hash"] else None

def audio_info(path):
    with open(path, "rb") as f:
        n = int.from_bytes(f.read(4), "big")
        pages = parse_header(f.read(n))["pages"]
    off = 4 + n
    before, last = {}, 0
    with open(path, "rb") as f:
        f.seek(off)
        while True:
            hdr = f.read(27)
            if len(hdr) < 27 or hdr[:4] != b"OggS":
                break
            gran = int.from_bytes(hdr[6:14], "little", signed=True)
            seq = int.from_bytes(hdr[18:22], "little")
            segs = f.read(hdr[26])
            before[seq] = last
            if gran > 0:
                last = gran
            f.seek(sum(segs), 1)
    starts = [before.get(p, 0) / 48000 for p in pages] or [0]
    return off, starts, last / 48000

# ---------- Bibliothek ----------
def load_lib():
    try:
        with open(LIB, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_lib(lib):
    with open(LIB + ".tmp", "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=1)
    os.replace(LIB + ".tmp", LIB)

def fix_path(p):
    """Findet Dateien wieder, wenn der Datenordner verschoben wurde."""
    if not p:
        return ""
    if os.path.isfile(p):
        return p
    q = p.replace("\\", "/")
    i = q.rfind("/daten/")
    if i >= 0:
        cand = os.path.join(DATA, *q[i + 7:].split("/"))
        if os.path.isfile(cand):
            return cand
    return p

# ---------- Hilfen ----------
def env():
    e = dict(os.environ)
    if not IS_WIN:
        e["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + e.get("PATH", "/usr/bin:/bin")
    return e

def safe_id(s):
    s = re.sub(r"[^A-Za-z0-9]", "", s or "")
    if not s:
        raise ValueError("Ungültige Sitzung")
    return s[:64]

def safe_name(n):
    n = os.path.basename((n or "").replace("\\", "/")).strip()
    if not n or n.startswith("."):
        raise ValueError("Ungültiger Dateiname")
    return n

def title_of(n):
    return os.path.splitext(n.split("__", 1)[-1])[0]

def content_file(card, key):
    parts = (key or "").split("/")
    if len(parts) != 2 or not all(HEX8.match(x) for x in parts):
        raise ValueError("Ungültige Figur")
    return os.path.join(card, "CONTENT", parts[0], parts[1])

def is_system(lk, e):
    return lk.split("/")[0].startswith("0000000") or \
        ((e.get("figur") or {}).get("serie") or "").lower().startswith("system")

# ---------- Plattform: Karten ----------
def win_label(root):
    buf = ctypes.create_unicode_buffer(261)
    ok = K32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261, None, None, None, None, 0)
    return buf.value if ok else ""

def cards():
    res = []
    if IS_WIN:
        mask = K32.GetLogicalDrives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = "%s:\\" % chr(65 + i)
            try:
                if os.path.isdir(os.path.join(root, "CONTENT")):
                    lab = win_label(root)
                    res.append({"name": "%s (%s)" % (lab, root[:2]) if lab else root[:2], "path": root})
            except OSError:
                pass
        return res
    try:
        for v in sorted(os.listdir("/Volumes")):
            p = os.path.join("/Volumes", v)
            try:
                if os.path.isdir(os.path.join(p, "CONTENT")):
                    res.append({"name": v, "path": p})
            except OSError:
                pass
    except OSError:
        pass
    return res

def check_card(p):
    for c in cards():
        if c["path"] == p:
            return p
    raise ValueError("SD-Karte nicht gefunden. Bitte „Neu laden“ klicken.")

def card_label(p):
    return next((c["name"] for c in cards() if c["path"] == p), "Karte")

# ---------- Plattform: LIVE-Flag (versteckt) ----------
def is_live(p):
    try:
        st = os.stat(p)
        if IS_WIN:
            return bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
        return bool(st.st_flags & stat.UF_HIDDEN)
    except (OSError, AttributeError):
        return False

def set_live(p, on):
    if IS_WIN:
        a = K32.GetFileAttributesW(p)
        if a == 0xFFFFFFFF:
            raise OSError("Dateiattribute nicht lesbar: " + p)
        n = (a | 0x2) if on else (a & ~0x2)
        if n != a and not K32.SetFileAttributesW(p, n or 0x80):
            raise OSError("Dateiattribute nicht änderbar: " + p)
        return
    st = os.stat(p)
    fl = (st.st_flags | stat.UF_HIDDEN) if on else (st.st_flags & ~stat.UF_HIDDEN)
    if fl != st.st_flags:
        os.chflags(p, fl)

def write_to_card(src, dst):
    live = os.path.exists(dst) and is_live(dst)
    if live:
        set_live(dst, False)
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    with open(tmp, "rb+") as f:
        os.fsync(f.fileno())
    os.replace(tmp, dst)
    if live:
        set_live(dst, True)

def backup_current(src, lk, e):
    h = read_header(src)
    if not h:
        return
    cust = e.get("eigen")
    if cust and h["hash"] == cust.get("hash"):
        return
    backups = e.setdefault("backups", [])
    if any(b["hash"] == h["hash"] and os.path.isfile(fix_path(b["datei"])) for b in backups):
        return
    dst = os.path.join(BACKUP, "%s_%s.taf" % (lk.replace("/", "_"), h["audio_id"]))
    shutil.copyfile(src, dst)
    backups.append({"hash": h["hash"], "audio_id": h["audio_id"], "datei": dst, "zeit": int(time.time())})

# ---------- Figuren ----------
def scan(card):
    out = []
    with LOCK:
        lib = load_lib(); changed = False
        root = os.path.join(card, "CONTENT")
        for d in sorted(os.listdir(root)):
            dp = os.path.join(root, d)
            if not HEX8.match(d) or not os.path.isdir(dp):
                continue
            for f in sorted(os.listdir(dp)):
                fp = os.path.join(dp, f)
                if not HEX8.match(f) or not os.path.isfile(fp):
                    continue
                key = d + "/" + f
                lk = key.upper()
                h = read_header(fp) or {}
                e = lib.get(lk, {})
                t = TONIES.get(str(h.get("audio_id"))) if h else None
                if t and not e.get("figur"):
                    e["figur"] = {"name": t.get("episodes") or t.get("title") or "",
                                  "serie": t.get("series") or "", "bild": t.get("pic") or "",
                                  "titel": t.get("tracks") or []}
                    lib[lk] = e; changed = True
                cust = e.get("eigen")
                if cust and h.get("hash") == cust.get("hash"):
                    status = "eigen"
                elif cust and e.get("aktiv") == "eigen":
                    status = "ueberschrieben"
                else:
                    status = "original"
                fig = e.get("figur") or {}
                out.append({
                    "key": key, "status": status,
                    "name": fig.get("name") or "Unbekannte Figur",
                    "serie": fig.get("serie", ""), "bild": fig.get("bild", ""),
                    "originalTitel": fig.get("titel", []),
                    "eigen": {"name": cust.get("name", ""), "titel": cust.get("titel", [])} if cust else None,
                    "hatBackup": bool(e.get("backups")),
                    "system": is_system(lk, e),
                    "live": is_live(fp),
                })
        if changed:
            save_lib(lib)
    return out

def apply(card, key, session, order, name):
    src = content_file(card, key)
    if not os.path.isfile(src):
        raise ValueError("Figur nicht auf der Karte gefunden.")
    lk = key.upper()
    sdir = os.path.join(WORK, session)
    files = [os.path.join(sdir, safe_name(n)) for n in order]
    if not files or not all(os.path.isfile(p) for p in files):
        raise ValueError("Hochgeladene Dateien fehlen.")
    out_dir = os.path.join(sdir, "out")
    os.makedirs(out_dir, exist_ok=True)
    lst = os.path.join(sdir, "liste.lst")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(files) + "\n")
    ts = int(time.time()) - 0x50000000
    r = subprocess.run([TT, lst, "tonie.taf", "-t", str(ts), "-A"], cwd=out_dir, env=env(),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **NOWIN)
    tafs = sorted(glob.glob(os.path.join(out_dir, "**", "*.taf"), recursive=True), key=os.path.getmtime)
    if r.returncode != 0 or not tafs:
        raise RuntimeError("Umwandlung fehlgeschlagen:\n" + ((r.stderr or "") + (r.stdout or ""))[-1500:])
    new = tafs[-1]
    nh = read_header(new)
    if not nh:
        raise RuntimeError("Die erzeugte Datei ist ungültig.")
    with LOCK:
        lib = load_lib(); e = lib.setdefault(lk, {})
        backup_current(src, lk, e)
        keep = os.path.join(CUSTOM, lk.replace("/", "_") + ".taf")
        shutil.copyfile(new, keep)
        write_to_card(keep, src)
        e["eigen"] = {"hash": nh["hash"], "audio_id": nh["audio_id"], "datei": keep,
                      "name": name, "titel": [title_of(n) for n in order]}
        e["aktiv"] = "eigen"
        save_lib(lib)
    shutil.rmtree(sdir, ignore_errors=True)

def restore_original(card, key):
    src = content_file(card, key); lk = key.upper()
    with LOCK:
        lib = load_lib(); e = lib.get(lk) or {}
        cust = e.get("eigen") or {}
        backups = [b for b in e.get("backups", [])
                   if b["hash"] != cust.get("hash") and os.path.isfile(fix_path(b["datei"]))]
        if not backups:
            raise ValueError("Für diese Figur ist kein Original gesichert.")
        backup_current(src, lk, e)
        write_to_card(fix_path(backups[-1]["datei"]), src)
        e["aktiv"] = "original"; lib[lk] = e
        save_lib(lib)

def restore_custom(card, key):
    src = content_file(card, key); lk = key.upper()
    with LOCK:
        lib = load_lib(); e = lib.get(lk) or {}
        c = e.get("eigen")
        if not c or not os.path.isfile(fix_path(c.get("datei", ""))):
            raise ValueError("Für diese Figur sind keine eigenen Titel gesichert.")
        backup_current(src, lk, e)
        write_to_card(fix_path(c["datei"]), src)
        e["aktiv"] = "eigen"; lib[lk] = e
        save_lib(lib)

def restore_all(card):
    for f in scan(card):
        if f["status"] == "ueberschrieben":
            restore_custom(card, f["key"])

def export_csv(card):
    label = {"original": "Original", "eigen": "Eigene Titel",
             "ueberschrieben": "Original zurück (eigene Titel gesichert)"}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Figur", "Serie", "Ordner", "Status", "Inhalt", "Nr", "Titel"])
    for f in scan(card):
        if f["status"] == "eigen":
            inhalt = f["eigen"]["name"] or "Eigener Inhalt"; titel = f["eigen"]["titel"]
        else:
            inhalt = "Original"; titel = f["originalTitel"]
        base = [f["name"], f["serie"], f["key"],
                ("System – " if f["system"] else "") + label[f["status"]], inhalt]
        if not titel:
            w.writerow(base + ["", ""])
        for i, t in enumerate(titel, 1):
            w.writerow(base + [i, t])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")

# ---------- Abspielen ----------
def play_info(card, key):
    src = content_file(card, key)
    h = read_header(src)
    if not h:
        raise ValueError("Diese Datei kann nicht abgespielt werden.")
    info = CHAP.get(h["hash"]) or audio_info(src)
    CHAP[h["hash"]] = info
    off, starts, dauer = info
    e = load_lib().get(key.upper(), {})
    cust = e.get("eigen") or {}
    titel = cust.get("titel", []) if cust.get("hash") == h["hash"] else (e.get("figur") or {}).get("titel", [])
    kap = [{"nr": i + 1, "titel": titel[i] if i < len(titel) else "Kapitel %d" % (i + 1), "start": s}
           for i, s in enumerate(starts)]
    src_url = "/api/audio?" + urllib.parse.urlencode({"karte": card, "key": key, "v": h["hash"][:8]})
    return {"src": src_url, "kapitel": kap, "dauer": dauer}

# ---------- Löschen & Übertragen ----------
def delete_figure(card, key):
    src = content_file(card, key)
    lk = key.upper()
    if not os.path.isfile(src):
        raise ValueError("Figur nicht auf der Karte gefunden.")
    with LOCK:
        lib = load_lib(); e = lib.setdefault(lk, {})
        if is_system(lk, e):
            raise ValueError("Systemklänge können nicht gelöscht werden.")
        h = read_header(src) or {}
        was_live = is_live(src)
        dst = os.path.join(TRASH, "%s_%d.taf" % (lk.replace("/", "_"), int(time.time())))
        shutil.copyfile(src, dst)
        if was_live:
            set_live(src, False)
        os.remove(src)
        folder = os.path.dirname(src)
        try:
            rest = os.listdir(folder)
            if all(n.startswith(".") for n in rest):
                for n in rest:
                    os.remove(os.path.join(folder, n))
                os.rmdir(folder)
        except OSError:
            pass
        e["geloescht"] = {"datei": dst, "hash": h.get("hash"), "zeit": int(time.time()), "live": was_live}
        save_lib(lib)

def undelete(card, key):
    lk = key.upper()
    dst = content_file(card, lk)
    with LOCK:
        lib = load_lib(); e = lib.get(lk) or {}
        g = e.get("geloescht")
        if not g or not os.path.isfile(fix_path(g.get("datei", ""))):
            raise ValueError("Keine Sicherung dieser Figur gefunden.")
        if os.path.exists(dst):
            raise ValueError("Auf der Karte liegt bereits ein Inhalt für diese Figur.")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        gd = fix_path(g["datei"])
        write_to_card(gd, dst)
        if g.get("live"):
            set_live(dst, True)
        del e["geloescht"]
        save_lib(lib)
        try:
            os.remove(gd)
        except OSError:
            pass

def deleted_list(card):
    res = []
    for lk, e in load_lib().items():
        g = e.get("geloescht")
        if not g or not os.path.isfile(fix_path(g.get("datei", ""))):
            continue
        try:
            if os.path.isfile(content_file(card, lk)):
                continue
        except ValueError:
            continue
        fig = e.get("figur") or {}
        cust = e.get("eigen") or {}
        res.append({"key": lk, "name": fig.get("name") or "Unbekannte Figur", "bild": fig.get("bild", ""),
                    "inhalt": (cust.get("name") or "Eigene Titel") if cust.get("hash") == g.get("hash") else "Original"})
    return res

def transfer(card, von, nach):
    src = content_file(card, von); dst = content_file(card, nach)
    lks, lkd = von.upper(), nach.upper()
    if lks == lkd:
        raise ValueError("Quelle und Ziel sind dieselbe Figur.")
    if not os.path.isfile(src) or not os.path.isfile(dst):
        raise ValueError("Figur nicht auf der Karte gefunden.")
    hs = read_header(src)
    if not hs:
        raise ValueError("Der Inhalt der Quellfigur ist nicht lesbar.")
    with LOCK:
        lib = load_lib()
        es = lib.get(lks, {}); ed = lib.setdefault(lkd, {})
        if is_system(lks, es) or is_system(lkd, ed):
            raise ValueError("Systemklänge können nicht übertragen werden.")
        backup_current(dst, lkd, ed)
        keep = os.path.join(CUSTOM, lkd.replace("/", "_") + ".taf")
        shutil.copyfile(src, keep)
        write_to_card(keep, dst)
        cust = es.get("eigen") or {}
        if cust.get("hash") == hs["hash"]:
            name, titel = cust.get("name", ""), cust.get("titel", [])
        else:
            fig = es.get("figur") or {}
            name = ("Von: " + fig["name"]) if fig.get("name") else "Übertragen"
            titel = fig.get("titel", [])
        ed["eigen"] = {"hash": hs["hash"], "audio_id": hs["audio_id"], "datei": keep, "name": name, "titel": titel}
        ed["aktiv"] = "eigen"
        save_lib(lib)

# ---------- Figur zuordnen ----------
def search_tonies(q):
    if not TONIE_LIST:
        raise ValueError("Die Figurenliste ist noch nicht geladen. Internetverbindung prüfen und TonieTausch neu starten.")
    words = [w for w in re.split(r"\s+", (q or "").lower()) if w]
    if not words:
        return []
    res, seen = [], set()
    for t in TONIE_LIST:
        hay = " ".join(str(t.get(k) or "") for k in ("title", "series", "episodes", "model")).lower()
        if not all(w in hay for w in words):
            continue
        pic = t.get("pic") or ""
        ident = (t.get("title"), pic)
        if ident in seen:
            continue
        seen.add(ident)
        ep = t.get("episodes")
        res.append({"name": (ep if ep and ep != "None" else "") or t.get("title") or "",
                    "serie": t.get("series") or "", "bild": pic,
                    "titel": t.get("tracks") or [], "sprache": t.get("language") or ""})
    res.sort(key=lambda x: (not x["sprache"].lower().startswith("de"), not x["bild"], x["serie"], x["name"]))
    return res[:60]

def assign_figure(key, fig):
    lk = (key or "").upper()
    parts = lk.split("/")
    if len(parts) != 2 or not all(HEX8.match(x) for x in parts):
        raise ValueError("Ungültige Figur")
    bild = str(fig.get("bild") or "")
    if bild and not bild.startswith(("http://", "https://")):
        bild = ""
    with LOCK:
        lib = load_lib(); e = lib.setdefault(lk, {})
        e["figur"] = {"name": str(fig.get("name") or "")[:200], "serie": str(fig.get("serie") or "")[:200],
                      "bild": bild[:500], "titel": [str(x) for x in (fig.get("titel") or [])][:200],
                      "manuell": True}
        save_lib(lib)

# ---------- Kartensicherung ----------
def copy_tree(src, dst):
    count = size = 0
    for root, dirs, fs in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("._"))
        rel = os.path.relpath(root, src)
        td = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(td, exist_ok=True)
        for f in sorted(fs):
            if f in SKIP or f.startswith("._"):
                continue
            s = os.path.join(root, f)
            shutil.copyfile(s, os.path.join(td, f))
            count += 1
            size += os.path.getsize(s)
    return count, size

def backup_card(card):
    lab = re.sub(r"[^A-Za-z0-9_-]", "_", card_label(card))
    name = "%s_%s" % (lab, time.strftime("%Y-%m-%d_%H-%M-%S"))
    dst = os.path.join(CARDBAK, name)
    tmp = dst + ".unfertig"
    shutil.rmtree(tmp, ignore_errors=True)
    count, size = copy_tree(card, os.path.join(tmp, "inhalt"))
    with open(os.path.join(tmp, "info.json"), "w", encoding="utf-8") as f:
        json.dump({"karte": card_label(card), "datum": time.strftime("%d.%m.%Y %H:%M"),
                   "dateien": count, "groesse": size}, f, ensure_ascii=False)
    os.replace(tmp, dst)
    return {"ok": True, "name": name, "dateien": count, "groesse": size}

def card_backups():
    res = []
    for n in sorted(os.listdir(CARDBAK), reverse=True):
        p = os.path.join(CARDBAK, n, "info.json")
        if n.endswith(".unfertig") or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                i = json.load(f)
        except Exception:
            continue
        i["name"] = n
        res.append(i)
    return res

# ---------- Plattform: Laufwerke & Formatieren ----------
def du(args):
    r = subprocess.run(["/usr/sbin/diskutil"] + args, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).decode("utf-8", "replace").strip() or "diskutil-Fehler")
    return plistlib.loads(r.stdout)

def partitions_mac(dev):
    try:
        e = (du(["list", "-plist", dev]).get("AllDisksAndPartitions") or [{}])[0]
        return e.get("Partitions") or [e]
    except Exception:
        return []

def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", **NOWIN)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-800:] or "PowerShell-Fehler")
    return r.stdout

PS_DRIVES = r"""
$r = @(Get-CimInstance Win32_DiskDrive | Where-Object { $_.MediaType -like '*Removable*' -or $_.InterfaceType -eq 'USB' } | ForEach-Object {
  $d = $_
  $vols = @(Get-CimAssociatedInstance -InputObject $d -ResultClassName Win32_DiskPartition | ForEach-Object {
      Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_LogicalDisk } |
    ForEach-Object { ($_.DeviceID + ' ' + $_.VolumeName).Trim() })
  [pscustomobject]@{ index = $d.Index; name = $d.Model; size = [int64]$d.Size; volumes = $vols }
})
ConvertTo-Json -InputObject $r -Compress -Depth 3
"""

PS_FORMAT = r"""
$ErrorActionPreference = 'Stop'
try {
  try { Clear-Disk -Number __N__ -RemoveData -RemoveOEM -Confirm:$false } catch {}
  Initialize-Disk -Number __N__ -PartitionStyle MBR
  $p = New-Partition -DiskNumber __N__ -UseMaximumSize -AssignDriveLetter
  Start-Sleep -Seconds 2
  Format-Volume -Partition $p -FileSystem FAT32 -NewFileSystemLabel 'TONIEBOX' -Confirm:$false -Force | Out-Null
  $p = Get-Partition -DiskNumber __N__ -PartitionNumber $p.PartitionNumber
  Set-Content -LiteralPath '__RES__' -Value ('OK ' + $p.DriveLetter) -Encoding ASCII
} catch {
  Set-Content -LiteralPath '__RES__' -Value ('ERR ' + $_.Exception.Message) -Encoding ASCII
}
"""

def drives():
    res = []
    if IS_WIN:
        sysdrv = os.environ.get("SystemDrive", "C:").upper()
        for d in json.loads(ps(PS_DRIVES) or "[]"):
            vols = d.get("volumes") or []
            if not d.get("size") or any(v.upper().startswith(sysdrv) for v in vols):
                continue
            res.append({"dev": str(d["index"]), "name": d.get("name") or "Laufwerk %s" % d["index"],
                        "groesse": d["size"], "volumes": vols})
        return res
    try:
        boot = du(["info", "-plist", "/"]).get("ParentWholeDisk", "")
    except Exception:
        boot = ""
    for dev in du(["list", "-plist", "physical"]).get("WholeDisks", []):
        if dev == boot:
            continue
        try:
            i = du(["info", "-plist", dev])
        except Exception:
            continue
        if not (i.get("RemovableMedia") or i.get("Removable") or i.get("Internal") is False):
            continue
        vols = [p["VolumeName"] for p in partitions_mac(dev) if p.get("VolumeName")]
        res.append({"dev": dev, "name": i.get("MediaName") or i.get("IORegistryEntryName") or dev,
                    "groesse": i.get("TotalSize") or i.get("Size") or 0, "volumes": vols})
    return res

def format_mac(dev):
    if not re.fullmatch(r"disk\d+", dev):
        raise ValueError("Ungültiges Laufwerk.")
    r = subprocess.run(["/usr/sbin/diskutil", "eraseDisk", "FAT32", "TONIEBOX", "MBRFormat", "/dev/" + dev],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Formatieren fehlgeschlagen:\n" + (r.stderr or r.stdout)[-800:])
    for _ in range(30):
        mount = next((p["MountPoint"] for p in partitions_mac(dev) if p.get("MountPoint")), "")
        if mount:
            return mount
        time.sleep(1)
    raise RuntimeError("Die neue Karte wurde nicht eingebunden. Bitte neu einstecken.")

def format_win(drv):
    if drv["groesse"] > FAT32_MAX:
        raise ValueError("Windows kann Karten über 32 GB nicht als FAT32 formatieren. Bitte am Mac vorbereiten.")
    tmpd = tempfile.mkdtemp()
    res = os.path.join(tmpd, "ergebnis.txt")
    scr = os.path.join(tmpd, "format.ps1")
    with open(scr, "w", encoding="utf-8-sig") as f:
        f.write(PS_FORMAT.replace("__N__", str(int(drv["dev"]))).replace("__RES__", res.replace("'", "''")))
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "
                    "'-NoProfile','-ExecutionPolicy','Bypass','-File','\"%s\"'" % scr.replace("'", "''")], **NOWIN)
    out = ""
    if os.path.isfile(res):
        with open(res, encoding="ascii", errors="replace") as f:
            out = f.read().strip()
    shutil.rmtree(tmpd, ignore_errors=True)
    if not out.startswith("OK ") or len(out) < 4:
        raise RuntimeError("Formatieren fehlgeschlagen oder abgebrochen. " + out)
    mount = out[3] + ":\\"
    for _ in range(30):
        if os.path.isdir(mount):
            return mount
        time.sleep(1)
    raise RuntimeError("Die neue Karte wurde nicht eingebunden. Bitte neu einstecken.")

def prepare_card(sicherung, dev):
    name = os.path.basename(sicherung or "")
    src = os.path.join(CARDBAK, name, "inhalt")
    if not name or not os.path.isdir(src):
        raise ValueError("Sicherung nicht gefunden.")
    drv = next((d for d in drives() if d["dev"] == str(dev)), None)
    if not drv:
        raise ValueError("Dieses Laufwerk darf nicht formatiert werden.")
    mount = format_win(drv) if IS_WIN else format_mac(drv["dev"])
    count, size = copy_tree(src, mount)
    if not IS_WIN:
        subprocess.run(["/bin/sync"])
    return {"ok": True, "karte": mount, "dateien": count}

# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_file_range(self, path, ctype, offset=0):
        size = os.path.getsize(path) - offset
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(offset + start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                left -= len(chunk)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
                    return self.send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/ping":
                return self.send(200, {"ok": True})
            if u.path == "/api/karten":
                return self.send(200, {"karten": cards()})
            if u.path == "/api/figuren":
                k = check_card(q.get("karte"))
                return self.send(200, {"figuren": scan(k), "geloescht": deleted_list(k)})
            if u.path == "/api/export.csv":
                data = export_csv(check_card(q.get("karte")))
                name = "tonie-liste-%s.csv" % time.strftime("%Y-%m-%d")
                return self.send(200, data, "text/csv; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="%s"' % name})
            if u.path == "/api/suche":
                return self.send(200, {"treffer": search_tonies(q.get("q"))})
            if u.path == "/api/sicherungen":
                return self.send(200, {"sicherungen": card_backups()})
            if u.path == "/api/laufwerke":
                return self.send(200, {"laufwerke": drives()})
            if u.path == "/api/audio":
                p = content_file(check_card(q.get("karte")), q.get("key"))
                with open(p, "rb") as fh:
                    n = int.from_bytes(fh.read(4), "big")
                return self.send_file_range(p, "audio/ogg", 4 + n)
            self.send(404, {"fehler": "Nicht gefunden"})
        except Exception as e:
            self.send(500, {"fehler": str(e)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if u.path == "/api/upload":
                d = os.path.join(WORK, safe_id(q.get("s")))
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, safe_name(q.get("n"))), "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk); left -= len(chunk)
                return self.send(200, {"ok": True})
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/beenden":
                self.send(200, {"ok": True})
                threading.Thread(target=SERVER.shutdown, daemon=True).start()
                return
            if u.path == "/api/zuordnen":
                assign_figure(body.get("key"), body.get("figur") or {})
                return self.send(200, {"ok": True})
            if u.path == "/api/vorbereiten":
                return self.send(200, prepare_card(body.get("sicherung"), body.get("laufwerk")))
            if u.path == "/api/abspielen":
                return self.send(200, play_info(check_card(body.get("karte")), body.get("key")))
            if u.path == "/api/karte-sichern":
                return self.send(200, backup_card(check_card(body.get("karte"))))
            karte = check_card(body.get("karte"))
            if u.path == "/api/aufspielen":
                apply(karte, body.get("key"), safe_id(body.get("session")),
                      body.get("reihenfolge") or [], (body.get("name") or "").strip())
            elif u.path == "/api/original-zurueck":
                restore_original(karte, body.get("key"))
            elif u.path == "/api/eigene-zurueck":
                restore_custom(karte, body.get("key"))
            elif u.path == "/api/alle-zurueck":
                restore_all(karte)
            elif u.path == "/api/loeschen":
                delete_figure(karte, body.get("key"))
            elif u.path == "/api/geloescht-zurueck":
                undelete(karte, body.get("key"))
            elif u.path == "/api/uebertragen":
                transfer(karte, body.get("von"), body.get("nach"))
            elif u.path == "/api/live":
                p = content_file(karte, body.get("key"))
                if not os.path.isfile(p):
                    raise ValueError("Figur nicht auf der Karte gefunden.")
                set_live(p, bool(body.get("an")))
            elif u.path == "/api/alle-unhide":
                for f in scan(karte):
                    if f["live"]:
                        set_live(content_file(karte, f["key"]), False)
            else:
                return self.send(404, {"fehler": "Nicht gefunden"})
            self.send(200, {"ok": True})
        except Exception as e:
            self.send(500, {"fehler": str(e)})

if __name__ == "__main__":
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    shutil.rmtree(os.path.join(DATA, "vorschau"), ignore_errors=True)
    read_tonies()
    threading.Thread(target=refresh_tonies, daemon=True).start()
    SERVER = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print("TonieTausch läuft auf http://127.0.0.1:%d" % PORT, flush=True)
    SERVER.serve_forever()
