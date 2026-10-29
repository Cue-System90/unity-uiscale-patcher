"""Weg 2: serialisierte CanvasScaler-Komponenten in den Szenen- und Asset-Dateien ändern.

Funktioniert für Mono und IL2CPP, aber nur, solange kein Skript die Skalierung
zur Laufzeit überschreibt. Das Layout der Komponente ist seit Unity 5 stabil:

    m_UiScaleMode          int    0 ConstantPixelSize, 1 ScaleWithScreenSize, 2 ConstantPhysicalSize
    m_ReferencePixelsPerUnit float
    m_ScaleFactor          float
    m_ReferenceResolution  float x2
    m_ScreenMatchMode      int
    m_MatchWidthOrHeight   float
    m_PhysicalUnit         int
    m_FallbackScreenDPI    float
    m_DefaultSpriteDPI     float
    m_DynamicPixelsPerUnit float
    m_PresetInfoIsWorld    bool   (ab Unity 2019)
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from .backup import atomic_write, ensure_backup

FIELDS = "<iffffififff"          # 11 Felder, 44 Byte
FIELDS_SIZE = struct.calcsize(FIELDS)
MODE_NAMES = {0: "ConstantPixelSize", 1: "ScaleWithScreenSize", 2: "ConstantPhysicalSize"}
RENDER_MODES = {0: "ScreenSpaceOverlay", 1: "ScreenSpaceCamera", 2: "WorldSpace"}
UNIT_DPI = {0: 2.54, 1: 25.4, 2: 1.0, 3: 72.0, 4: 6.0}   # cm, mm, inch, points, picas


@dataclass
class ScalerAsset:
    file: str
    path_id: int
    byte_start: int
    size: int
    field_off: int
    mode: int
    ref_ppu: float
    scale: float
    ref_res: tuple[float, float]
    match_mode: int
    match: float
    unit: int
    fallback_dpi: float
    sprite_dpi: float
    dyn_ppu: float
    go_name: str = ""
    render_mode: int | None = None

    @property
    def world_space(self) -> bool:
        return self.render_mode == 2

    def describe(self) -> str:
        mode = MODE_NAMES.get(self.mode, str(self.mode))
        if self.mode == 0:
            val = f"scaleFactor={self.scale:g}"
        elif self.mode == 1:
            val = f"referenceResolution={self.ref_res[0]:g}x{self.ref_res[1]:g} match={self.match:g}"
        else:
            val = f"unit={self.unit} fallbackDPI={self.fallback_dpi:g}"
        rm = RENDER_MODES.get(self.render_mode, "?") if self.render_mode is not None else "?"
        return f"{os.path.basename(self.file)} #{self.path_id} '{self.go_name}' {mode} {val} canvas={rm}"


def _script_map(env) -> dict[tuple[str, int], str]:
    scripts = {}
    for obj in env.objects:
        if obj.type.name == "MonoScript":
            try:
                d = obj.read()
                scripts[(os.path.basename(obj.assets_file.name), obj.path_id)] = d.m_ClassName
            except Exception:
                pass
    return scripts


def _resolve_file(obj, fid: int) -> str:
    if fid == 0:
        return os.path.basename(obj.assets_file.name)
    try:
        return os.path.basename(obj.assets_file.externals[fid - 1].path)
    except Exception:
        return "?"


def _find_object(env, obj, fid: int, pid: int):
    fname = _resolve_file(obj, fid)
    for name, sf in env.files.items():
        if os.path.basename(name) == fname:
            return getattr(sf, "objects", {}).get(pid)
    return None


def _canvas_info(env, obj, go_fid: int, go_pid: int) -> tuple[str, int | None]:
    """Name des GameObjects und renderMode seines Canvas, best effort."""
    try:
        target = _find_object(env, obj, go_fid, go_pid)
        if target is None or target.type.name != "GameObject":
            return "", None
        go = target.read()
        name = getattr(go, "m_Name", "") or ""
        for comp in getattr(go, "m_Component", []) or []:
            ptr = getattr(comp, "component", None)
            if ptr is None and isinstance(comp, (list, tuple)) and len(comp) == 2:
                ptr = comp[1]
            if ptr is None:
                continue
            try:
                reader = ptr.deref()
            except Exception:
                continue
            if reader.type.name == "Canvas":
                return name, int(getattr(reader.read(), "m_RenderMode", 0))
        return name, None
    except Exception:
        return "", None


def scan_assets(files: list[str], progress=None) -> list[ScalerAsset]:
    if not files:
        return []
    import UnityPy
    if progress:
        progress(f"  lese {len(files)} Asset-Dateien …")
    env = UnityPy.load(*files)
    scripts = _script_map(env)
    out: list[ScalerAsset] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        raw = obj.get_raw_data()
        if len(raw) < 32 + FIELDS_SIZE:
            continue
        fid, pid = struct.unpack_from("<iq", raw, 16)
        if scripts.get((_resolve_file(obj, fid), pid)) != "CanvasScaler":
            continue
        nlen = struct.unpack_from("<i", raw, 28)[0]
        if nlen < 0 or nlen > 256:
            continue
        off = 32 + ((nlen + 3) // 4) * 4
        if len(raw) < off + FIELDS_SIZE:
            continue
        mode, rppu, scale, rx, ry, smm, mwh, unit, fdpi, sdpi, dppu = struct.unpack_from(FIELDS, raw, off)
        if mode not in MODE_NAMES or unit not in UNIT_DPI or not (0 < rppu < 10000):
            continue   # anderes Layout, lieber nicht anfassen
        go_fid, go_pid = struct.unpack_from("<iq", raw, 0)
        name, rm = _canvas_info(env, obj, go_fid, go_pid)
        out.append(ScalerAsset(obj.assets_file.name if os.path.isabs(obj.assets_file.name) else _abs_path(files, obj.assets_file.name),
                               obj.path_id, obj.byte_start, obj.byte_size, off, mode, rppu, scale, (rx, ry),
                               smm, mwh, unit, fdpi, sdpi, dppu, name, rm))
    return out


def _abs_path(files: list[str], name: str) -> str:
    base = os.path.basename(name)
    for f in files:
        if os.path.basename(f) == base:
            return f
    return name


def apply_factor(assets: list[ScalerAsset], factor: float, manifest: dict | None = None,
                 include_world: bool = False, dry: bool = False) -> list[str]:
    report: list[str] = []
    by_file: dict[str, list[ScalerAsset]] = {}
    for a in assets:
        by_file.setdefault(a.file, []).append(a)
    for path, items in by_file.items():
        orig = ensure_backup(path, manifest)
        data = bytearray(open(orig, "rb").read())
        for a in items:
            if a.world_space and not include_world:
                report.append(f"übersprungen (World-Space-Canvas): {a.describe()}")
                continue
            base = a.byte_start + a.field_off
            # Ausgangswerte aus dem Original lesen: der Scan lief womöglich über schon gepatchte Dateien
            mode, _, scale, rx, ry, _, _, unit, fdpi, _, _ = struct.unpack_from(FIELDS, data, base)
            if mode not in MODE_NAMES or unit not in UNIT_DPI:
                report.append(f"übersprungen (Original hat anderes Layout): {a.describe()}")
                continue
            if mode == 0:
                struct.pack_into("<f", data, base + 8, scale * factor)
                report.append(f"{a.describe()}  -> scaleFactor={scale * factor:g}")
            elif mode == 1:
                struct.pack_into("<ff", data, base + 12, rx / factor, ry / factor)
                report.append(f"{a.describe()}  -> referenceResolution={rx / factor:g}x{ry / factor:g}")
            else:
                # Physische Größe hängt von der Bildschirm-DPI ab; wir rechnen mit der Fallback-DPI fest um
                new_scale = factor * fdpi / UNIT_DPI[unit]
                struct.pack_into("<i", data, base, 0)
                struct.pack_into("<f", data, base + 8, new_scale)
                report.append(f"{a.describe()}  -> ConstantPixelSize scaleFactor={new_scale:g} (aus Fallback-DPI)")
        if not dry:
            atomic_write(path, bytes(data))
            report.append(f"geschrieben: {path}")
    return report
