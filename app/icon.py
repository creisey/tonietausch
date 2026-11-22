#!/usr/bin/env python3
# TonieTausch – App-Icon erzeugen (.icns für macOS, .ico für Windows)
import os, sys, shutil, subprocess, tempfile
from PIL import Image, ImageDraw

K = 2  # Supersampling für glatte Kanten

def r(*v):
    return [x * K for x in v]

def draw():
    img = Image.new("RGBA", (1024 * K, 1024 * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rot, weiss = (210, 55, 44, 255), (255, 255, 255, 255)
    d.rounded_rectangle(r(100, 100, 924, 924), radius=185 * K, fill=rot)
    d.ellipse(r(300, 230, 520, 450), fill=weiss)
    d.rounded_rectangle(r(290, 470, 530, 720), radius=90 * K, fill=weiss)
    d.rounded_rectangle(r(245, 700, 575, 780), radius=40 * K, fill=weiss)
    d.ellipse(r(600, 600, 722, 690), fill=weiss)
    d.rectangle(r(692, 330, 722, 650), fill=weiss)
    d.polygon([tuple(r(722, 330)), tuple(r(805, 385)), tuple(r(805, 445)), tuple(r(722, 395))], fill=weiss)
    return img.resize((1024, 1024), Image.LANCZOS)

def main(out):
    big = draw()
    if out.lower().endswith(".ico"):
        big.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return
    tmp = tempfile.mkdtemp()
    iset = os.path.join(tmp, "AppIcon.iconset")
    os.makedirs(iset)
    for s in (16, 32, 128, 256, 512):
        big.resize((s, s), Image.LANCZOS).save(os.path.join(iset, "icon_%dx%d.png" % (s, s)))
        big.resize((s * 2, s * 2), Image.LANCZOS).save(os.path.join(iset, "icon_%dx%d@2x.png" % (s, s)))
    subprocess.run(["/usr/bin/iconutil", "-c", "icns", iset, "-o", out], check=True)
    shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main(sys.argv[1])
