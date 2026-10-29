# Unity UI-Scale-Patcher

Hebt die UI-Skalierung von Unity-Spielen an, deren Menü keinen passenden Regler hat. Der
Patcher erkennt pro Spiel, wie es seine Oberfläche skaliert, und wählt daraus einen von drei
Wegen. Alle drei lassen sich mit einem Befehl zurücknehmen.

| Weg | Wann | Wie |
|---|---|---|
| **il** | Mono-Spiel, der Code setzt die Skalierung aus einer Wertetabelle | Konstanten in `Assembly-CSharp.dll` werden multipliziert, Menü-Beschriftungen mit umbenannt |
| **assets** | Kein Code setzt die Skalierung, die Werte stehen in den Szenen (auch IL2CPP) | `CanvasScaler`-Komponenten in den Asset-Dateien werden umgeschrieben |
| **plugin** | Immer, besonders bei berechneten Werten, gebündelten Daten oder IL2CPP | BepInEx wird installiert; ein Plugin multipliziert zur Laufzeit jeden `CanvasScaler.SetScaleFactor()`-Aufruf |

## Benutzung

Python 3.10 oder neuer. Das Spiel darf während des Patchens nicht laufen.

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python uiscale.py detect "/pfad/zu/steamapps/common/Spiel"
venv/bin/python uiscale.py patch  "/pfad/zu/steamapps/common/Spiel" --factor 1.5
venv/bin/python uiscale.py restore "/pfad/zu/steamapps/common/Spiel"
```

`detect` zeigt Unity-Version, Backend (Mono oder IL2CPP), Plattform, gefundene Wertetabellen im
Code, `CanvasScaler`-Komponenten in den Szenen und die Empfehlung. `patch` wendet den empfohlenen
Weg an; `--method il|assets|plugin` erzwingt einen bestimmten. `--dry-run` zeigt nur, was passieren
würde.

Der Faktor ist ein Multiplikator: `--factor 1.5` macht jede Stufe, die das Spiel anbietet, um die
Hälfte größer. Beim Plugin lässt er sich später in `BepInEx/config/de.cuesystem.uiscale.cfg` oder
im Spiel mit Strg + Nummernblock-Plus/Minus ändern.

Weitere Optionen:

```bash
--no-labels        il: Beschriftungen wie "1.20" nicht umbenennen
--include-world    assets: auch World-Space-Canvases skalieren
--plugin-dll PFAD  plugin: lokal gebaute DLL statt Download aus den Releases
--bepinex-zip PFAD plugin: lokales BepInEx-Archiv statt Download
```

Geänderte Dateien bekommen ein `*.orig`-Backup, installierte Dateien stehen in
`.uiscale-patcher.json` im Spielordner. `restore` spielt alles zurück und entfernt, was der
Patcher hinzugefügt hat. Ein Steam-Update oder „Spieldateien überprüfen“ überschreibt die Wege
`il` und `assets`; danach `patch` einfach erneut ausführen. Das Plugin überlebt Updates, solange
BepInEx im Ordner bleibt.

## Wie die Erkennung entscheidet

1. **Mono und Wertetabelle im Code**: `il`. Der Patcher liest alle Spiel-Assemblies, sucht
   Aufrufe von `CanvasScaler.set_scaleFactor` und `set_referenceResolution` und verfolgt, welche
   Konstanten in die übergebene Variable fließen. Nur diese werden geändert; eine zufällig
   gleiche Zahl an anderer Stelle bleibt unberührt.
2. **Mono, aber der Wert wird berechnet** (etwa aus einem Regler): `plugin`. Ein
   Konstanten-Patch greift nicht, und Asset-Werte würden überschrieben.
3. **Kein Code-Zugriff, `CanvasScaler` in den Szenen gefunden**: `assets`. Bei IL2CPP lässt sich
   nicht prüfen, ob der Code die Werte überschreibt; greift der Patch nicht, hilft `--method plugin`.
4. **Sonst**: `plugin`. Das betrifft gebündelte Daten (`data.unity3d`), UI Toolkit statt uGUI,
   und Spiele, deren Canvases nur in Asset-Bundles stecken.

## Das Plugin

`plugin/` enthält den Quellcode von **UIScaleMultiplier**: ein Harmony-Prefix auf
`CanvasScaler.SetScaleFactor(float)`, der den Faktor multipliziert. World-Space-Canvases werden
übersprungen, einzelne Canvases lassen sich per Name ausnehmen. Der Zugriff auf Unity-Typen läuft
über Reflection, deshalb kompiliert derselbe Kern für BepInEx 5 (Mono) und BepInEx 6 (IL2CPP).

Die DLLs baut GitHub Actions (`.github/workflows/plugin.yml`), bei einem Tag `v*` landen sie als
Release-Assets, von wo der Patcher sie lädt:

| Datei | Für |
|---|---|
| `UIScaleMultiplier.dll` | BepInEx 5, Unity 2018 und neuer |
| `UIScaleMultiplier.net35.dll` | BepInEx 5, Unity 5.x und 2017 |
| `UIScaleMultiplier.IL2CPP.dll` | BepInEx 6 Vorabversion, experimentell |

Unter Proton braucht BepInEx die Startoption `WINEDLLOVERRIDES="winhttp=n,b" %command%`, bei
nativen Linux-Builds `./run_bepinex.sh %command%`. Der Patcher gibt die passende Zeile aus.

## Stand

- `il` ist am Spiel Polismos verifiziert (der Vorgänger dieses Projekts ist der
  [polismos-patcher](https://github.com/Cue-System90/polismos-patcher)).
- `assets` wurde gegen Kopien von Szenen-Dateien getestet; der Byte-Patch ist an der
  Komponentengröße und Plausibilität der Felder abgesichert.
- Die Erkennung lief gegen Mono- und IL2CPP-Spiele von Unity 2019 bis Unity 6.
- Das Plugin wird von GitHub Actions gebaut und ist bisher nicht in einem laufenden Spiel
  getestet. Der IL2CPP-Zweig ist experimentell.

Dieses Repository enthält keine Spieldateien. Hintergründe und Grenzen der drei Wege stehen in
[docs/funktionsweise.md](docs/funktionsweise.md).
