"""Prepare static 400/700 serif faces for Writer (variable defaults may be Thin).

Usage: python scripts/setup-report-fonts.py --source <NotoSerifSC-VF.ttf>
       Add --install-user to make the same family available to local Word/HTML.
The original font's copyright and license records are retained in both faces.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

FAMILY = "Research Report Serif"


def main():
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"))
    parser.add_argument("--install-user", action="store_true")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error("Provide the installed Noto Serif SC variable font using --source.")
    root = Path(__file__).resolve().parents[1]
    output = root / ".runtime/report-tools/fonts"
    output.mkdir(parents=True, exist_ok=True)
    for weight, face in ((400, "Regular"), (700, "Bold")):
        target = output / f"ResearchReportSerif-{face}.ttf"
        if not target.is_file():
            original = TTFont(args.source)
            font = instantiateVariableFont(original, {"wght": weight}, inplace=True)
            # Rename the derivative, including typographic and Windows names.
            names = {1: FAMILY, 2: face, 3: f"{FAMILY}-{face}", 4: f"{FAMILY} {face}",
                     6: f"ResearchReportSerif-{face}", 16: FAMILY, 17: face}
            for name_id in names:
                font["name"].removeNames(nameID=name_id)
                font["name"].setName(names[name_id], name_id, 3, 1, 0x409)
            font["OS/2"].usWeightClass = weight
            font["OS/2"].fsSelection &= ~((1 << 5) | (1 << 6))
            font["OS/2"].fsSelection |= 1 << (5 if weight == 700 else 6)
            font["head"].macStyle = (font["head"].macStyle & ~1) | (1 if weight == 700 else 0)
            font.save(target)
            license_text = "\n".join(record.toUnicode() for record in font["name"].names if record.nameID in (0, 13, 14))
            (output / "FONT-LICENSE.txt").write_text(license_text, encoding="utf-8")
            font.close()
        # The dedicated service runtime discovers its own fonts on startup.
        runtime_fonts = root / ".runtime/libreoffice/share/fonts/truetype"
        if runtime_fonts.parent.exists():
            runtime_fonts.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, runtime_fonts / target.name)
        if args.install_user and os.name == "nt":
            import ctypes
            import winreg
            user_fonts = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts"
            user_fonts.mkdir(parents=True, exist_ok=True)
            installed = user_fonts / target.name
            shutil.copy2(target, installed)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                winreg.SetValueEx(key, f"{FAMILY} {face} (TrueType)", 0, winreg.REG_SZ, str(installed))
            ctypes.windll.gdi32.AddFontResourceExW(str(installed), 0, 0)
            ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 2, 1000, None)
        print(target)


if __name__ == "__main__":
    main()
