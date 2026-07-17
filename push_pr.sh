#!/usr/bin/env bash
# Pousse la branche de décomplexification et affiche le lien pour ouvrir la PR.
# À lancer depuis ton terminal (auth GitHub interactive) :  bash push_pr.sh
set -e
cd "$(dirname "$0")"
git push -u origin chore/declutter-aiforssh
echo
echo "Ouvre la PR ici (base: main) :"
echo "https://github.com/laurykb/ai_for_requirements/compare/main...chore/declutter-aiforssh?expand=1"
