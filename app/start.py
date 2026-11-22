#!/usr/bin/env python3
# TonieTausch – Starter: Server starten (falls nötig) und Browser öffnen
import os, sys, time, subprocess, urllib.request, webbrowser

APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
URL = "http://127.0.0.1:8765"
IS_WIN = os.name == "nt"

def alive():
    try:
        urllib.request.urlopen(URL + "/api/ping", timeout=1)
        return True
    except Exception:
        return False

def open_browser():
    if sys.platform == "darwin" and os.path.isdir("/Applications/Google Chrome.app"):
        subprocess.run(["open", "-a", "Google Chrome", URL])
        return
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            exe = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if base and os.path.isfile(exe):
                subprocess.Popen([exe, URL])
                return
    webbrowser.open(URL)

if not alive():
    log = open(os.path.join(ROOT, "server.log"), "a", encoding="utf-8")
    kw = {"creationflags": 0x00000008 | 0x00000200} if IS_WIN else {"start_new_session": True}
    subprocess.Popen([sys.executable, os.path.join(APP, "server.py")], cwd=ROOT,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kw)
    for _ in range(50):
        if alive():
            break
        time.sleep(0.2)
open_browser()
