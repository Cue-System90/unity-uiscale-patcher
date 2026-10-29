"""Erkennung eines Unity-Spiels: Plattform, Scripting-Backend, Unity-Version, Dateilayout."""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass

# 5.6.7f1, 2019.4.22f1c1, 2021.3.45f2, 6000.3.23f1
UNITY_VERSION_RE = re.compile(rb"(?<![\d.])(\d{1,4}\.\d{1,2}\.\d{1,3}[abfpx]\d{1,3}(?:c\d)?)(?![\d.])")
SERIALIZED_RE = re.compile(r"^(level\d+|sharedassets\d+\.assets|resources\.assets|globalgamemanagers(\.assets)?)$")
# Assemblies, die sicher nicht zum Spielcode gehören
FOREIGN_DLL_RE = re.compile(
    r"^(Unity|UnityEngine|UnityEditor|System|Mono\.|mscorlib|netstandard|Microsoft\.|nunit|Newtonsoft|"
    r"0Harmony|BepInEx|MonoMod|Assembly-CSharp-Editor|com\.unity|I18N|Accessibility|Boo\.|UnityScript)",
    re.I,
)


@dataclass
class GameInfo:
    root: str
    data_dir: str
    name: str
    platform: str = "unknown"      # windows | linux | macos
    arch: str = "x64"              # x64 | x86
    backend: str = "unknown"       # mono | il2cpp
    unity_version: str = ""
    managed_dir: str | None = None
    bundled_data: bool = False     # data.unity3d statt loser Serialized Files
    bepinex: bool = False
    proton_hint: bool = False      # Windows-Build auf einem Linux-System: läuft vermutlich unter Proton

    @property
    def unity_major(self) -> int:
        m = re.match(r"(\d+)", self.unity_version)
        return int(m.group(1)) if m else 0

    @property
    def exe_name(self) -> str:
        return self.name + (".exe" if self.platform == "windows" else "")

    def serialized_files(self) -> list[str]:
        if not os.path.isdir(self.data_dir):
            return []
        out = [os.path.join(self.data_dir, f) for f in sorted(os.listdir(self.data_dir)) if SERIALIZED_RE.match(f)]
        return [f for f in out if os.path.isfile(f)]

    def game_assemblies(self) -> list[str]:
        if not self.managed_dir or not os.path.isdir(self.managed_dir):
            return []
        dlls = sorted(f for f in os.listdir(self.managed_dir) if f.lower().endswith(".dll") and not FOREIGN_DLL_RE.match(f))
        # Assembly-CSharp zuerst, dort steckt fast immer die Einstellungslogik
        dlls.sort(key=lambda f: (not f.startswith("Assembly-CSharp"), f.lower()))
        return [os.path.join(self.managed_dir, f) for f in dlls]

    def summary(self) -> str:
        be = {"mono": "Mono", "il2cpp": "IL2CPP"}.get(self.backend, "unbekannt")
        plat = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}.get(self.platform, "unbekannt")
        lines = [
            f"Spiel:          {self.name}",
            f"Ordner:         {self.root}",
            f"Unity:          {self.unity_version or 'unbekannt'}",
            f"Backend:        {be}",
            f"Plattform:      {plat} {self.arch}" + ("  (läuft hier vermutlich unter Proton/Wine)" if self.proton_hint else ""),
            f"Datenlayout:    {'gebündelt (data.unity3d)' if self.bundled_data else 'lose Serialized Files'}",
            f"BepInEx:        {'vorhanden' if self.bepinex else 'nicht installiert'}",
        ]
        return "\n".join(lines)


def _pe_machine(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(0x400)
        if head[:2] != b"MZ":
            return None
        e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
        with open(path, "rb") as f:
            f.seek(e_lfanew)
            sig = f.read(6)
        if sig[:4] != b"PE\0\0":
            return None
        machine = struct.unpack_from("<H", sig, 4)[0]
        return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(machine)
    except OSError:
        return None


def _elf_class(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.read(5)
        if head[:4] != b"\x7fELF":
            return None
        return {1: "x86", 2: "x64"}.get(head[4])
    except OSError:
        return None


def _find_version(paths: list[str]) -> str:
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "rb") as f:
                head = f.read(65536)
        except OSError:
            continue
        m = UNITY_VERSION_RE.search(head)
        if m:
            return m.group(1).decode()
    return ""


def find_game(path: str) -> GameInfo:
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Kein Ordner: {path}")

    data_dir = None
    root = path
    if path.endswith("_Data") and os.path.isdir(path):
        data_dir, root = path, os.path.dirname(path)
    else:
        cands = [os.path.join(path, d) for d in os.listdir(path) if d.endswith("_Data") and os.path.isdir(os.path.join(path, d))]
        if not cands:
            # macOS-Bundle: Spiel.app/Contents/Resources/Data
            apps = [os.path.join(path, d) for d in os.listdir(path) if d.endswith(".app")]
            for app in apps:
                d = os.path.join(app, "Contents", "Resources", "Data")
                if os.path.isdir(d):
                    cands.append(d)
        if not cands:
            raise FileNotFoundError(f"Kein Unity-Datenordner (*_Data) in {path}")
        # bei mehreren Kandidaten den nehmen, zu dem eine Startdatei existiert
        def has_binary(d: str) -> bool:
            n = os.path.basename(d)[:-5]
            return any(os.path.exists(os.path.join(path, n + ext)) for ext in (".exe", ".x86_64", ".x86", ""))
        cands.sort(key=lambda d: (not has_binary(d), d))
        data_dir = cands[0]

    base = os.path.basename(data_dir)
    name = base[:-5] if base.endswith("_Data") else os.path.basename(os.path.dirname(os.path.dirname(data_dir)))[:-4]
    g = GameInfo(root=root, data_dir=data_dir, name=name)

    # Plattform und Architektur
    if os.path.exists(os.path.join(root, name + ".exe")) or os.path.exists(os.path.join(root, "UnityPlayer.dll")):
        g.platform = "windows"
        g.arch = _pe_machine(os.path.join(root, "UnityPlayer.dll")) or _pe_machine(os.path.join(root, name + ".exe")) or "x64"
    elif os.path.exists(os.path.join(root, name + ".x86_64")) or os.path.exists(os.path.join(root, "UnityPlayer.so")):
        g.platform, g.arch = "linux", "x64"
    elif os.path.exists(os.path.join(root, name + ".x86")):
        g.platform, g.arch = "linux", "x86"
    elif "/Contents/Resources/Data" in data_dir.replace("\\", "/"):
        g.platform = "macos"
    else:
        for f in os.listdir(root):
            p = os.path.join(root, f)
            if os.path.isfile(p) and not f.endswith((".dll", ".so", ".json", ".txt", ".ini")):
                cls = _elf_class(p)
                if cls:
                    g.platform, g.arch = "linux", cls
                    break
    g.proton_hint = g.platform == "windows" and os.name == "posix"

    # Backend
    managed = os.path.join(data_dir, "Managed")
    if os.path.isfile(os.path.join(managed, "Assembly-CSharp.dll")):
        g.backend, g.managed_dir = "mono", managed
    elif any(os.path.exists(os.path.join(root, f)) for f in ("GameAssembly.dll", "GameAssembly.so", "GameAssembly.dylib")) \
            or os.path.isdir(os.path.join(data_dir, "il2cpp_data")):
        g.backend = "il2cpp"
    elif os.path.isdir(managed):
        g.backend, g.managed_dir = "mono", managed

    # Unity-Version und Datenlayout
    g.unity_version = _find_version([
        os.path.join(data_dir, "globalgamemanagers"),
        os.path.join(data_dir, "data.unity3d"),
        os.path.join(data_dir, "level0"),
        os.path.join(data_dir, "mainData"),
        os.path.join(data_dir, "resources.assets"),
    ])
    g.bundled_data = os.path.isfile(os.path.join(data_dir, "data.unity3d")) and not os.path.isfile(os.path.join(data_dir, "globalgamemanagers"))
    g.bepinex = os.path.isdir(os.path.join(root, "BepInEx"))
    return g
