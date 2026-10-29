# Funktionsweise

Wie Unity-Spiele ihre Oberfläche skalieren, und warum dieser Patcher drei Wege braucht.
Entstanden aus dem [polismos-patcher](https://github.com/Cue-System90/polismos-patcher).

## Was Polismos gezeigt hat

Das Spiel hält eine Tabelle von fünf Faktoren, wählt per Regler-Index einen aus und schreibt
ihn in `CanvasScaler.scaleFactor` aller Canvases. Drei Dinge daran sind typisch für
Unity-Spiele, eines nicht:

- Typisch: Die Skalierung läuft über `UnityEngine.UI.CanvasScaler`. Jeder Canvas hat
  einen, und jede Änderung endet in `CanvasScaler.SetScaleFactor()` beziehungsweise
  `Canvas.scaleFactor`.
- Typisch: Die Auswahl des Spielers landet als kleine Zahl in den PlayerPrefs.
- Typisch: Mono-Build mit `Assembly-CSharp.dll`, das sich mit Metadaten-Werkzeugen
  lesen lässt.
- Nicht typisch: Dass die Faktoren als Konstanten im Code stehen. Viele Spiele rechnen
  den Faktor aus einem Regler-Float, oder sie setzen ihn gar nicht per Code, sondern nur
  in der Szene.

Ein allgemeines Werkzeug muss also an der Stelle ansetzen, die alle Spiele teilen:
dem `CanvasScaler`.

## Wie Unity skaliert

`CanvasScaler.Update()` ruft jedes Frame `Handle()` auf. Je nach `uiScaleMode` passiert dort:

| Modus | Eingang | Ergebnis |
|---|---|---|
| Constant Pixel Size | `scaleFactor` | `SetScaleFactor(scaleFactor)` |
| Scale With Screen Size | `referenceResolution`, `matchWidthOrHeight` | Faktor aus Bildschirm / Referenz, dann `SetScaleFactor()` |
| Constant Physical Size | DPI | Faktor aus DPI, dann `SetScaleFactor()` |

Alle drei Wege enden in der privaten Methode `SetScaleFactor(float)`, die `Canvas.scaleFactor`
setzt. World-Space-Canvases werden übersprungen. Das ist der eine Trichter, durch den jede
uGUI-Skalierung läuft.

Abseits von uGUI gibt es zwei weitere Systeme, die seltener in Spielen vorkommen: UI Toolkit
(`PanelSettings.scale` und `referenceResolution`) und das alte IMGUI (`GUI.matrix`). Ein
allgemeines Werkzeug sollte sie kennen, muss sie aber nicht in der ersten Fassung abdecken.

## Drei Wege

### 1. Statischer Patch der Assembly

Das ist `--method il` (`uiscale/ilpatch.py`): Methoden finden, die
`CanvasScaler.set_scaleFactor` oder `set_referenceResolution` aufrufen, ihre `ldc.r4`-Konstanten
zeigen, gewünschte Werte ersetzen. Die Methode wird über die Metadaten gefunden, nicht über
feste Dateioffsets, daher überlebt der Patch kleine Spiel-Updates. Welche Konstanten zur
Skalierung gehören, entscheidet ein kleiner Datenfluss-Blick: Steht vor dem Setter ein `ldloc`,
zählen alle `ldc.r4`, die per `stloc` in dieselbe Variable geschrieben werden. So bleibt eine
zufällig gleiche Konstante an anderer Stelle der Methode unberührt.

Grenzen:

- Nur Mono-Builds. Bei IL2CPP gibt es keine Assembly mit IL, nur `GameAssembly.dll` als
  nativen Code.
- Nur, wenn das Spiel die Werte als Konstanten hält. Rechnet es `slider.value * k`, müsste man
  IL umschreiben (Multiplikation einfügen), was Methodenlängen ändert und Sprungziele verschiebt.
  Dafür braucht es einen echten IL-Rewriter (Mono.Cecil, dnlib) statt Byte-Patching.
- Beschriftungen lassen sich nur bei gleicher Länge ersetzen, sonst muss der `#US`-Heap neu
  gebaut werden.

`--method assets` (`uiscale/assetpatch.py`) kommt ohne Code aus: Setzt das Spiel die Skalierung nirgends per Code, steht sie
als `m_ScaleFactor` oder `m_ReferenceResolution` in den serialisierten `CanvasScaler`-Objekten
der Szenen (`level*`, `sharedassets*.assets`). Die lassen sich mit UnityPy finden (MonoScript
`CanvasScaler`) und direkt umschreiben; das Layout der Komponente ist fest und klein. Das
funktioniert auch bei IL2CPP, aber nur, solange kein Skript den Wert zur Laufzeit überschreibt.

### 2. Laufzeit-Plugin mit BepInEx und Harmony

`--method plugin` (`uiscale/plugin.py`, Quellcode unter `plugin/`). Der allgemeinste Weg. Ein Harmony-Prefix auf `CanvasScaler.SetScaleFactor(float)` multipliziert
das Argument mit einem einstellbaren Faktor:

```csharp
[BepInPlugin("de.cuesystem.uiscale", "UI Scale Multiplier", "1.0.0")]
public class UiScalePlugin : BaseUnityPlugin
{
    internal static ConfigEntry<float> Multiplier;

    void Awake()
    {
        Multiplier = Config.Bind("General", "Multiplier", 1.5f,
            "Faktor, mit dem jede uGUI-Skalierung multipliziert wird");
        new Harmony("de.cuesystem.uiscale").PatchAll();
    }
}

[HarmonyPatch(typeof(CanvasScaler), "SetScaleFactor")]
static class SetScaleFactorPatch
{
    static void Prefix(CanvasScaler __instance, ref float scaleFactor)
    {
        var canvas = __instance.GetComponent<Canvas>();
        if (canvas != null && canvas.renderMode == RenderMode.WorldSpace) return;
        scaleFactor *= UiScalePlugin.Multiplier.Value;
    }
}
```

Das deckt alle drei Skalierungsmodi ab, greift auch dann, wenn das Spiel den Faktor jedes
Frame neu setzt, und braucht keinerlei Wissen über das einzelne Spiel. Mit BepInEx 5 läuft es
auf Mono, mit BepInEx 6 (Il2CppInterop) auch auf IL2CPP. Sinnvolle Erweiterungen:

- Ausnahmen pro Canvas-Name, weil manche Overlays (Ladebalken, Cursor, Splash) nicht mitwachsen
  sollen.
- Ein Hotkey, der den Faktor zur Laufzeit in Schritten ändert und speichert.
- Ein zweiter Prefix auf `Canvas.set_scaleFactor` für Spiele, die am CanvasScaler vorbei direkt
  in den Canvas schreiben. Dann muss man Rekursion vermeiden, weil `SetScaleFactor` selbst diesen
  Setter ruft.

Unter Proton braucht BepInEx den Startparameter `WINEDLLOVERRIDES="winhttp=n,b" %command%`,
damit der Loader geladen wird.

### 3. Nur Doorstop, ohne Plugin-System

UnityDoorstop allein lädt eine beliebige Assembly in den Mono-Prozess. Damit ließe sich ein
Mini-Loader bauen, der nach jedem Szenenwechsel alle `CanvasScaler` einsammelt und ihre Werte
multipliziert. Das spart BepInEx, verliert aber Harmony und damit den sauberen Eingriff in
`SetScaleFactor`. Da BepInEx ohnehin auf Doorstop aufsetzt, lohnt sich dieser Mittelweg kaum.

## Empfehlung

| Ziel | Weg |
|---|---|
| Ein bestimmtes Spiel, schnell | `uiscale_tool.py scan`, dann `patch` mit den gefundenen Konstanten |
| Spiel ohne Code-Zugriff auf die Skalierung | Serialisierten `CanvasScaler` in den Szenen-Assets umschreiben |
| Beliebige Spiele, auch IL2CPP | BepInEx-Plugin mit Harmony-Prefix auf `SetScaleFactor` |

Genau so ist dieses Repository aufgebaut: `uiscale.py detect` analysiert das Spiel und
empfiehlt einen Weg, `patch` wendet ihn an, `restore` nimmt ihn zurück.

## Was kein Werkzeug lösen kann

- Layouts sind für einen Faktor gebaut. Ab einem bestimmten Punkt überlappen Panels, laufen
  Texte aus Rahmen oder verschwinden Buttons hinter dem Bildschirmrand. Das hängt an Ankern und
  Layout-Gruppen des jeweiligen Spiels, nicht an der Skalierung selbst.
- Screen-Space-Camera-Canvases und Pixel-Perfect-Einstellungen reagieren anders als
  Overlay-Canvases und brauchen oft eigene Ausnahmen.
- TextMeshPro mit Auto-Size setzt seine Schriftgröße nach eigenen Regeln neu; große Faktoren
  können hier zu kleinerem statt größerem Text führen.
