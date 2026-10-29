using System;
using System.Reflection;
using HarmonyLib;

namespace UIScaleMultiplier
{
    /// <summary>
    /// Hängt sich in UnityEngine.UI.CanvasScaler.SetScaleFactor(float) und multipliziert den Faktor.
    /// Alles läuft über Reflection, damit keine Compile-Zeit-Abhängigkeit zu UnityEngine.UI nötig ist
    /// und derselbe Code für Mono- und IL2CPP-Interop-Typen funktioniert.
    /// </summary>
    internal static class ScalerHook
    {
        internal static Func<float> GetMultiplier = () => 1f;
        internal static Func<string> GetExclude = () => "";
        internal static Func<bool> GetSkipWorldSpace = () => true;
        internal static Action<string> Log = _ => { };

        private static Type _canvasType;
        private static MethodInfo _getCanvas;      // Component.GetComponent<Canvas>()
        private static PropertyInfo _renderMode;   // Canvas.renderMode
        private static PropertyInfo _gameObject;   // Component.gameObject
        private static PropertyInfo _name;         // Object.name

        private static void Preload(string assembly)
        {
            try { Assembly.Load(assembly); } catch (Exception) { }
        }

        internal static bool Apply(Harmony harmony)
        {
            foreach (var a in new[] { "UnityEngine.CoreModule", "UnityEngine.UIModule", "UnityEngine.UI", "UnityEngine" })
                Preload(a);

            var scaler = AccessTools.TypeByName("UnityEngine.UI.CanvasScaler");
            if (scaler == null)
            {
                Log("UnityEngine.UI.CanvasScaler nicht gefunden. Nutzt das Spiel kein uGUI?");
                return false;
            }
            var target = AccessTools.Method(scaler, "SetScaleFactor", new[] { typeof(float) });
            if (target == null)
            {
                Log("CanvasScaler.SetScaleFactor(float) nicht gefunden.");
                return false;
            }

            _canvasType = AccessTools.TypeByName("UnityEngine.Canvas");
            var component = AccessTools.TypeByName("UnityEngine.Component");
            var unityObject = AccessTools.TypeByName("UnityEngine.Object");
            if (_canvasType != null && component != null)
            {
                _renderMode = AccessTools.Property(_canvasType, "renderMode");
                _gameObject = AccessTools.Property(component, "gameObject");
                foreach (var m in component.GetMethods(BindingFlags.Public | BindingFlags.Instance))
                {
                    if (m.Name == "GetComponent" && m.IsGenericMethodDefinition && m.GetParameters().Length == 0)
                    {
                        _getCanvas = m.MakeGenericMethod(_canvasType);
                        break;
                    }
                }
            }
            if (unityObject != null)
                _name = AccessTools.Property(unityObject, "name");

            harmony.Patch(target, prefix: new HarmonyMethod(typeof(ScalerHook), nameof(Prefix)));
            return true;
        }

        // __0 ist das erste Argument (scaleFactor), unabhängig von seinem Namen in der jeweiligen Unity-Version.
        private static void Prefix(object __instance, ref float __0)
        {
            try
            {
                if (__instance == null) return;

                if (GetSkipWorldSpace() && _getCanvas != null && _renderMode != null)
                {
                    var canvas = _getCanvas.Invoke(__instance, null);
                    if (canvas != null && Convert.ToInt32(_renderMode.GetValue(canvas, null)) == 2) // RenderMode.WorldSpace
                        return;
                }

                var exclude = GetExclude();
                if (!string.IsNullOrEmpty(exclude) && _gameObject != null && _name != null)
                {
                    var go = _gameObject.GetValue(__instance, null);
                    var name = go == null ? null : _name.GetValue(go, null) as string;
                    if (name != null)
                    {
                        foreach (var part in exclude.Split(','))
                        {
                            var p = part.Trim();
                            if (p.Length > 0 && string.Equals(p, name, StringComparison.OrdinalIgnoreCase))
                                return;
                        }
                    }
                }

                __0 *= GetMultiplier();
            }
            catch (Exception)
            {
                // im Zweifel den Originalwert durchlassen
            }
        }
    }
}
