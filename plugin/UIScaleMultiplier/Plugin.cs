using BepInEx;
using BepInEx.Configuration;
using HarmonyLib;
using UnityEngine;

namespace UIScaleMultiplier
{
    [BepInPlugin(Guid, Name, Version)]
    public class Plugin : BaseUnityPlugin
    {
        public const string Guid = "de.cuesystem.uiscale";
        public const string Name = "UI Scale Multiplier";
        public const string Version = "1.0.0";

        private const float Min = 0.25f, Max = 5f;

        private ConfigEntry<float> _multiplier;
        private ConfigEntry<string> _exclude;
        private ConfigEntry<bool> _skipWorldSpace;
        private ConfigEntry<float> _step;
        private ConfigEntry<KeyboardShortcut> _increase;
        private ConfigEntry<KeyboardShortcut> _decrease;

        private void Awake()
        {
            _multiplier = Config.Bind("General", "Multiplier", 1.5f,
                new ConfigDescription("Faktor, mit dem jede uGUI-Skalierung multipliziert wird.", new AcceptableValueRange<float>(Min, Max)));
            _exclude = Config.Bind("General", "Exclude", "",
                "Canvas-Namen, die nicht skaliert werden (kommagetrennt).");
            _skipWorldSpace = Config.Bind("General", "SkipWorldSpace", true,
                "World-Space-Canvases unverändert lassen.");
            _step = Config.Bind("Hotkeys", "Step", 0.1f, "Schrittweite der Hotkeys.");
            _increase = Config.Bind("Hotkeys", "Increase", new KeyboardShortcut(KeyCode.KeypadPlus, KeyCode.LeftControl),
                "UI vergrößern.");
            _decrease = Config.Bind("Hotkeys", "Decrease", new KeyboardShortcut(KeyCode.KeypadMinus, KeyCode.LeftControl),
                "UI verkleinern.");

            ScalerHook.GetMultiplier = () => _multiplier.Value;
            ScalerHook.GetExclude = () => _exclude.Value;
            ScalerHook.GetSkipWorldSpace = () => _skipWorldSpace.Value;
            ScalerHook.Log = msg => Logger.LogWarning(msg);

            if (ScalerHook.Apply(new Harmony(Guid)))
                Logger.LogInfo(string.Format("{0} {1}: Faktor {2:0.##}", Name, Version, _multiplier.Value));
        }

        private void Update()
        {
            if (_increase.Value.IsDown()) Bump(_step.Value);
            else if (_decrease.Value.IsDown()) Bump(-_step.Value);
        }

        private void Bump(float delta)
        {
            var v = Mathf.Round((_multiplier.Value + delta) * 100f) / 100f;
            _multiplier.Value = Mathf.Clamp(v, Min, Max);
            Logger.LogInfo(string.Format("UI-Faktor: {0:0.##}", _multiplier.Value));
        }
    }
}
