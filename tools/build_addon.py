"""Erzeugt dist/liner_wandstaerke.zip zur Installation in Blender.

    python3 tools/build_addon.py
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "liner_wandstaerke"


def main():
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, PKG + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(os.path.join(ROOT, PKG))):
            if name.endswith(".py"):
                z.write(os.path.join(ROOT, PKG, name), arcname=PKG + "/" + name)
    print(out)


if __name__ == "__main__":
    main()
