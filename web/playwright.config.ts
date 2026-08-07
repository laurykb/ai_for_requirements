import { defineConfig } from "@playwright/test";

/** Smoke tests du front (e2e légers).
 *
 * Cible par défaut : l'app qui tourne déjà (python serve.py, front :3000).
 * Autre cible : E2E_BASE_URL=http://localhost:3001 npx playwright test
 *
 * Ces tests vérifient que chaque page se monte et que les éléments
 * structurants sont là ; ils tolèrent une API dégradée (bannières d'erreur)
 * mais échouent sur une page blanche ou une erreur de rendu React.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    headless: true,
  },
});
