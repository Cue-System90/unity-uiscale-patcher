#!/usr/bin/env python3
"""Unity UI-Scale-Patcher: UI-Skalierung beliebiger Unity-Spiele anheben.

    uiscale.py detect  SPIELORDNER                      Spiel analysieren, Weg empfehlen
    uiscale.py patch   SPIELORDNER --factor 1.5         empfohlenen Weg anwenden
    uiscale.py patch   SPIELORDNER --factor 1.5 --method il|assets|plugin
    uiscale.py restore SPIELORDNER                      alles zurücknehmen

Drei Wege, automatisch gewählt:
  il       Konstanten im Spielcode (Mono) – wenn das Spiel die Skalierung aus einer Wertetabelle setzt
  assets   CanvasScaler-Komponenten in den Szenen – wenn kein Code sie überschreibt (auch IL2CPP)
  plugin   BepInEx-Plugin mit Harmony-Hook auf CanvasScaler.SetScaleFactor – immer möglich
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uiscale import __version__, assetpatch, backup, ilpatch, plugin  # noqa: E402
from uiscale.game import GameInfo, find_game  # noqa: E402


def say(msg: str) -> None:
    print(msg, flush=True)


# ---- Analyse ----------------------------------------------------------------------
def analyse(game: GameInfo, quiet: bool = False) -> dict:
    progress = (lambda m: None) if quiet else say
    result = {"il": [], "assets": []}
    if game.backend == "mono":
        result["il"] = ilpatch.scan_managed(game.game_assemblies(), progress)
    if not game.bundled_data:
        try:
            result["assets"] = assetpatch.scan_assets(game.serialized_files(), progress)
        except Exception as e:
            result["assets_error"] = f"{type(e).__name__}: {e}"
    result["method"], result["reasons"], result["warnings"] = recommend(game, result["il"], result["assets"])
    return result


def recommend(game: GameInfo, il_hits, assets) -> tuple[str, list[str], list[str]]:
    reasons, warnings = [], []
    il_tables = [h for h in il_hits if h.sites]
    il_computed = [h for h in il_hits if not h.sites]
    usable_assets = [a for a in assets if not a.world_space]
    if game.backend == "mono" and il_tables:
        reasons.append(f"Der Spielcode setzt die Skalierung aus einer Wertetabelle ({len(il_tables)} Methode(n)).")
        if il_computed:
            warnings.append(f"{len(il_computed)} weitere Methode(n) berechnen den Wert zur Laufzeit; die bleiben unverändert.")
        return "il", reasons, warnings
    if game.backend == "mono" and il_computed:
        reasons.append("Der Spielcode berechnet die Skalierung zur Laufzeit, ein Konstanten-Patch greift nicht.")
        reasons.append("Asset-Werte würden überschrieben, bleibt das Plugin.")
        return "plugin", reasons, warnings
    if usable_assets:
        reasons.append(f"{len(usable_assets)} CanvasScaler-Komponente(n) in den Szenen gefunden.")
        if game.backend == "mono":
            reasons.append("Kein Spielcode greift auf den CanvasScaler zu, die Werte aus den Szenen gelten.")
        else:
            warnings.append("IL2CPP: ob Spielcode die Werte zur Laufzeit überschreibt, lässt sich nicht prüfen. Greift der Patch nicht, hilft --method plugin.")
        return "assets", reasons, warnings
    if game.bundled_data:
        reasons.append("Die Spieldaten liegen gebündelt in data.unity3d; Asset-Patch wird dafür nicht unterstützt.")
    elif not assets:
        reasons.append("Keine CanvasScaler-Komponenten gefunden (UI Toolkit, nur Prefabs in Bundles, oder Scan fehlgeschlagen).")
    else:
        reasons.append("Alle CanvasScaler hängen an World-Space-Canvases.")
    reasons.append("Bleibt das Plugin, das zur Laufzeit jeden Faktor multipliziert.")
    if game.backend == "il2cpp":
        warnings.append("IL2CPP braucht BepInEx 6 (Vorabversion); das IL2CPP-Plugin ist experimentell.")
    return "plugin", reasons, warnings


def print_analysis(game: GameInfo, a: dict) -> None:
    say(game.summary())
    say("")
    if game.backend == "mono":
        if a["il"]:
            say("Code mit CanvasScaler-Zugriff:")
            for h in a["il"]:
                vals = "  ".join(f"{v:g}" for v in h.values) if h.sites else "(keine Konstanten)"
                extra = f"   [{h.note}]" if h.note else ""
                say(f"  {h.dll} {h.method}  {', '.join(h.setters)}  Tabelle: {vals}{extra}")
        else:
            say("Code mit CanvasScaler-Zugriff: keiner")
    if "assets_error" in a:
        say(f"Asset-Scan fehlgeschlagen: {a['assets_error']}")
    elif not game.bundled_data:
        say(f"CanvasScaler in Szenen/Assets: {len(a['assets'])}")
        for s in a["assets"][:30]:
            say(f"  {s.describe()}")
        if len(a["assets"]) > 30:
            say(f"  … und {len(a['assets']) - 30} weitere")
    say("")
    say(f"Empfehlung: --method {a['method']}")
    for r in a["reasons"]:
        say(f"  {r}")
    for w in a["warnings"]:
        say(f"  Achtung: {w}")


# ---- Befehle ----------------------------------------------------------------------
def cmd_detect(args) -> None:
    game = find_game(args.game_dir)
    print_analysis(game, analyse(game))


def cmd_patch(args) -> None:
    game = find_game(args.game_dir)
    if args.factor <= 0:
        raise SystemExit("--factor muss größer als 0 sein")
    manifest = backup.load_manifest(game.root)
    method = args.method
    a = None
    if method == "auto" or method in ("il", "assets"):
        a = analyse(game)
        if method == "auto":
            print_analysis(game, a)
            method = a["method"]
            say("")
    else:
        say(game.summary())
        say("")
    say(f"Weg: {method}   Faktor: {args.factor:g}" + ("   (Probelauf)" if args.dry_run else ""))
    if method == "il":
        if game.backend != "mono":
            raise SystemExit("--method il geht nur bei Mono-Spielen (Managed/Assembly-CSharp.dll)")
        hits = [h for h in a["il"] if h.sites]
        if not hits:
            raise SystemExit("Keine Wertetabelle im Spielcode gefunden; --method assets oder plugin versuchen")
        report = ilpatch.apply_factor(hits, args.factor, manifest, labels=not args.no_labels, dry=args.dry_run)
    elif method == "assets":
        if game.bundled_data:
            raise SystemExit("Spieldaten liegen in data.unity3d; --method plugin verwenden")
        if not a["assets"]:
            raise SystemExit("Keine CanvasScaler-Komponenten gefunden; --method plugin verwenden")
        report = assetpatch.apply_factor(a["assets"], args.factor, manifest, include_world=args.include_world, dry=args.dry_run)
    elif method == "plugin":
        report = plugin.install(game, args.factor, manifest, plugin_dll=args.plugin_dll,
                                bepinex_zip=args.bepinex_zip, dry=args.dry_run, progress=say)
    else:
        raise SystemExit(f"Unbekannter Weg: {method}")
    for line in report:
        say("  " + line)
    if not args.dry_run:
        backup.record(manifest, method, args.factor, report)
        backup.save_manifest(game.root, manifest)
        say(f"Manifest: {backup.manifest_path(game.root)}  (uiscale.py restore nimmt alles zurück)")
    else:
        say("Probelauf, nichts geschrieben.")


def cmd_restore(args) -> None:
    game = find_game(args.game_dir)
    for line in backup.restore(game.root, game.data_dir):
        say("  " + line)
    say("Fertig.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"uiscale {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="Spiel analysieren und Weg empfehlen")
    d.add_argument("game_dir")
    d.set_defaults(func=cmd_detect)

    p = sub.add_parser("patch", help="UI-Skalierung anheben")
    p.add_argument("game_dir")
    p.add_argument("--factor", type=float, required=True, help="Multiplikator, z.B. 1.5 für 50 %% größer")
    p.add_argument("--method", choices=("auto", "il", "assets", "plugin"), default="auto")
    p.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts schreiben")
    p.add_argument("--no-labels", action="store_true", help="il: Menü-Beschriftungen nicht umbenennen")
    p.add_argument("--include-world", action="store_true", help="assets: auch World-Space-Canvases skalieren")
    p.add_argument("--plugin-dll", help="plugin: lokal gebaute UIScaleMultiplier-DLL statt Download")
    p.add_argument("--bepinex-zip", help="plugin: lokales BepInEx-Archiv statt Download")
    p.set_defaults(func=cmd_patch)

    r = sub.add_parser("restore", help="Backups zurückspielen, installierte Dateien entfernen")
    r.add_argument("game_dir")
    r.set_defaults(func=cmd_restore)

    args = ap.parse_args()
    try:
        args.func(args)
    except (FileNotFoundError, RuntimeError, LookupError, ValueError) as e:
        raise SystemExit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
