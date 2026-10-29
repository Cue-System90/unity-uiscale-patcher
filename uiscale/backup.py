"""Backups (*.orig), atomares Schreiben und das Manifest, das festhält, was der Patcher geändert hat."""
from __future__ import annotations

import json
import os
import shutil
import time

MANIFEST = ".uiscale-patcher.json"


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST)


def load_manifest(root: str) -> dict:
    p = manifest_path(root)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"backups": [], "installed": [], "installed_dirs": [], "history": []}


def save_manifest(root: str, data: dict) -> None:
    with open(manifest_path(root), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ensure_backup(path: str, manifest: dict | None = None) -> str:
    """Legt einmalig <datei>.orig an und liefert dessen Pfad. Weitere Läufe gehen immer vom Original aus."""
    orig = path + ".orig"
    if not os.path.exists(orig):
        shutil.copy2(path, orig)
    if manifest is not None and path not in manifest["backups"]:
        manifest["backups"].append(path)
    return orig


def atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def record(manifest: dict, method: str, factor: float, details: list[str]) -> None:
    manifest["history"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "method": method,
                                "factor": factor, "details": details})


def restore(root: str, data_dir: str) -> list[str]:
    """Spielt alle Backups zurück, entfernt installierte Dateien und das Manifest."""
    report: list[str] = []
    m = load_manifest(root)
    seen = set()
    for path in m.get("backups", []):
        seen.add(path)
        orig = path + ".orig"
        if os.path.exists(orig):
            shutil.copy2(orig, path)
            report.append(f"wiederhergestellt: {path}")
        else:
            report.append(f"kein Backup mehr vorhanden: {orig}")
    # auch verwaiste *.orig ohne Manifest-Eintrag berücksichtigen
    for d in (data_dir, os.path.join(data_dir, "Managed")):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".orig"):
                path = os.path.join(d, f[:-5])
                if path not in seen and os.path.exists(path):
                    shutil.copy2(os.path.join(d, f), path)
                    report.append(f"wiederhergestellt: {path}")
    for path in m.get("installed", []):
        if os.path.isfile(path):
            os.remove(path)
            report.append(f"entfernt: {path}")
    for d in m.get("installed_dirs", []):
        if os.path.isdir(d):
            shutil.rmtree(d)
            report.append(f"entfernt: {d}/")
    if os.path.isfile(manifest_path(root)):
        os.remove(manifest_path(root))
    return report
