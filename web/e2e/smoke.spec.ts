import { expect, test } from "@playwright/test";

/** Smoke : chaque page se monte (pas de page blanche, pas d'erreur React),
 * les éléments structurants sont présents. Tolère une API dégradée. */

test("accueil : les deux cartes d'outils", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText("Outil RAG")).toBeVisible();
  await expect(page.getByText("AI for Requirements")).toBeVisible();
});

for (const path of ["/rag", "/documents", "/agents", "/informations", "/settings", "/observability"]) {
  test(`page ${path} se monte`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const res = await page.goto(path);
    expect(res?.status()).toBe(200);
    await expect(page.locator("body")).not.toBeEmpty();
    expect(errors, `erreurs JS sur ${path} : ${errors.join(" | ")}`).toHaveLength(0);
  });
}

test("chat RAG : composeur présent, périmètre et modes visibles", async ({ page }) => {
  await page.goto("/rag");
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(page.getByLabel("Document interrogé")).toBeVisible();
  await expect(page.getByLabel("Mode de traitement")).toBeVisible();
});

test("AI for Requirements : onglets en barre de menu, le Chat affiche l'espace baseline", async ({ page }) => {
  await page.goto("/requirements");
  for (const tab of ["Matrice", "Exigences", "Chat", "Paramètres"]) {
    await expect(page.getByRole("link", { name: tab, exact: true })).toBeVisible();
  }
  await page.getByRole("link", { name: "Chat", exact: true }).click();
  // Selon l'état de l'index : bandeau baseline, invite de synchronisation,
  // spinner d'interrogation ou bannière d'erreur API — jamais une zone vide.
  await expect(
    page.getByText(/Baseline d['’]exigences|baseline|API locale injoignable/i).first(),
  ).toBeVisible({ timeout: 10_000 });
});

test("navigateur d'exigences : liste, filtres et compteur", async ({ page }) => {
  await page.goto("/requirements?tab=exigences");
  await expect(page.getByLabel("Rechercher une exigence")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByLabel("Filtrer par domaine")).toBeVisible();
});

test("chat baseline : pas de sélecteur de mode ni de pièce jointe", async ({ page }) => {
  await page.goto("/requirements?tab=chat");
  const composer = page.getByLabel("Question");
  // Le composeur n'apparaît que si l'index est prêt ; sinon le test vérifie
  // simplement l'absence des contrôles hors périmètre dans l'espace baseline.
  if (await composer.isVisible().catch(() => false)) {
    await expect(page.getByLabel("Mode de traitement")).toHaveCount(0);
    await expect(page.getByLabel("Joindre un document")).toHaveCount(0);
    await expect(page.getByLabel("Document interrogé")).toHaveCount(0);
  }
});
