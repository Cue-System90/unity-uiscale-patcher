using BepInEx;
using BepInEx.Configuration;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;

namespace UIScaleMultiplier
{
    [BepInPlugin(Guid, Name, Version)]
    public class Plugin : BasePlugin
    {
        public const string Guid = "de.cuesystem.uiscale";
        public const string Name = "UI-Skalierung";
        public const string Version = "1.0.0";

        private ConfigEntry<float> _multiplier;
        private ConfigEntry<string> _exclude;
        private ConfigEntry<bool> _skipWorldSpace;

        public override void Load()
        {
            _multiplier = Config.Bind("General", "Multiplier", 1.5f,
                new ConfigDescription("Faktor, mit dem jede uGUI-Skalierung multipliziert wird.", new AcceptableValueRange<float>(0.25f, 5f)));
            _exclude = Config.Bind("General", "Exclude", "",
                "Canvas-Namen, die nicht skaliert werden (kommagetrennt).");
            _skipWorldSpace = Config.Bind("General", "SkipWorldSpace", true,
                "World-Space-Canvases unverändert lassen.");

            ScalerHook.GetMultiplier = () => _multiplier.Value;
            ScalerHook.GetExclude = () => _exclude.Value;
            ScalerHook.GetSkipWorldSpace = () => _skipWorldSpace.Value;
            ScalerHook.Log = msg => Log.LogWarning(msg);

            if (ScalerHook.Apply(new Harmony(Guid)))
                Log.LogInfo($"{Name} {Version}: Faktor {_multiplier.Value:0.##}");
        }
    }
}
