"""Weg 1: statischer Patch der Mono-Assembly.

Findet Methoden, die den CanvasScaler ansteuern, bestimmt per Datenfluss die
Konstanten, die dort hineinfließen (die Wertetabelle der Skalierung), und
multipliziert sie. Beschriftungen wie "1.20" im Menü werden mit umbenannt,
wenn sie eindeutig sind und die neue Beschriftung gleich lang ist.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

import dnfile
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBase
from dncil.cil.error import MethodBodyFormatError
from dncil.clr.token import StringToken, Token

from .backup import atomic_write, ensure_backup

CANVAS_SCALER = "CanvasScaler"
# Setter -> wie ein größerer UI-Faktor auf den Wert wirkt
SETTER_EFFECT = {
    "set_scaleFactor": "mul",            # Faktor direkt
    "set_referenceResolution": "div",    # kleinere Referenzauflösung = größere UI
    "set_dynamicPixelsPerUnit": "mul",
}
INFO_SETTERS = ("set_matchWidthOrHeight", "set_referencePixelsPerUnit", "set_uiScaleMode")


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


class _FileOffsetReader(CilMethodBodyReaderBase):
    def __init__(self, pe, rva):
        self.pe = pe
        self.offset = pe.get_offset_from_rva(rva)

    def read(self, n):
        data = self.pe.get_data(self.pe.get_rva_from_offset(self.offset), n)
        self.offset += n
        return data

    def tell(self):
        return self.offset

    def seek(self, offset):
        self.offset = offset
        return offset


@dataclass
class Insn:
    offset: int
    opcode: str
    operand: object
    text: str

    def local_index(self) -> int | None:
        base, _, suffix = self.opcode.partition(".")
        if base not in ("ldloc", "stloc"):
            return None
        if suffix.isdigit():
            return int(suffix)
        return int(self.operand) if self.operand is not None else None


@dataclass
class Method:
    rid: int
    name: str
    row: object

    def __str__(self):
        return self.name


@dataclass
class Site:
    offset: int        # Dateioffset des 4-Byte-Werts
    value: float
    effect: str        # mul | div


@dataclass
class TableHit:
    dll: str
    method: str
    setters: list[str]
    sites: list[Site] = field(default_factory=list)
    note: str = ""
    path: str = ""

    @property
    def values(self) -> list[float]:
        return [s.value for s in self.sites]


class Assembly:
    def __init__(self, path: str):
        self.path = path
        self.data = bytearray(open(path, "rb").read())
        self.pe = dnfile.dnPE(data=bytes(self.data))
        self._methods: list[Method] | None = None

    def methods(self) -> list[Method]:
        if self._methods is None:
            md = self.pe.net.mdtables.MethodDef
            td = self.pe.net.mdtables.TypeDef
            if md is None or td is None:
                self._methods = []
                return self._methods
            starts = []
            for t in td.rows:
                try:
                    starts.append(t.MethodList[0].row_index if t.MethodList else None)
                except Exception:
                    starts.append(None)
            owner = {}
            n = len(md.rows)
            for i, t in enumerate(td.rows):
                s = starts[i]
                if s is None:
                    continue
                e = next((x for x in starts[i + 1:] if x is not None), n + 1)
                for r in range(s, e):
                    owner[r] = t.TypeName
            self._methods = [Method(i, f"{owner.get(i, '?')}::{row.Name}", row) for i, row in enumerate(md.rows, start=1)]
        return self._methods

    def find_method(self, name: str) -> Method:
        exact = [m for m in self.methods() if m.name == name]
        if len(exact) == 1:
            return exact[0]
        loose = [m for m in self.methods() if m.name.endswith("::" + name) or m.name.endswith(name)]
        if len(loose) == 1:
            return loose[0]
        if not loose:
            raise LookupError(f"Methode nicht gefunden: {name}")
        raise LookupError("Mehrdeutig: " + ", ".join(m.name for m in loose))

    def _resolve(self, tok: Token) -> str:
        if isinstance(tok, StringToken):
            try:
                us = self.pe.net.user_strings.get(tok.rid)
                return f'"{us.value}"' if us else "<str?>"
            except Exception:
                return "<str?>"
        table = self.pe.net.mdtables.tables.get(tok.table)
        if table is None:
            return f"<{tok.value:08x}>"
        try:
            row = table.rows[tok.rid - 1]
        except IndexError:
            return f"<{tok.value:08x}>"
        cls = type(row).__name__
        try:
            if cls == "MethodDefRow":
                return str(row.Name)
            if cls == "MemberRefRow":
                parent = row.Class.row if row.Class else None
                pn = getattr(parent, "TypeName", None) or getattr(parent, "Name", None) or "?"
                return f"{pn}::{row.Name}"
            if cls == "TypeRefRow":
                return f"{row.TypeNamespace}.{row.TypeName}"
            if cls == "TypeDefRow":
                return str(row.TypeName)
            if cls == "FieldRow":
                return str(row.Name)
            if cls == "MethodSpecRow":
                return f"{getattr(row.Method.row, 'Name', '?')}<>"
        except Exception:
            pass
        return cls

    def disasm(self, m: Method) -> list[Insn]:
        if not m.row.Rva:
            return []
        try:
            body = CilMethodBody(_FileOffsetReader(self.pe, m.row.Rva))
        except (MethodBodyFormatError, Exception):
            return []
        out = []
        for ins in body.instructions:
            op = ins.operand
            text = self._resolve(op) if isinstance(op, Token) else ("" if op is None else repr(op))
            out.append(Insn(ins.offset, ins.opcode.name, op, text))
        return out

    def float_consts(self, m: Method) -> list[tuple[int, float]]:
        return [(i.offset + 1, float(i.operand)) for i in self.disasm(m) if i.opcode == "ldc.r4"]

    def scaler_calls(self, m: Method, ins: list[Insn] | None = None) -> list[Insn]:
        ins = self.disasm(m) if ins is None else ins
        return [i for i in ins if i.opcode in ("call", "callvirt") and i.text.startswith(CANVAS_SCALER + "::")
                and i.text.split("::", 1)[1] in (*SETTER_EFFECT, *INFO_SETTERS)]

    def table_hit(self, m: Method) -> TableHit | None:
        """Wertetabelle einer Methode: die Konstanten, die in den CanvasScaler fließen.

        - ldc.r4 direkt vor dem Setter: dieser Wert
        - ldloc vor dem Setter: alle ldc.r4, die per stloc in dieselbe Variable gehen
        - newobj Vector2 vor set_referenceResolution: die beiden ldc.r4 davor
        """
        ins = self.disasm(m)
        calls = self.scaler_calls(m, ins)
        if not calls:
            return None
        pos = {i.offset: k for k, i in enumerate(ins)}
        hit = TableHit(dll=os.path.basename(self.path), method=m.name, path=self.path,
                       setters=sorted({c.text.split("::", 1)[1] for c in calls}))
        notes = []
        for c in calls:
            setter = c.text.split("::", 1)[1]
            effect = SETTER_EFFECT.get(setter)
            if effect is None:
                continue
            k = pos[c.offset]
            prev = ins[k - 1] if k > 0 else None
            if prev is None:
                continue
            if prev.opcode == "ldc.r4":
                hit.sites.append(Site(prev.offset + 1, float(prev.operand), effect))
            elif prev.local_index() is not None:
                n = prev.local_index()
                found = False
                for a, b in zip(ins, ins[1:]):
                    if a.opcode == "ldc.r4" and b.opcode.startswith("stloc") and b.local_index() == n:
                        hit.sites.append(Site(a.offset + 1, float(a.operand), effect))
                        found = True
                if not found:
                    notes.append(f"{setter}: Wert wird berechnet, keine Konstante")
            elif prev.opcode == "newobj" and "Vector2" in prev.text and k >= 3 \
                    and ins[k - 2].opcode == "ldc.r4" and ins[k - 3].opcode == "ldc.r4":
                hit.sites.append(Site(ins[k - 3].offset + 1, float(ins[k - 3].operand), effect))
                hit.sites.append(Site(ins[k - 2].offset + 1, float(ins[k - 2].operand), effect))
            else:
                notes.append(f"{setter}: Wert kommt aus {prev.opcode} {prev.text}".rstrip())
        seen = set()
        hit.sites = [s for s in hit.sites if not (s.offset in seen or seen.add(s.offset))]
        hit.note = "; ".join(dict.fromkeys(notes))
        return hit

    def scan(self) -> list[TableHit]:
        hits = []
        for m in self.methods():
            h = self.table_hit(m)
            if h:
                hits.append(h)
        return hits

    # ---- Schreiben ------------------------------------------------------
    def write_float(self, offset: int, value: float) -> None:
        struct.pack_into("<f", self.data, offset, value)

    def count_label(self, text: str) -> int:
        return self.data.count(text.encode("utf-16-le"))

    def rename_labels(self, mapping: dict[str, str]) -> list[str]:
        plan, report = [], []
        for old, new in mapping.items():
            if len(old) != len(new):
                report.append(f'Beschriftung "{old}" nicht umbenannt: "{new}" hat andere Länge')
                continue
            needle = old.encode("utf-16-le")
            if self.data.count(needle) != 1:
                report.append(f'Beschriftung "{old}" nicht umbenannt: nicht eindeutig')
                continue
            plan.append((self.data.find(needle), new.encode("utf-16-le"), old, new))
        for off, enc, old, new in plan:
            self.data[off:off + len(enc)] = enc
            report.append(f'Beschriftung "{old}" -> "{new}"')
        return report

    def save(self, out: str | None = None) -> None:
        atomic_write(out or self.path, bytes(self.data))


# ---- Modul-API ----------------------------------------------------------------
def scan_managed(dll_paths: list[str], progress=None) -> list[TableHit]:
    hits: list[TableHit] = []
    for p in dll_paths:
        if progress:
            progress(f"  lese {os.path.basename(p)} …")
        try:
            hits += Assembly(p).scan()
        except Exception as e:  # keine .NET-Assembly, kaputt, o.ä.
            if progress:
                progress(f"  {os.path.basename(p)}: übersprungen ({type(e).__name__})")
    return hits


def _label_pairs(old: float, new: float) -> list[tuple[str, str]]:
    """Mögliche Beschriftungen eines Werts und ihr Gegenstück nach dem Patch, gleiche Formatierung."""
    pairs = [
        (f"{old:.2f}", f"{new:.2f}"),
        (f"{old:.1f}", f"{new:.1f}"),
        (f"{old:g}", f"{new:g}"),
        (f"{round(old * 100):d}%", f"{round(new * 100):d}%"),
        (f"{round(old * 100):d} %", f"{round(new * 100):d} %"),
    ]
    return [(a, b) for a, b in dict.fromkeys(pairs) if len(a) >= 3]


def _new_value(site: Site, factor: float) -> float:
    return site.value * factor if site.effect == "mul" else site.value / factor


def apply_factor(hits: list[TableHit], factor: float, manifest: dict | None = None,
                 labels: bool = True, dry: bool = False) -> list[str]:
    """Multipliziert alle gefundenen Tabellenwerte mit dem Faktor (teilt bei Referenzauflösungen)."""
    report: list[str] = []
    by_path: dict[str, list[TableHit]] = {}
    for h in hits:
        if h.sites:
            by_path.setdefault(h.path, []).append(h)
    for path, dll_hits in by_path.items():
        orig = ensure_backup(path, manifest)
        asm = Assembly(orig)
        changed: dict[float, float] = {}
        for h in dll_hits:
            parts = []
            for s in h.sites:
                # Ausgangswert immer aus dem Original lesen: der Scan lief womöglich über eine schon gepatchte DLL
                base = Site(s.offset, struct.unpack_from("<f", asm.data, s.offset)[0], s.effect)
                nv = _new_value(base, factor)
                asm.write_float(s.offset, nv)
                if s.effect == "mul":
                    changed[base.value] = nv
                parts.append(f"{base.value:g}->{nv:.4g}")
            report.append(f"{h.dll} {h.method}: " + "  ".join(parts))
        if labels:
            mapping: dict[str, str] = {}
            for old, new in sorted(changed.items()):
                for a, b in _label_pairs(old, new):
                    if asm.count_label(a) == 1 and len(a) == len(b) and b not in mapping.values():
                        mapping[a] = b
                        break
            report += asm.rename_labels(mapping)
        if not dry:
            asm.save(path)
            report.append(f"geschrieben: {path}")
    return report
