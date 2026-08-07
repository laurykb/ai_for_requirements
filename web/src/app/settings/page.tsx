import { SettingsView } from "@/components/settings";
import { PanelTitle } from "@/components/ui";

/** Réglages du chat : préférences utilisateur et configuration experte. */

export default function SettingsPage() {
  return (
    <div className="rise-in py-4">
      <div className="mx-auto max-w-2xl">
        <PanelTitle
          kicker="Chat et recherche"
          title="Réglages du chat"
          hint="Personnaliser les réponses et, en mode avancé, les modèles et le moteur documentaire."
        />
      </div>
      <SettingsView />
    </div>
  );
}
