"""Weg 3: BepInEx und das Plugin UIScaleMultiplier installieren.

Das Plugin hängt sich per Harmony in CanvasScaler.SetScaleFactor() und
multipliziert jeden Faktor mit einem konfigurierbaren Wert. Es braucht kein
Wissen über das Spiel und funktioniert für Mono (BepInEx 5) und IL2CPP
(BepInEx 6, Vorabversion).
"""
from __future__ import annotations

import io
import json
import os
import re
import stat
import urllib.request
import zipfile

from .backup import atomic_write
from .game import GameInfo

BEPINEX_REPO = "BepInEx/BepInEx"
PLUGIN_REPO = "Cue-System90/unity-uiscale-patcher"
PLUGIN_GUID = "de.cuesystem.uiscale"
PLUGIN_DIR = os.path.join("BepInEx", "plugins", "UIScaleMultiplier")
CONFIG_REL = os.path.join("BepInEx", "config", PLUGIN_GUID + ".cfg")
USER_AGENT = "unity-uiscale-patcher"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _json(url: str):
    return json.loads(_get(url).decode("utf-8"))


def bepinex_release(game: GameInfo) -> tuple[str, str, str]:
    """(Download-URL, Dateiname, Version) des passenden BepInEx-Pakets."""
    plat = {"windows": "win", "linux": "linux", "macos": "macos"}.get(game.platform)
    if plat is None:
        raise RuntimeError("Plattform unbekannt, BepInEx-Paket nicht bestimmbar")
    if game.backend == "il2cpp":
        rels = _json(f"https://api.github.com/repos/{BEPINEX_REPO}/releases?per_page=30")
        pat = re.compile(rf"^BepInEx-Unity\.IL2CPP-{plat}-{game.arch}-.*\.zip$")
        for rel in rels:   # neueste zuerst; IL2CPP gibt es nur in den 6.x-Vorabversionen
            for a in rel.get("assets", []):
                if pat.match(a["name"]):
                    return a["browser_download_url"], a["name"], rel["tag_name"]
        raise RuntimeError(f"Kein BepInEx-IL2CPP-Paket für {plat}-{game.arch} gefunden")
    rel = _json(f"https://api.github.com/repos/{BEPINEX_REPO}/releases/latest")
    arch = "universal" if plat == "macos" else game.arch
    pat = re.compile(rf"^BepInEx_{plat}_{arch}_.*\.zip$")
    for a in rel.get("assets", []):
        if pat.match(a["name"]):
            return a["browser_download_url"], a["name"], rel["tag_name"]
    raise RuntimeError(f"Kein BepInEx-5-Paket für {plat}-{arch} gefunden")


def plugin_asset(game: GameInfo) -> str:
    if game.backend == "il2cpp":
        return "UIScaleMultiplier.IL2CPP.dll"
    if 0 < game.unity_major < 2018:        # Unity 5.x / 2017: .NET 3.5
        return "UIScaleMultiplier.net35.dll"
    return "UIScaleMultiplier.dll"


def plugin_url(game: GameInfo) -> str:
    return f"https://github.com/{PLUGIN_REPO}/releases/latest/download/{plugin_asset(game)}"


def _safe_extract(zf: zipfile.ZipFile, root: str) -> tuple[list[str], list[str]]:
    """Entpackt nach root und liefert (Dateien, Verzeichnisse) der obersten Ebene."""
    files, dirs = [], []
    root_abs = os.path.abspath(root)
    for info in zf.infolist():
        dest = os.path.abspath(os.path.join(root, info.filename))
        if not dest.startswith(root_abs + os.sep):
            raise RuntimeError(f"Unsicherer Pfad im Archiv: {info.filename}")
        top = info.filename.split("/")[0]
        if info.is_dir() or "/" in info.filename.rstrip("/"):
            if top and os.path.join(root, top) not in dirs:
                dirs.append(os.path.join(root, top))
        else:
            files.append(dest)
    zf.extractall(root)
    return files, dirs


def config_text(factor: float, existing: str | None = None) -> str:
    if existing:
        if re.search(r"^Multiplier\s*=", existing, re.M):
            return re.sub(r"^Multiplier\s*=.*$", f"Multiplier = {factor:g}", existing, count=1, flags=re.M)
        return existing.rstrip("\n") + f"\n\n[General]\nMultiplier = {factor:g}\n"
    return (
        f"## Settings file was created by plugin UI Scale Multiplier\n"
        f"## Plugin GUID: {PLUGIN_GUID}\n\n"
        "[General]\n\n"
        "## Faktor, mit dem jede uGUI-Skalierung multipliziert wird.\n"
        "# Setting type: Single\n# Default value: 1.5\n"
        f"Multiplier = {factor:g}\n\n"
        "## Canvas-Namen, die nicht skaliert werden (kommagetrennt).\n"
        "# Setting type: String\n# Default value: \n"
        "Exclude = \n\n"
        "## World-Space-Canvases unverändert lassen.\n"
        "# Setting type: Boolean\n# Default value: true\n"
        "SkipWorldSpace = true\n"
    )


def launch_hint(game: GameInfo) -> list[str]:
    if game.platform == "windows" and game.proton_hint:
        return ["Steam > Eigenschaften > Startoptionen:", '    WINEDLLOVERRIDES="winhttp=n,b" %command%']
    if game.platform in ("linux", "macos"):
        return ["Steam > Eigenschaften > Startoptionen:", "    ./run_bepinex.sh %command%",
                "(oder das Spiel direkt über run_bepinex.sh starten)"]
    return ["Unter Windows startet BepInEx automatisch über winhttp.dll."]


def install(game: GameInfo, factor: float, manifest: dict, plugin_dll: str | None = None,
            bepinex_zip: str | None = None, dry: bool = False, progress=print) -> list[str]:
    report: list[str] = []
    root = game.root

    # 1. BepInEx
    if game.bepinex:
        report.append("BepInEx ist bereits installiert, wird nicht angefasst")
    else:
        if bepinex_zip:
            data = open(bepinex_zip, "rb").read()
            name, tag = os.path.basename(bepinex_zip), "lokal"
        else:
            url, name, tag = bepinex_release(game)
            report.append(f"BepInEx {tag}: {name}")
            if dry:
                report.append(f"  würde laden: {url}")
                data = b""
            else:
                progress(f"  lade {name} …")
                data = _get(url)
        if not dry:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                files, dirs = _safe_extract(zf, root)
            for f in files:
                if f not in manifest["installed"]:
                    manifest["installed"].append(f)
            for d in dirs:
                if d not in manifest["installed_dirs"]:
                    manifest["installed_dirs"].append(d)
            sh = os.path.join(root, "run_bepinex.sh")
            if os.path.isfile(sh):
                os.chmod(sh, os.stat(sh).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            report.append(f"BepInEx entpackt nach {root}")

    # 2. Plugin-DLL
    asset = plugin_asset(game)
    dest = os.path.join(root, PLUGIN_DIR, asset)
    if plugin_dll:
        src_desc, data = plugin_dll, open(plugin_dll, "rb").read()
    else:
        src_desc = plugin_url(game)
        if dry:
            data = b""
        else:
            progress(f"  lade {asset} …")
            try:
                data = _get(src_desc)
            except Exception as e:
                raise RuntimeError(f"Plugin-DLL nicht ladbar ({e}). Alternativ mit --plugin-dll eine lokal gebaute DLL angeben.") from e
    if dry:
        report.append(f"Plugin: würde {asset} aus {src_desc} nach {dest} legen")
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        atomic_write(dest, data)
        if dest not in manifest["installed"]:
            manifest["installed"].append(dest)
        report.append(f"Plugin installiert: {dest}")

    # 3. Konfiguration
    cfg = os.path.join(root, CONFIG_REL)
    existing = open(cfg, encoding="utf-8").read() if os.path.isfile(cfg) else None
    if dry:
        report.append(f"Konfiguration: Multiplier = {factor:g} in {cfg}")
    else:
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        atomic_write(cfg, config_text(factor, existing).encode("utf-8"))
        if existing is None and cfg not in manifest["installed"]:
            manifest["installed"].append(cfg)
        report.append(f"Konfiguration geschrieben: Multiplier = {factor:g} ({cfg})")

    report += launch_hint(game)
    if game.backend == "il2cpp":
        report.append("Hinweis IL2CPP: BepInEx 6 erzeugt beim ersten Start die Interop-Assemblies, das dauert und braucht Internet.")
    report.append("Hotkeys im Spiel: Strg + Nummernblock-Plus/Minus ändern den Faktor (nur Mono).")
    return report
