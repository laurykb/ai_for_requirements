import { SettingsView } from "@/components/settings";
import { PanelTitle } from "@/components/ui";

/** Paramètres : recherche, modèles, réglages .env, system prompt, corpus. */

export default function SettingsPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-2xl">
        <PanelTitle
          kicker="Configuration"
          title="Paramètres"
          hint="Préférences de recherche (effet immédiat), modèles, réglages .env et gestion du corpus."
        />
      </div>
      <SettingsView />
    </div>
  );
}
