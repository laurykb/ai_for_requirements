# Conversion documents d'exigences → baseline JSON — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** Page LynX « Conversion » qui transforme un lot de documents d'exigences (.doc/.docx/.pdf/.xls/.xlsx/.md) en baseline JSON conforme au schéma `Requirement`, avec revue avant export/envoi.

**Architecture :** Pipeline 4 étages (normalisation LibreOffice+Docling → extraction déterministe par familles d'ids auto-détectées → détection LLM pour docs non marqués → liens explicites matrices/références), file séquentielle dédiée, endpoints FastAPI, onglet front à 3 états (dépôt/progression SSE/revue).

**Tech stack :** Python 3 (FastAPI, pydantic), LibreOffice headless, Docling (existant), LLM via `lynx/src/llm.call_agent`, Next.js + TS (front), pytest, Playwright.

**Spec :** `docs/superpowers/specs/2026-08-10-conversion-documents-baseline-design.md`

## Global Constraints

- **Transversalité** : aucun motif d'id, nom de document ou référentiel codé en dur ; seuils élastiques (proportionnels au document), pas de constantes figées.
- **Zéro hallucination** : texte LLM vérifié verbatim (aux espaces près) sinon rejeté et compté.
- **Rien n'entre dans le corpus LynX sans geste explicite** (l'envoi réutilise `/corpus/upload` existant avec le JSON édité en revue — pas de nouvel endpoint push).
- **Glass box** : chaque étage émet des événements SSE par document ; échecs et exclusions toujours signalés, jamais silencieux.
- Backend : le venv est `/home/marsattacks/Documents/rag_project/.venv` ; lancer pytest avec `~/Documents/rag_project/.venv/bin/python -m pytest`.
- Style : commentaires sobres en français, comme le code existant. Tests LLM mockés (pattern des 315 tests existants).
- Commits atomiques par tâche, messages `feat:`/`test:`/`docs:` en français.

## File Structure

```
conversion/
  __init__.py          (vide)
  model.py             dataclasses ExtractedReq, NormalizedDoc, IdFamily
  normalize.py         to_markdown() — soffice + Docling + tableur→CSV
  extract_marked.py    detect_id_families(), extract_marked()
  extract_llm.py       split_sections(), extract_unmarked()  [LLM]
  link_builder.py      matrix_links(), text_reference_links(), apply_links()
  assemble.py          assemble() → (baseline, report)
  queue.py             file séquentielle dédiée (pattern ingest_queue)
api/conversion.py      routeur (jobs, SSE, baseline, redo)
api/main.py            + include_router
api/lynx_api.py        refactor : merge_corpus_payloads() partagé
tests/conversion/      test_* par module + fixtures/
evals/run_conversion_eval.py + evals/conversion_golden.json
web/src/lib/types.ts, api.ts, sse.ts          (ajouts)
web/src/components/conversion/{index,depot,progress,revue}.tsx
web/src/app/requirements/page.tsx             (onglet "conversion")
web/e2e/conversion.spec.ts
```

---

### Task 1 : Modèle partagé + extraction déterministe (`extract_marked`)

**Files:**
- Create: `conversion/__init__.py` (vide), `conversion/model.py`, `conversion/extract_marked.py`
- Test: `tests/conversion/test_extract_marked.py`, `tests/conversion/__init__.py` (vide)

**Interfaces:**
- Produces:
  - `model.ExtractedReq` dataclass : `id: str, texte: str, doc: str, section: str, famille: str, etage: str ("marque"|"llm"), titre: str = ""`
  - `model.IdFamily` dataclass : `skeleton: str, count: int, n_definitions: int`
  - `extract_marked.detect_id_families(md_text: str) -> list[IdFamily]`
  - `extract_marked.extract_marked(md_text: str, doc_name: str, families: list[IdFamily]) -> list[ExtractedReq]`
  - `extract_marked.TOKEN_RE` (regex des tokens d'ids, réutilisée par link_builder)
  - `extract_marked.skeleton_of(token: str) -> str` (famille d'un token)

- [ ] **Step 1 : Écrire les tests qui échouent**

```python
# tests/conversion/test_extract_marked.py
"""Détection des familles d'ids et extraction des exigences marquées.

Fixtures synthétiques multi-motifs : crochets, underscores, points —
la détection ne doit connaître AUCUN référentiel particulier.
"""
from conversion.extract_marked import detect_id_families, extract_marked, skeleton_of

DOC_CROCHETS = """## 3.1 Etats du système
[SSS-STC-E-REQ-0001]
Etats du système
Le socle peut se trouver dans quatre états.
Chaque état est exclusif.
[SSS-STC-E-REQ-0002]
Etat non opérationnel
Le système est inactif.
## 3.2 Transitions
[SSS-STC-E-REQ-0003]
La transition suit un ordre de bataille initial (cf. [MC-TST-3.1-3780]).
"""

DOC_UNDERSCORES = """## 1 Exigences
EXG_SYS_01
Le produit doit démarrer en moins de 10 secondes.
EXG_SYS_02
Le produit doit journaliser chaque accès.
EXG_SYS_03
Le produit doit chiffrer les données au repos.
"""

DOC_LIBRE = """## 1 Introduction
Ce document décrit le fonctionnement général.
Le système doit être disponible. Aucun identifiant ici.
"""


def test_skeleton_remplace_les_nombres():
    assert skeleton_of("SSS-STC-E-REQ-0001") == "SSS-STC-E-REQ-#"
    assert skeleton_of("MC-TST-3.1-3780") == "MC-TST-#.#-#"
    assert skeleton_of("EXG_SYS_01") == "EXG_SYS_#"


def test_detecte_famille_avec_crochets():
    fams = detect_id_families(DOC_CROCHETS)
    skels = {f.skeleton for f in fams}
    assert "SSS-STC-E-REQ-#" in skels
    fam = next(f for f in fams if f.skeleton == "SSS-STC-E-REQ-#")
    assert fam.count == 3 and fam.n_definitions == 3


def test_famille_seulement_referencee_a_zero_definitions():
    # MC-TST-… n'apparaît qu'en citation dans le texte : 0 définition.
    fams = detect_id_families(DOC_CROCHETS)
    mc = [f for f in fams if f.skeleton == "MC-TST-#.#-#"]
    assert mc == [] or mc[0].n_definitions == 0


def test_document_sans_ids_ne_detecte_rien():
    assert detect_id_families(DOC_LIBRE) == []


def test_extraction_id_titre_corps_section():
    fams = detect_id_families(DOC_CROCHETS)
    reqs = extract_marked(DOC_CROCHETS, "SSS_test.doc", fams)
    assert [r.id for r in reqs] == ["SSS-STC-E-REQ-0001", "SSS-STC-E-REQ-0002",
                                    "SSS-STC-E-REQ-0003"]
    r1 = reqs[0]
    assert r1.titre == "Etats du système"
    assert "quatre états" in r1.texte and "exclusif" in r1.texte
    assert r1.section == "3.1 Etats du système"
    assert r1.doc == "SSS_test.doc"
    assert r1.famille == "SSS-STC-E-REQ-#" and r1.etage == "marque"
    # Le corps s'arrête au heading suivant :
    assert "transition" not in r1.texte.lower()


def test_extraction_sans_crochets_ni_titre():
    fams = detect_id_families(DOC_UNDERSCORES)
    reqs = extract_marked(DOC_UNDERSCORES, "exg.docx", fams)
    assert len(reqs) == 3
    assert reqs[0].id == "EXG_SYS_01"
    assert "10 secondes" in reqs[0].texte


def test_citation_inline_nest_pas_une_definition():
    fams = detect_id_families(DOC_CROCHETS)
    reqs = extract_marked(DOC_CROCHETS, "d.doc", fams)
    assert all(r.id != "MC-TST-3.1-3780" for r in reqs)


def test_seuil_elastique_petit_document():
    # 2 occurrences seulement : sous le seuil minimal de 3 → pas de famille.
    doc = "REQ-AA-01\nTexte un.\nREQ-AA-02\nTexte deux.\n"
    assert detect_id_families(doc) == []
```

- [ ] **Step 2 : Vérifier l'échec**

Run: `cd ~/Documents/AI_for_ssh_version_export && ~/Documents/rag_project/.venv/bin/python -m pytest tests/conversion/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'conversion'`

- [ ] **Step 3 : Implémenter**

```python
# conversion/model.py
"""Types partagés du pipeline de conversion documents → baseline."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractedReq:
    id: str
    texte: str
    doc: str            # nom du fichier source
    section: str        # dernier heading vu (ex. "3.1 Etats du système")
    famille: str        # squelette de la famille ("SSS-STC-E-REQ-#") ou "AUTO"
    etage: str          # "marque" | "llm"
    titre: str = ""


@dataclass
class IdFamily:
    skeleton: str
    count: int = 0          # occurrences totales (définitions + citations)
    n_definitions: int = 0  # occurrences seules sur leur ligne (sites de définition)


@dataclass
class NormalizedDoc:
    name: str
    kind: str                    # "markdown" | "table" | "echec"
    md_path: Path | None = None
    rows: list | None = None     # kind == "table" : lignes de cellules
    warnings: list = field(default_factory=list)
    error: str = ""
```

```python
# conversion/extract_marked.py
"""Détection des familles d'identifiants et extraction des exigences marquées.

Transversalité : aucune famille n'est codée en dur — un id est un token
répété de forme stable PREFIXE(sep)…(sep)numéro, toute ponctuation admise.
Le seuil de rétention est élastique (proportionnel à la taille du document).
"""
import re

from conversion.model import ExtractedReq, IdFamily

# Un token d'id : segments alphanumériques MAJUSCULES séparés par -_. ,
# se terminant par un nombre. Ex. [SSS-STC-E-REQ-0001], EXG_SYS_42, STB.1.2.3
TOKEN_RE = re.compile(
    r"\b[A-Z][A-Z0-9]{1,11}(?:[-_.][A-Z0-9]{1,12}){0,6}[-_.]\d{1,6}\b")

# Un heading : markdown (## …) ou numérotation nue en début de ligne (3.1.4 Titre)
HEADING_RE = re.compile(r"^(?:#{1,6}\s+)(.+)$")


def skeleton_of(token: str) -> str:
    """Famille d'un token : les nombres deviennent '#'. """
    return re.sub(r"\d+", "#", token)


def _is_definition_line(line: str) -> str | None:
    """Renvoie le token si la ligne est un site de définition (token seul,
    crochets/ponctuation tolérés), sinon None."""
    stripped = line.strip().strip("[]").strip(" .:")
    m = TOKEN_RE.fullmatch(stripped)
    return m.group(0) if m else None


def detect_id_families(md_text: str) -> list[IdFamily]:
    """Découvre les familles d'ids d'un document. Seuil élastique :
    une famille est retenue si count >= max(3, n_lignes // 500)."""
    lines = md_text.splitlines()
    fams: dict[str, IdFamily] = {}
    for line in lines:
        def_token = _is_definition_line(line)
        for m in TOKEN_RE.finditer(line):
            sk = skeleton_of(m.group(0))
            fam = fams.setdefault(sk, IdFamily(skeleton=sk))
            fam.count += 1
            if def_token == m.group(0):
                fam.n_definitions += 1
    seuil = max(3, len(lines) // 500)
    return sorted([f for f in fams.values() if f.count >= seuil],
                  key=lambda f: -f.count)


def extract_marked(md_text: str, doc_name: str,
                   families: list[IdFamily]) -> list[ExtractedReq]:
    """Découpe le document aux sites de définition des familles retenues :
    id → titre optionnel (ligne courte suivante) → corps jusqu'au prochain
    id ou heading. La section (dernier heading vu) est conservée."""
    defined = {f.skeleton for f in families if f.n_definitions > 0}
    lines = md_text.splitlines()
    reqs: list[ExtractedReq] = []
    section = ""
    current: ExtractedReq | None = None
    body: list[str] = []

    def close():
        nonlocal current, body
        if current is not None:
            texte = "\n".join(body).strip()
            current.texte = texte if texte else current.titre
            reqs.append(current)
        current, body = None, []

    for line in lines:
        h = HEADING_RE.match(line)
        if h:
            close()
            section = h.group(1).strip()
            continue
        token = _is_definition_line(line)
        if token and skeleton_of(token) in defined:
            close()
            current = ExtractedReq(id=token, texte="", doc=doc_name,
                                   section=section,
                                   famille=skeleton_of(token), etage="marque")
            continue
        if current is not None:
            if (not current.titre and not body and line.strip()
                    and len(line.strip()) <= 120
                    and not _is_definition_line(line)):
                current.titre = line.strip()
            else:
                body.append(line)
    close()
    return reqs
```

- [ ] **Step 4 : Vérifier que les tests passent**

Run: `~/Documents/rag_project/.venv/bin/python -m pytest tests/conversion/ -v`
Expected: 8 PASS. Si `test_skeleton_remplace_les_nombres` échoue sur `MC-TST-3.1-3780`, vérifier que `TOKEN_RE` accepte les segments numériques internes (`3.1`).

- [ ] **Step 5 : Vérifier la non-régression globale et commit**

Run: `~/Documents/rag_project/.venv/bin/python -m pytest --tb=short -q` — Expected : tout vert.

```bash
git add conversion/ tests/conversion/
git commit -m "feat(conversion): familles d'ids auto-détectées + extraction déterministe"
```

---

### Task 2 : Normalisation (`normalize.py`)

**Files:**
- Create: `conversion/normalize.py`
- Test: `tests/conversion/test_normalize.py`

**Interfaces:**
- Consumes: `model.NormalizedDoc` (Task 1) ; `preprocessing.pdf_to_markdown.doc_to_md(src, out_dir, do_ocr=False) -> str (chemin .md)`, `clean_md(md_path, save_as) -> str`, `_looks_scanned(md_text) -> bool` (existants)
- Produces: `normalize.to_markdown(path: Path, out_dir: Path) -> NormalizedDoc` ; `normalize.ACCEPTED_EXTS = ("pdf","doc","docx","pptx","xls","xlsx","html","md")` ; `normalize.soffice_convert(path: Path, target_ext: str, out_dir: Path) -> Path` (lève `RuntimeError` explicite si LibreOffice absent/échec)

- [ ] **Step 1 : Tests qui échouent** (soffice et Docling mockés — pas de dépendance système dans les tests)

```python
# tests/conversion/test_normalize.py
"""Routage par extension. soffice/Docling sont mockés : on teste la
logique de routage et les chemins d'erreur, pas les convertisseurs."""
from pathlib import Path
from unittest.mock import patch

import pytest

from conversion.normalize import to_markdown, soffice_convert, _rows_from_csv


def test_md_passe_tel_quel(tmp_path):
    src = tmp_path / "doc.md"
    src.write_text("## Titre\nCorps.", encoding="utf-8")
    nd = to_markdown(src, tmp_path / "out")
    assert nd.kind == "markdown" and nd.md_path.read_text(encoding="utf-8").startswith("## Titre")
    assert nd.error == ""


def test_doc_passe_par_soffice_puis_docling(tmp_path):
    src = tmp_path / "vieux.doc"
    src.write_bytes(b"binaire")
    fake_docx = tmp_path / "vieux.docx"
    fake_md = tmp_path / "out" / "vieux.md"

    with patch("conversion.normalize.soffice_convert", return_value=fake_docx) as so, \
         patch("conversion.normalize._docling_to_md", return_value=fake_md) as dm:
        fake_md.parent.mkdir(parents=True, exist_ok=True)
        fake_md.write_text("## ok", encoding="utf-8")
        nd = to_markdown(src, tmp_path / "out")
    so.assert_called_once()
    dm.assert_called_once_with(fake_docx, tmp_path / "out")
    assert nd.kind == "markdown"


def test_xls_tabulaire_sort_en_lignes(tmp_path):
    src = tmp_path / "matrice.xls"
    src.write_bytes(b"binaire")
    fake_csv = tmp_path / "matrice.csv"
    fake_csv.write_text('A,"B\nsuite",C\nD,E,F\n', encoding="utf-8")
    with patch("conversion.normalize.soffice_convert", return_value=fake_csv):
        nd = to_markdown(src, tmp_path / "out")
    assert nd.kind == "table"
    assert nd.rows == [["A", "B\nsuite", "C"], ["D", "E", "F"]]


def test_echec_conversion_est_signale_pas_leve(tmp_path):
    src = tmp_path / "casse.doc"
    src.write_bytes(b"binaire")
    with patch("conversion.normalize.soffice_convert",
               side_effect=RuntimeError("LibreOffice introuvable")):
        nd = to_markdown(src, tmp_path / "out")
    assert nd.kind == "echec" and "LibreOffice" in nd.error


def test_extension_inconnue_refusee(tmp_path):
    src = tmp_path / "schema.vsd"
    src.write_bytes(b"binaire")
    nd = to_markdown(src, tmp_path / "out")
    assert nd.kind == "echec" and "non supporté" in nd.error


def test_document_scanne_averti(tmp_path):
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"%PDF")
    fake_md = tmp_path / "out" / "scan.md"
    fake_md.parent.mkdir(parents=True, exist_ok=True)
    fake_md.write_text("<!-- page:1 -->\n\n<!-- page:2 -->", encoding="utf-8")
    with patch("conversion.normalize._docling_to_md", return_value=fake_md):
        nd = to_markdown(src, tmp_path / "out")
    assert any("scanné" in w for w in nd.warnings)
```

- [ ] **Step 2 : Vérifier l'échec** — Run pytest, Expected: FAIL `No module named 'conversion.normalize'`

- [ ] **Step 3 : Implémenter**

```python
# conversion/normalize.py
"""Étage 1 : tout document accepté devient Markdown (ou lignes de tableau).

.doc/.xls (formats binaires legacy) passent d'abord par LibreOffice headless ;
le Markdown vient de Docling via le convertisseur existant (pdf_to_markdown).
Un échec de conversion est un RÉSULTAT (kind="echec" + cause), jamais une
exception : le job continue sur les autres documents du lot.
"""
import csv
import io
import shutil
import subprocess
from pathlib import Path

from conversion.model import NormalizedDoc

ACCEPTED_EXTS = ("pdf", "doc", "docx", "pptx", "xls", "xlsx", "html", "md")
_SOFFICE_TIMEOUT = 300  # s — les .doc de 10 Mo prennent du temps


def soffice_convert(path: Path, target_ext: str, out_dir: Path) -> Path:
    """Convertit via LibreOffice headless. RuntimeError explicite si absent/échec."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice introuvable (installer libreoffice pour "
                           "convertir les .doc/.xls)")
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [soffice, "--headless", "--convert-to", target_ext,
         str(path), "--outdir", str(out_dir)],
        capture_output=True, timeout=_SOFFICE_TIMEOUT)
    target = out_dir / f"{path.stem}.{target_ext}"
    if proc.returncode != 0 or not target.exists():
        raise RuntimeError(f"Conversion LibreOffice échouée pour {path.name} : "
                           f"{proc.stderr.decode(errors='replace')[:300]}")
    return target


def _docling_to_md(path: Path, out_dir: Path) -> Path:
    """Docling → Markdown nettoyé, via le convertisseur existant."""
    from preprocessing.pdf_to_markdown import doc_to_md, clean_md
    raw = doc_to_md(str(path), out_dir=str(out_dir))
    return Path(clean_md(raw, save_as=str(out_dir / f"{path.stem}-clean.md")))


def _rows_from_csv(csv_path: Path) -> list[list[str]]:
    text = csv_path.read_text(encoding="utf-8", errors="replace")
    return [row for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]


def _looks_scanned_md(md_text: str) -> bool:
    from preprocessing.pdf_to_markdown import _looks_scanned
    return _looks_scanned(md_text)


def to_markdown(path: Path, out_dir: Path) -> NormalizedDoc:
    ext = path.suffix.lower().lstrip(".")
    nd = NormalizedDoc(name=path.name, kind="markdown")
    if ext not in ACCEPTED_EXTS:
        nd.kind, nd.error = "echec", f"Type non supporté : .{ext}"
        return nd
    try:
        if ext == "md":
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / path.name
            if target != path:
                target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            nd.md_path = target
            return nd
        if ext in ("xls", "xlsx"):
            # Tableur → lignes (matrice de traçabilité potentielle).
            csv_path = soffice_convert(path, "csv", out_dir)
            nd.kind, nd.rows = "table", _rows_from_csv(csv_path)
            return nd
        src = path
        if ext == "doc":
            src = soffice_convert(path, "docx", out_dir)
        nd.md_path = _docling_to_md(src, out_dir)
        md_text = nd.md_path.read_text(encoding="utf-8")
        if _looks_scanned_md(md_text):
            nd.warnings.append("Document probablement scanné — OCR non tenté en V1, "
                               "extraction dégradée.")
        return nd
    except Exception as exc:  # échec = résultat, le lot continue
        nd.kind, nd.error = "echec", str(exc)
        return nd
```

- [ ] **Step 4 : Vérifier** — Run `~/Documents/rag_project/.venv/bin/python -m pytest tests/conversion/ -v` — Expected: tout PASS.

- [ ] **Step 5 : Commit**

```bash
git add conversion/normalize.py tests/conversion/test_normalize.py
git commit -m "feat(conversion): normalisation LibreOffice+Docling, tableurs en lignes"
```

---

### Task 3 : Détection LLM (`extract_llm.py`)

**Files:**
- Create: `conversion/extract_llm.py`
- Test: `tests/conversion/test_extract_llm.py`

**Interfaces:**
- Consumes: `model.ExtractedReq` ; `lynx/src/llm.call_agent(system_prompt, user_data, label=None, schema=None) -> dict` (renvoie `{"error": ...}` en échec) — import paresseux avec insertion de `lynx/` dans `sys.path` (même mécanique que `api/lynx_api.py`).
- Produces:
  - `extract_llm.split_sections(md_text: str) -> list[tuple[str, str]]` (label, texte)
  - `extract_llm.extract_unmarked(md_text: str, doc_name: str, budget: int | None = None, progress=None) -> tuple[list[ExtractedReq], dict]` — stats : `{"sections": int, "llm_calls": int, "rejets_verbatim": int, "sections_non_traitees": int, "erreurs": int}` ; `progress` optionnel : `callable(done: int, total: int)`
  - `extract_llm.PROMPT_EXTRACTION` (constante)

- [ ] **Step 1 : Tests qui échouent** (LLM mocké)

```python
# tests/conversion/test_extract_llm.py
"""Détection LLM : découpage en sections, garde-fou verbatim, budget.
call_agent est mocké — aucun appel réseau."""
from unittest.mock import patch

from conversion.extract_llm import split_sections, extract_unmarked

DOC = """## 1 Introduction
Contexte général sans exigence.
## 2 Fonctions
Le système doit horodater chaque échange.
Il peut aussi archiver les journaux.
## 3 Performances
Le temps de réponse doit rester sous 2 secondes.
"""


def test_split_sections_par_heading():
    secs = split_sections(DOC)
    assert [s[0] for s in secs] == ["1 Introduction", "2 Fonctions", "3 Performances"]
    assert "horodater" in secs[1][1]


def _fake_llm(responses):
    """Renvoie un mock de call_agent qui dépile `responses` (une par section)."""
    it = iter(responses)
    def fake(system_prompt, user_data, label=None, schema=None):
        return next(it)
    return fake


def test_extraction_verbatim_acceptee_et_ids_deterministes():
    responses = [
        {"exigences": []},
        {"exigences": ["Le système doit horodater chaque échange."]},
        {"exigences": ["Le temps de réponse doit rester sous 2 secondes."]},
    ]
    with patch("conversion.extract_llm._call_agent", side_effect=_fake_llm(responses)):
        reqs, stats = extract_unmarked(DOC, "IRS_test.doc")
    assert [r.id for r in reqs] == ["IRS-TEST-AUTO-001", "IRS-TEST-AUTO-002"]
    assert reqs[0].section == "2 Fonctions" and reqs[0].etage == "llm"
    assert stats["llm_calls"] == 3 and stats["rejets_verbatim"] == 0


def test_texte_non_verbatim_rejete_et_compte():
    responses = [
        {"exigences": []},
        {"exigences": ["Le système horodate les échanges (reformulé)."]},
        {"exigences": []},
    ]
    with patch("conversion.extract_llm._call_agent", side_effect=_fake_llm(responses)):
        reqs, stats = extract_unmarked(DOC, "x.doc")
    assert reqs == [] and stats["rejets_verbatim"] == 1


def test_budget_epuise_sections_non_traitees():
    responses = [{"exigences": []}]
    with patch("conversion.extract_llm._call_agent", side_effect=_fake_llm(responses)):
        reqs, stats = extract_unmarked(DOC, "x.doc", budget=1)
    assert stats["llm_calls"] == 1 and stats["sections_non_traitees"] == 2


def test_erreur_llm_comptee_sans_casser():
    responses = [{"error": "LLM_DISABLED"}, {"exigences": []}, {"exigences": []}]
    with patch("conversion.extract_llm._call_agent", side_effect=_fake_llm(responses)):
        reqs, stats = extract_unmarked(DOC, "x.doc")
    assert stats["erreurs"] == 1 and reqs == []
```

- [ ] **Step 2 : Vérifier l'échec** — Expected: FAIL `No module named 'conversion.extract_llm'`

- [ ] **Step 3 : Implémenter**

```python
# conversion/extract_llm.py
"""Étage 3 : détection LLM des exigences dans les documents NON marqués.

Garde-fous (spec) : texte verbatim vérifié (zéro hallucination possible),
budget d'appels élastique, tout écart compté au rapport — jamais silencieux.
"""
import os
import re
import sys
from pathlib import Path

from pydantic import BaseModel

from conversion.model import ExtractedReq

ROOT = Path(__file__).resolve().parent.parent

PROMPT_EXTRACTION = (
    "Tu extrais les exigences d'une section de document technique.\n"
    "Une exigence est une phrase normative : « doit », « devra », « ne doit "
    "pas », « est tenu de »… Recopie chaque exigence STRICTEMENT verbatim "
    "(texte exact de la section, sans reformuler, sans compléter, sans "
    "fusionner deux phrases). Si la section ne contient aucune exigence, "
    "renvoie une liste vide.\n"
    'Réponds en JSON : {"exigences": ["texte exact 1", "texte exact 2"]}')


class _SectionExigences(BaseModel):
    exigences: list[str]


def _call_agent(system_prompt, user_data, label=None, schema=None):
    """Indirection testable vers lynx llm.call_agent (import paresseux)."""
    lynx_dir = str(ROOT / "lynx")
    if lynx_dir not in sys.path:
        sys.path.insert(0, lynx_dir)
    from src.llm import call_agent
    return call_agent(system_prompt, user_data, label=label, schema=schema)


def split_sections(md_text: str) -> list[tuple[str, str]]:
    """Découpe au heading markdown. Le préambule sans heading est ignoré
    s'il est vide, sinon rattaché à une section '(préambule)'."""
    sections: list[tuple[str, str]] = []
    label, buf = "(préambule)", []
    for line in md_text.splitlines():
        m = re.match(r"^#{1,6}\s+(.+)$", line)
        if m:
            if "".join(buf).strip():
                sections.append((label, "\n".join(buf).strip()))
            label, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((label, "\n".join(buf).strip()))
    return sections


def _norm(s: str) -> str:
    return " ".join(s.split())


def _doc_slug(doc_name: str) -> str:
    stem = Path(doc_name).stem
    slug = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").upper()
    return slug[:24] or "DOC"


def extract_unmarked(md_text: str, doc_name: str, budget: int | None = None,
                     progress=None) -> tuple[list[ExtractedReq], dict]:
    sections = split_sections(md_text)
    if budget is None:
        env = int(os.getenv("LYNX_CONVERSION_LLM_BUDGET", "0"))
        budget = env if env > 0 else max(10, 2 * len(sections))  # élastique
    stats = {"sections": len(sections), "llm_calls": 0,
             "rejets_verbatim": 0, "sections_non_traitees": 0, "erreurs": 0}
    reqs: list[ExtractedReq] = []
    slug = _doc_slug(doc_name)
    n = 0
    for i, (label, texte) in enumerate(sections):
        if stats["llm_calls"] >= budget:
            stats["sections_non_traitees"] = len(sections) - i
            break
        stats["llm_calls"] += 1
        out = _call_agent(PROMPT_EXTRACTION, texte, label="conversion",
                          schema=_SectionExigences)
        if progress:
            progress(i + 1, len(sections))
        if out.get("error"):
            stats["erreurs"] += 1
            continue
        for t in out.get("exigences", []):
            if _norm(t) and _norm(t) in _norm(texte):   # garde-fou verbatim
                n += 1
                reqs.append(ExtractedReq(
                    id=f"{slug}-AUTO-{n:03d}", texte=t.strip(), doc=doc_name,
                    section=label, famille="AUTO", etage="llm"))
            else:
                stats["rejets_verbatim"] += 1
    return reqs, stats
```

- [ ] **Step 4 : Vérifier** — pytest `tests/conversion/` : tout PASS. Attention au test des ids : `IRS_test.doc` → slug `IRS-TEST`.

- [ ] **Step 5 : Commit**

```bash
git add conversion/extract_llm.py tests/conversion/test_extract_llm.py
git commit -m "feat(conversion): détection LLM verbatim avec budget élastique"
```

---

### Task 4 : Liens explicites (`link_builder.py`)

**Files:**
- Create: `conversion/link_builder.py`
- Test: `tests/conversion/test_link_builder.py`

**Interfaces:**
- Consumes: `extract_marked.TOKEN_RE`, `extract_marked.skeleton_of`, `model.ExtractedReq`
- Produces:
  - `link_builder.matrix_links(rows: list[list[str]], niveau_of: dict[str, int]) -> tuple[list[tuple[str, str]], list[str]]` — paires `(parent_id, enfant_id)` + ids externes non résolus
  - `link_builder.text_reference_links(reqs: list[ExtractedReq], niveau_of: dict[str, int]) -> list[tuple[str, str]]`
  - `link_builder.apply_links(reqs_json: list[dict], links: list[tuple[str, str]]) -> dict` — pose `parent_id` (premier lien gagne) ; renvoie `{"appliques": int, "externes": [ids], "conflits": [[enfant, parent_ignore], …]}`
  - `niveau_of` : squelette de famille → niveau (construit par assemble, Task 5)

- [ ] **Step 1 : Tests qui échouent**

```python
# tests/conversion/test_link_builder.py
"""Matrices structurelles et références inter-documents → parent_id.
Aucun nom de matrice codé en dur : deux familles d'ids dans un tableau
suffisent."""
from conversion.model import ExtractedReq
from conversion.link_builder import matrix_links, text_reference_links, apply_links

NIVEAUX = {"MC-TST-#.#-#": 0, "SSS-STC-E-REQ-#": 1}

ROWS = [
    ["STB", "Texte STB", "ID SSS", "Texte SSS"],
    ["[MC-TST-3.1-3780]", "Les annuaires doivent…",
     "[SSS-STC-E-REQ-0117]\n[SSS-STC-E-REQ-0119]", "L'accès LDAP…"],
    ["[MC-TST-9.9-0001]", "Exigence hors lot", "[EXT-REF-0001]", "…"],
]


def _req(id, famille, niveau, texte=""):
    return ExtractedReq(id=id, texte=texte, doc="d", section="s",
                        famille=famille, etage="marque")


def test_matrice_paires_orientees_par_niveau():
    links, externes = matrix_links(ROWS, NIVEAUX)
    assert ("MC-TST-3.1-3780", "SSS-STC-E-REQ-0117") in links
    assert ("MC-TST-3.1-3780", "SSS-STC-E-REQ-0119") in links


def test_matrice_famille_inconnue_va_aux_externes():
    links, externes = matrix_links(ROWS, NIVEAUX)
    assert "EXT-REF-0001" in externes
    assert all("EXT-REF-0001" not in pair for pair in links)


def test_reference_dans_le_texte():
    reqs = [
        _req("MC-TST-1.1-0001", "MC-TST-#.#-#", 0),
        _req("SSS-STC-E-REQ-0003", "SSS-STC-E-REQ-#", 1,
             texte="La transition suit l'ordre initial (cf. [MC-TST-1.1-0001])."),
    ]
    links = text_reference_links(reqs, NIVEAUX)
    assert links == [("MC-TST-1.1-0001", "SSS-STC-E-REQ-0003")]


def test_reference_vers_niveau_superieur_ignoree():
    # Un parent doit être de niveau STRICTEMENT inférieur.
    reqs = [
        _req("SSS-STC-E-REQ-0001", "SSS-STC-E-REQ-#", 1),
        _req("MC-TST-1.1-0002", "MC-TST-#.#-#", 0,
             texte="Voir [SSS-STC-E-REQ-0001]."),
    ]
    assert text_reference_links(reqs, NIVEAUX) == []


def test_apply_links_premier_gagne_et_externes():
    reqs_json = [
        {"id": "A-1", "parent_id": None},
        {"id": "B-1", "parent_id": None},
    ]
    res = apply_links(reqs_json, [("B-1", "A-1"), ("X-9", "A-1"), ("Z-1", "GHOST")])
    assert reqs_json[0]["parent_id"] == "B-1"          # premier lien gagne
    assert res["appliques"] == 1
    assert "X-9" in res["externes"] and "GHOST" in res["externes"]
    assert res["conflits"] == [["A-1", "X-9"]]
```

- [ ] **Step 2 : Vérifier l'échec** — Expected: FAIL import.

- [ ] **Step 3 : Implémenter**

```python
# conversion/link_builder.py
"""Étage 4 : liens parent→enfant depuis les preuves EXPLICITES uniquement.

Deux sources : (1) matrices — tableau où des cellules d'une même ligne
portent des ids de familles de niveaux différents ; (2) références — id
d'une autre famille cité dans le texte d'une exigence. Le parent est
toujours de niveau strictement inférieur. Pas d'inférence sémantique (V1).
"""
from conversion.extract_marked import TOKEN_RE, skeleton_of
from conversion.model import ExtractedReq


def _ids_in(text: str) -> list[str]:
    return [m.group(0) for m in TOKEN_RE.finditer(text)]


def matrix_links(rows: list[list[str]],
                 niveau_of: dict[str, int]) -> tuple[list[tuple[str, str]], list[str]]:
    links: list[tuple[str, str]] = []
    externes: list[str] = []
    for row in rows:
        by_family: dict[str, list[str]] = {}
        for cell in row:
            for tok in _ids_in(cell):
                sk = skeleton_of(tok)
                if sk in niveau_of:
                    by_family.setdefault(sk, []).append(tok)
                else:
                    externes.append(tok)
        fams = sorted(by_family, key=lambda sk: niveau_of[sk])
        for i, fp in enumerate(fams):
            for fc in fams[i + 1:]:
                if niveau_of[fp] < niveau_of[fc]:
                    for p in by_family[fp]:
                        for c in by_family[fc]:
                            links.append((p, c))
    return links, sorted(set(externes))


def text_reference_links(reqs: list[ExtractedReq],
                         niveau_of: dict[str, int]) -> list[tuple[str, str]]:
    links = []
    for r in reqs:
        child_niv = niveau_of.get(r.famille)
        if child_niv is None:
            continue
        for tok in _ids_in(r.texte):
            sk = skeleton_of(tok)
            if sk != r.famille and niveau_of.get(sk) is not None \
                    and niveau_of[sk] < child_niv:
                links.append((tok, r.id))
    return links


def apply_links(reqs_json: list[dict], links: list[tuple[str, str]]) -> dict:
    """Pose parent_id sur les exigences du lot. Premier lien gagne ; parent
    absent du lot → externe ; second parent différent → conflit signalé."""
    by_id = {r["id"]: r for r in reqs_json}
    externes: set[str] = set()
    conflits: list[list[str]] = []
    n = 0
    for parent, child in links:
        c = by_id.get(child)
        if c is None:
            externes.add(child)
            continue
        if parent not in by_id:
            externes.add(parent)
            continue
        if c.get("parent_id") in (None, ""):
            c["parent_id"] = parent
            n += 1
        elif c["parent_id"] != parent:
            conflits.append([child, parent])
    return {"appliques": n, "externes": sorted(externes), "conflits": conflits}
```

- [ ] **Step 4 : Vérifier** — pytest : tout PASS.
- [ ] **Step 5 : Commit**

```bash
git add conversion/link_builder.py tests/conversion/test_link_builder.py
git commit -m "feat(conversion): liens explicites matrices + références, orientés par niveau"
```

---

### Task 5 : Assemblage (`assemble.py`)

**Files:**
- Create: `conversion/assemble.py`
- Test: `tests/conversion/test_assemble.py`

**Interfaces:**
- Consumes: `link_builder.*`, `model.ExtractedReq` ; `lynx corpus_io.validate_corpus` (import paresseux via `sys.path` comme Task 3)
- Produces: `assemble.assemble(reqs: list[ExtractedReq], tables: dict[str, list[list[str]]], doc_niveau: dict[str, int]) -> tuple[dict, dict]` — `(baseline, report)` :
  - `baseline = {"meta": {"genere_par": "conversion", "date": iso, "mapping_niveaux": doc_niveau, "stats": {...}}, "exigences": [dict Requirement…]}`
  - chaque exigence : `{id, niveau, type, texte, parent_id, test_status: "PENDING", source: "<doc> §<section>"}` avec `type` = `"Exigence"` (marque) ou `"Exigence (détectée)"` (llm)
  - `report = {"documents": {doc: {"n_marquees": int, "n_llm": int, "familles": {skeleton: count}}}, "liens": {"appliques", "externes", "conflits"}, "doublons": [ids], "erreurs_validation": [str]}`
  - `assemble.famille_niveaux(reqs, doc_niveau) -> dict[str, int]` (squelette → niveau, depuis le doc où la famille est définie)

- [ ] **Step 1 : Tests qui échouent**

```python
# tests/conversion/test_assemble.py
from conversion.model import ExtractedReq
from conversion.assemble import assemble, famille_niveaux


def _req(id, doc, famille, etage="marque", texte="Un texte.", section="1 S"):
    return ExtractedReq(id=id, texte=texte, doc=doc, section=section,
                        famille=famille, etage=etage)


REQS = [
    _req("MC-TST-1.1-0001", "STB.doc", "MC-TST-#.#-#"),
    _req("SSS-REQ-0001", "SSS.doc", "SSS-REQ-#",
         texte="Décline [MC-TST-1.1-0001]."),
    _req("SSS-REQ-0002", "SSS.doc", "SSS-REQ-#"),
    _req("IRS-X-AUTO-001", "IRS.doc", "AUTO", etage="llm"),
]
NIVEAUX_DOCS = {"STB.doc": 0, "SSS.doc": 1, "IRS.doc": 2}


def test_famille_niveaux_depuis_les_docs():
    fn = famille_niveaux(REQS, NIVEAUX_DOCS)
    assert fn == {"MC-TST-#.#-#": 0, "SSS-REQ-#": 1, "AUTO": 2}


def test_assemble_champs_requirement():
    baseline, report = assemble(REQS, tables={}, doc_niveau=NIVEAUX_DOCS)
    ex = {e["id"]: e for e in baseline["exigences"]}
    assert len(ex) == 4
    assert ex["SSS-REQ-0001"]["niveau"] == 1
    assert ex["SSS-REQ-0001"]["parent_id"] == "MC-TST-1.1-0001"  # réf. texte
    assert ex["SSS-REQ-0001"]["source"] == "SSS.doc §1 S"
    assert ex["IRS-X-AUTO-001"]["type"] == "Exigence (détectée)"
    assert ex["MC-TST-1.1-0001"]["type"] == "Exigence"
    assert all(e["test_status"] == "PENDING" for e in baseline["exigences"])
    assert baseline["meta"]["genere_par"] == "conversion"


def test_assemble_matrice_et_rapport():
    tables = {"DJEM.xls": [["[MC-TST-1.1-0001]", "[SSS-REQ-0002]"]]}
    baseline, report = assemble(REQS, tables=tables, doc_niveau=NIVEAUX_DOCS)
    ex = {e["id"]: e for e in baseline["exigences"]}
    assert ex["SSS-REQ-0002"]["parent_id"] == "MC-TST-1.1-0001"
    assert report["documents"]["SSS.doc"]["n_marquees"] == 2
    assert report["documents"]["IRS.doc"]["n_llm"] == 1


def test_doublons_dedupliques_et_nommes():
    reqs = REQS + [_req("SSS-REQ-0001", "SSS_v2.doc", "SSS-REQ-#")]
    niveaux = dict(NIVEAUX_DOCS, **{"SSS_v2.doc": 1})
    baseline, report = assemble(reqs, tables={}, doc_niveau=niveaux)
    assert len([e for e in baseline["exigences"] if e["id"] == "SSS-REQ-0001"]) == 1
    assert any("SSS-REQ-0001" in d for d in report["doublons"])
```

- [ ] **Step 2 : Vérifier l'échec** — FAIL import.

- [ ] **Step 3 : Implémenter**

```python
# conversion/assemble.py
"""Fusion des extractions + liens explicites + validation LynX.

Sortie : baseline enveloppée {meta, exigences} conforme au schéma
Requirement, et rapport par document pour la revue (glass box).
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from conversion.link_builder import apply_links, matrix_links, text_reference_links
from conversion.model import ExtractedReq

ROOT = Path(__file__).resolve().parent.parent


def _validate(raw: list[dict]) -> tuple[list[dict], list[str]]:
    lynx_dir = str(ROOT / "lynx")
    if lynx_dir not in sys.path:
        sys.path.insert(0, lynx_dir)
    from src.corpus_io import validate_corpus
    return validate_corpus(raw)


def famille_niveaux(reqs: list[ExtractedReq], doc_niveau: dict[str, int]) -> dict:
    """Niveau d'une famille = niveau du document où elle est définie."""
    fn: dict[str, int] = {}
    for r in reqs:
        niv = doc_niveau.get(r.doc)
        if niv is not None and r.famille not in fn:
            fn[r.famille] = niv
    return fn


def assemble(reqs: list[ExtractedReq], tables: dict,
             doc_niveau: dict[str, int]) -> tuple[dict, dict]:
    niveau_of = famille_niveaux(reqs, doc_niveau)

    reqs_json = [{
        "id": r.id,
        "niveau": doc_niveau.get(r.doc, 0),
        "type": "Exigence (détectée)" if r.etage == "llm" else "Exigence",
        "texte": r.texte,
        "parent_id": None,
        "test_status": "PENDING",
        "source": f"{r.doc} §{r.section}" if r.section else r.doc,
    } for r in reqs]

    # Doublons inter-documents : la validation LynX déduplique ; on les nomme.
    seen, doublons = set(), []
    for r in reqs:
        if r.id in seen:
            doublons.append(f"{r.id} (revu dans {r.doc})")
        seen.add(r.id)

    links: list[tuple[str, str]] = []
    externes_matrices: list[str] = []
    for rows in tables.values():
        l, ext = matrix_links(rows, niveau_of)
        links.extend(l)
        externes_matrices.extend(ext)
    links.extend(text_reference_links(reqs, niveau_of))
    lien_res = apply_links(reqs_json, links)
    lien_res["externes"] = sorted(set(lien_res["externes"]) | set(externes_matrices))

    valides, erreurs = _validate(reqs_json)

    docs_report: dict[str, dict] = {}
    for r in reqs:
        d = docs_report.setdefault(r.doc, {"n_marquees": 0, "n_llm": 0, "familles": {}})
        d["n_marquees" if r.etage == "marque" else "n_llm"] += 1
        if r.famille != "AUTO":
            d["familles"][r.famille] = d["familles"].get(r.famille, 0) + 1

    baseline = {
        "meta": {
            "genere_par": "conversion",
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mapping_niveaux": doc_niveau,
            "stats": {"n_exigences": len(valides),
                      "n_liens": lien_res["appliques"],
                      "n_docs": len(doc_niveau)},
        },
        "exigences": valides,
    }
    report = {"documents": docs_report, "liens": lien_res,
              "doublons": doublons, "erreurs_validation": erreurs}
    return baseline, report
```

- [ ] **Step 4 : Vérifier** — pytest `tests/conversion/` puis suite complète : tout PASS.
- [ ] **Step 5 : Commit**

```bash
git add conversion/assemble.py tests/conversion/test_assemble.py
git commit -m "feat(conversion): assemblage baseline + rapport, validation LynX"
```

---

### Task 6 : File de jobs (`queue.py`)

**Files:**
- Create: `conversion/queue.py`
- Test: `tests/conversion/test_queue.py`

**Interfaces:**
- Consumes: tous les modules Tasks 1–5.
- Produces:
  - `queue.CONV_DIR = ROOT / "data" / "conversions"`
  - `queue.create_job(files: list[dict], doc_niveau: dict[str, int], rag_index: bool, start: bool = True) -> str` — `files` : `[{"name": str, "path": str}]` ; retourne `job_id` ; `start=False` crée sans réveiller le worker (l'API écrit d'abord les sources puis appelle `start_job`)
  - `queue.start_job(job_id: str) -> None` — réveille le worker
  - `queue.get_job(job_id: str) -> dict | None` — état complet `{id, status ("queued"|"running"|"success"|"error"), files, doc_niveau, rag_index, events: list, error, dir}` ; recharge depuis `data/conversions/<id>/job.json` si absent de la mémoire
  - `queue.events_since(job_id: str, i: int) -> list[dict]` — événements d'indice ≥ i ; événement : `{"i": int, "type": str, ...}` ; types émis : `etape` (`{doc, etage, detail}`), `doc_fini` (`{doc, n_marquees, n_llm, warnings, error}`), `fini` (`{status}`)
  - `queue.redo_doc(job_id: str, doc_name: str, familles: list[str]) -> dict` — ré-extrait UN document depuis son Markdown persisté avec les familles imposées (liste de squelettes ; vide → détection LLM), ré-assemble, réécrit baseline/report ; renvoie le rapport du doc
  - Persistance : `data/conversions/<id>/{job.json, baseline.json, report.json, md/}`

- [ ] **Step 1 : Tests qui échouent** (worker exécuté en ligne : on appelle `_process_job` directement pour rester déterministe, comme les tests d'`ingest_queue`)

```python
# tests/conversion/test_queue.py
"""Cycle de vie d'un job : traitement séquentiel, événements, persistance,
redo par document. LLM et Docling mockés."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import conversion.queue as cq
from conversion.model import NormalizedDoc

DOC_MD = """## 1 Exigences
REQ_AA_01
Le système doit démarrer.
REQ_AA_02
Le système doit s'arrêter.
REQ_AA_03
Le système doit journaliser.
"""


@pytest.fixture(autouse=True)
def isole_conv_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cq, "CONV_DIR", tmp_path / "conversions")
    # Pas de thread worker dans les tests : _process_job est appelé en direct.
    monkeypatch.setattr(cq, "_ensure_worker", lambda: None)
    cq._JOBS.clear()
    yield


def _make_files(tmp_path):
    src = tmp_path / "in.md"
    src.write_text(DOC_MD, encoding="utf-8")
    return [{"name": "in.md", "path": str(src)}]


def test_job_success_events_et_persistance(tmp_path):
    job_id = cq.create_job(_make_files(tmp_path), {"in.md": 1}, rag_index=False)
    cq._process_job(cq.get_job(job_id))
    job = cq.get_job(job_id)
    assert job["status"] == "success"
    types = [e["type"] for e in job["events"]]
    assert "doc_fini" in types and types[-1] == "fini"
    jdir = Path(job["dir"])
    baseline = json.loads((jdir / "baseline.json").read_text(encoding="utf-8"))
    assert len(baseline["exigences"]) == 3
    report = json.loads((jdir / "report.json").read_text(encoding="utf-8"))
    assert report["documents"]["in.md"]["n_marquees"] == 3


def test_echec_dun_doc_naborte_pas_le_lot(tmp_path):
    files = _make_files(tmp_path) + [{"name": "casse.doc", "path": str(tmp_path / "absent.doc")}]
    with patch("conversion.queue.to_markdown",
               side_effect=[NormalizedDoc(name="in.md", kind="markdown",
                                          md_path=Path(files[0]["path"])),
                            NormalizedDoc(name="casse.doc", kind="echec",
                                          error="LibreOffice introuvable")]):
        job_id = cq.create_job(files, {"in.md": 1, "casse.doc": 1}, rag_index=False)
        cq._process_job(cq.get_job(job_id))
    job = cq.get_job(job_id)
    assert job["status"] == "success"      # le lot survit
    doc_evts = {e["doc"]: e for e in job["events"] if e["type"] == "doc_fini"}
    assert doc_evts["casse.doc"]["error"] != ""


def test_events_since(tmp_path):
    job_id = cq.create_job(_make_files(tmp_path), {"in.md": 1}, rag_index=False)
    cq._process_job(cq.get_job(job_id))
    evts = cq.events_since(job_id, 0)
    assert evts and cq.events_since(job_id, evts[-1]["i"] + 1) == []


def test_get_job_recharge_depuis_disque(tmp_path):
    job_id = cq.create_job(_make_files(tmp_path), {"in.md": 1}, rag_index=False)
    cq._process_job(cq.get_job(job_id))
    cq._JOBS.clear()                      # simule un redémarrage serveur
    job = cq.get_job(job_id)
    assert job is not None and job["status"] == "success"


def test_redo_doc_avec_familles_imposees(tmp_path):
    job_id = cq.create_job(_make_files(tmp_path), {"in.md": 1}, rag_index=False)
    cq._process_job(cq.get_job(job_id))
    # Désactivation de la famille détectée → plus aucune exigence marquée ;
    # familles=[] → bascule détection LLM (mockée : rien trouvé).
    with patch("conversion.queue.extract_unmarked", return_value=([], {
            "sections": 1, "llm_calls": 1, "rejets_verbatim": 0,
            "sections_non_traitees": 0, "erreurs": 0})):
        doc_report = cq.redo_doc(job_id, "in.md", familles=[])
    assert doc_report["n_marquees"] == 0 and doc_report["n_llm"] == 0
    jdir = Path(cq.get_job(job_id)["dir"])
    baseline = json.loads((jdir / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["exigences"] == []
```

- [ ] **Step 2 : Vérifier l'échec** — FAIL import.

- [ ] **Step 3 : Implémenter**

```python
# conversion/queue.py
"""File de conversion SÉQUENTIELLE, indépendante de la file RAG.

Même pattern qu'ingest_queue (worker unique, jobs en mémoire) + persistance
disque par job (data/conversions/<id>/) : la revue reste consultable après
fermeture de l'onglet ou redémarrage du serveur.
"""
import json
import threading
import uuid
from pathlib import Path

from conversion.assemble import assemble
from conversion.extract_llm import extract_unmarked
from conversion.extract_marked import detect_id_families, extract_marked
from conversion.model import ExtractedReq
from conversion.normalize import to_markdown

ROOT = Path(__file__).resolve().parent.parent
CONV_DIR = ROOT / "data" / "conversions"

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_WORKER_ALIVE = False


def _emit(job: dict, type_: str, **data) -> None:
    with _LOCK:
        job["events"].append({"i": len(job["events"]), "type": type_, **data})
    _save(job)


def _save(job: dict) -> None:
    jdir = Path(job["dir"])
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "job.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")


def create_job(files: list[dict], doc_niveau: dict, rag_index: bool,
               start: bool = True) -> str:
    """start=False : le job est créé sans réveiller le worker — l'appelant
    (API) écrit d'abord les fichiers sources puis appelle start_job()."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "queued", "files": files,
           "doc_niveau": doc_niveau, "rag_index": rag_index,
           "events": [], "error": "", "dir": str(CONV_DIR / job_id)}
    with _LOCK:
        _JOBS[job_id] = job
    _save(job)
    if start:
        _ensure_worker()
    return job_id


def start_job(job_id: str) -> None:
    """Réveille le worker (jobs créés avec start=False)."""
    _ensure_worker()


def get_job(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        return job
    jpath = CONV_DIR / job_id / "job.json"
    if jpath.exists():
        job = json.loads(jpath.read_text(encoding="utf-8"))
        with _LOCK:
            _JOBS[job_id] = job
        return job
    return None


def events_since(job_id: str, i: int) -> list[dict]:
    job = get_job(job_id)
    if job is None:
        return []
    with _LOCK:
        return [e for e in job["events"] if e["i"] >= i]


def _extract_doc(job: dict, name: str, md_text: str,
                 familles_imposees: list[str] | None = None) -> tuple[list, dict]:
    """Extraction d'un document : marquée si familles définies, sinon LLM.
    familles_imposees (redo) : squelettes à garder ; [] force la voie LLM."""
    fams = detect_id_families(md_text)
    if familles_imposees is not None:
        fams = [f for f in fams if f.skeleton in familles_imposees]
    defined = [f for f in fams if f.n_definitions > 0]
    if defined:
        reqs = extract_marked(md_text, name, defined)
        return reqs, {"n_marquees": len(reqs), "n_llm": 0,
                      "familles": {f.skeleton: f.count for f in defined}}
    def prog(done, total):
        _emit(job, "etape", doc=name, etage="llm",
              detail=f"détection LLM {done}/{total} sections")
    reqs, stats = extract_unmarked(md_text, name, progress=prog)
    return reqs, {"n_marquees": 0, "n_llm": len(reqs), "familles": {},
                  "stats_llm": stats}


def _process_job(job: dict) -> None:
    job["status"] = "running"
    _save(job)
    jdir = Path(job["dir"])
    md_dir = jdir / "md"
    all_reqs: list[ExtractedReq] = []
    tables: dict[str, list] = {}
    try:
        for f in job["files"]:
            name = f["name"]
            _emit(job, "etape", doc=name, etage="normalisation", detail="conversion…")
            nd = to_markdown(Path(f["path"]), md_dir)
            if nd.kind == "echec":
                _emit(job, "doc_fini", doc=name, n_marquees=0, n_llm=0,
                      warnings=[], error=nd.error)
                continue
            if nd.kind == "table":
                tables[name] = nd.rows
                _emit(job, "doc_fini", doc=name, n_marquees=0, n_llm=0,
                      warnings=["tableur : exploité comme matrice de liens"], error="")
                continue
            md_text = nd.md_path.read_text(encoding="utf-8")
            (md_dir / f"{Path(name).stem}.extracted.md").write_text(
                md_text, encoding="utf-8")   # source du redo
            _emit(job, "etape", doc=name, etage="extraction", detail="extraction…")
            reqs, doc_stats = _extract_doc(job, name, md_text)
            all_reqs.extend(reqs)
            _emit(job, "doc_fini", doc=name, n_marquees=doc_stats["n_marquees"],
                  n_llm=doc_stats["n_llm"], warnings=nd.warnings, error="")
        baseline, report = assemble(all_reqs, tables, job["doc_niveau"])
        (jdir / "baseline.json").write_text(
            json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
        (jdir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        job["status"] = "success"
        if job.get("rag_index"):
            _push_rag(job, md_dir)
    except Exception as exc:
        job["status"], job["error"] = "error", str(exc)
    _emit(job, "fini", status=job["status"])


def _push_rag(job: dict, md_dir: Path) -> None:
    """Passerelle RAG : les Markdown produits partent en file d'ingestion."""
    from core import ingest_queue
    items = [{"name": p.name, "path": str(p)}
             for p in sorted(md_dir.glob("*-clean.md")) or sorted(md_dir.glob("*.extracted.md"))]
    if items:
        ingest_queue.enqueue(items, ingest_queue.default_params())
        _emit(job, "etape", doc="(lot)", etage="rag",
              detail=f"{len(items)} documents envoyés à l'ingestion RAG")


def redo_doc(job_id: str, doc_name: str, familles: list[str]) -> dict:
    """Ré-extrait UN document (depuis son Markdown persisté) avec des familles
    imposées, ré-assemble le lot, réécrit baseline/report."""
    job = get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    jdir = Path(job["dir"])
    md_path = jdir / "md" / f"{Path(doc_name).stem}.extracted.md"
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown persisté introuvable pour {doc_name}")
    report = json.loads((jdir / "report.json").read_text(encoding="utf-8"))
    baseline = json.loads((jdir / "baseline.json").read_text(encoding="utf-8"))

    md_text = md_path.read_text(encoding="utf-8")
    reqs, doc_stats = _extract_doc(job, doc_name, md_text, familles_imposees=familles)

    # Reconstruit le lot : exigences des autres docs (depuis la baseline,
    # dépouillées en ExtractedReq) + nouvelles exigences de ce doc.
    others = [ExtractedReq(
        id=e["id"], texte=e["texte"], doc=e["source"].split(" §")[0],
        section=e["source"].split(" §")[1] if " §" in e["source"] else "",
        famille="AUTO" if "détectée" in e["type"] else "?",
        etage="llm" if "détectée" in e["type"] else "marque",
    ) for e in baseline["exigences"]
        if e["source"].split(" §")[0] != doc_name]
    # Les familles des autres docs se recalculent depuis leurs ids :
    from conversion.extract_marked import skeleton_of
    for r in others:
        if r.famille == "?":
            r.famille = skeleton_of(r.id)
    tables_path = jdir / "tables.json"
    tables = json.loads(tables_path.read_text(encoding="utf-8")) if tables_path.exists() else {}
    new_baseline, new_report = assemble(others + reqs, tables, job["doc_niveau"])
    (jdir / "baseline.json").write_text(
        json.dumps(new_baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    new_report["documents"].setdefault(doc_name, {"n_marquees": 0, "n_llm": 0,
                                                  "familles": {}})
    (jdir / "report.json").write_text(
        json.dumps(new_report, ensure_ascii=False, indent=1), encoding="utf-8")
    _emit(job, "doc_fini", doc=doc_name, n_marquees=doc_stats["n_marquees"],
          n_llm=doc_stats["n_llm"], warnings=["re-extraction"], error="")
    return new_report["documents"][doc_name]


def _worker() -> None:
    global _WORKER_ALIVE
    while True:
        with _LOCK:
            pending = [j for j in _JOBS.values() if j["status"] == "queued"]
            if not pending:
                _WORKER_ALIVE = False
                return
            job = pending[0]
        _process_job(job)


def _ensure_worker() -> None:
    global _WORKER_ALIVE
    with _LOCK:
        if _WORKER_ALIVE:
            return
        _WORKER_ALIVE = True
    threading.Thread(target=_worker, daemon=True).start()
```

Note d'implémentation : `_process_job` doit aussi persister `tables` (`tables.json` dans le dossier du job) juste avant `assemble(...)`, pour que `redo_doc` les retrouve :

```python
        (jdir / "tables.json").write_text(
            json.dumps(tables, ensure_ascii=False), encoding="utf-8")
```

- [ ] **Step 4 : Vérifier** — pytest `tests/conversion/` : tout PASS (les tests appellent `_process_job` en direct, pas le thread).
- [ ] **Step 5 : Commit**

```bash
git add conversion/queue.py tests/conversion/test_queue.py
git commit -m "feat(conversion): file de jobs persistée, redo par document, passerelle RAG"
```

---

### Task 7 : API FastAPI (`api/conversion.py`)

**Files:**
- Create: `api/conversion.py`
- Modify: `api/main.py` (import + `include_router`, après `documents_router`)
- Test: `tests/conversion/test_api.py`

**Interfaces:**
- Consumes: `conversion.queue` (Task 6), `conversion.normalize.ACCEPTED_EXTS`
- Produces (endpoints) :
  - `POST /api/conversion/jobs` — multipart `files` + Form `mapping` (JSON `{filename: niveau}`) + Form `rag_index` (bool) → `{"job_id": str}` ; 400 si extension refusée (message : `Type non accepté : <nom> (.vsd, .zip… non supportés en V1)`)
  - `GET /api/conversion/jobs/{id}` → `{job (sans events), report, baseline_n}` ; `report` inclut les exigences (`baseline["exigences"]`) pour la table de revue ; 404 si inconnu
  - `GET /api/conversion/jobs/{id}/events?since=N` → SSE (`text/event-stream`), une trame JSON par événement, se termine par l'événement `fini`
  - `GET /api/conversion/jobs/{id}/baseline` → `FileResponse` du `baseline.json`
  - `POST /api/conversion/jobs/{id}/redo` — body `{"doc": str, "familles": [str]}` → rapport du doc

- [ ] **Step 1 : Tests qui échouent** (TestClient FastAPI ; worker traité en synchrone via monkeypatch de `_ensure_worker`)

```python
# tests/conversion/test_api.py
"""Cycle API complet sur mini-lot : upload → traitement → état → baseline.
Le worker est court-circuité : traitement synchrone dans le test."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import conversion.queue as cq
from api.main import app

client = TestClient(app)

DOC_MD = b"""## 1 Exigences
REQ_TT_01
Le service doit répondre en moins d'une seconde.
REQ_TT_02
Le service doit tracer les erreurs.
REQ_TT_03
Le service doit redémarrer seul.
"""


@pytest.fixture(autouse=True)
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(cq, "CONV_DIR", tmp_path / "conversions")
    cq._JOBS.clear()
    # Worker synchrone : create_job n'ouvre pas de thread pendant les tests.
    monkeypatch.setattr(cq, "_ensure_worker", lambda: None)
    yield


def _upload():
    r = client.post("/api/conversion/jobs",
                    files=[("files", ("spec.md", DOC_MD, "text/markdown"))],
                    data={"mapping": json.dumps({"spec.md": 1}),
                          "rag_index": "false"})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def test_cycle_complet():
    job_id = _upload()
    cq._process_job(cq.get_job(job_id))
    r = client.get(f"/api/conversion/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["job"]["status"] == "success"
    assert body["baseline_n"] == 3
    assert len(body["report"]["exigences"]) == 3
    rb = client.get(f"/api/conversion/jobs/{job_id}/baseline")
    assert rb.status_code == 200
    assert len(json.loads(rb.content)["exigences"]) == 3


def test_extension_refusee_avec_motif():
    r = client.post("/api/conversion/jobs",
                    files=[("files", ("schema.vsd", b"x", "application/octet-stream"))],
                    data={"mapping": "{}", "rag_index": "false"})
    assert r.status_code == 400 and "non accepté" in r.json()["detail"]


def test_job_inconnu_404():
    assert client.get("/api/conversion/jobs/zzz").status_code == 404


def test_sse_stream_termine_par_fini():
    job_id = _upload()
    cq._process_job(cq.get_job(job_id))
    with client.stream("GET", f"/api/conversion/jobs/{job_id}/events") as r:
        frames = [json.loads(l[len("data: "):]) for l in r.iter_lines()
                  if l.startswith("data: ")]
    assert frames[-1]["type"] == "fini"


def test_redo_endpoint():
    job_id = _upload()
    cq._process_job(cq.get_job(job_id))
    with patch("conversion.queue.extract_unmarked",
               return_value=([], {"sections": 1, "llm_calls": 0,
                                  "rejets_verbatim": 0,
                                  "sections_non_traitees": 0, "erreurs": 0})):
        r = client.post(f"/api/conversion/jobs/{job_id}/redo",
                        json={"doc": "spec.md", "familles": []})
    assert r.status_code == 200 and r.json()["n_marquees"] == 0
```

- [ ] **Step 2 : Vérifier l'échec** — FAIL (routes 404 : routeur absent).

- [ ] **Step 3 : Implémenter**

```python
# api/conversion.py
"""Conversion documents d'exigences → baseline JSON (page LynX Conversion).

Le job est traité par la file conversion (thread unique) ; l'UI suit la
progression en SSE puis charge le rapport pour la revue. L'envoi vers le
corpus LynX se fait côté client via /corpus/upload (JSON édité en revue).
"""
import json
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from conversion import queue as conv_queue
from conversion.normalize import ACCEPTED_EXTS

router = APIRouter()


@router.post("/api/conversion/jobs")
async def create_job(files: list[UploadFile] = File(...),
                     mapping: str = Form("{}"),
                     rag_index: bool = Form(False)) -> dict:
    try:
        doc_niveau = {k: int(v) for k, v in json.loads(mapping).items()}
    except Exception:
        raise HTTPException(400, "mapping : JSON {nom_fichier: niveau} attendu.")
    items = []
    for up in files:
        name = (up.filename or "document").replace("/", "_").replace("\\", "_")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ACCEPTED_EXTS:
            raise HTTPException(400, f"Type non accepté : {name} "
                                     "(.vsd, .zip… non supportés en V1)")
        items.append({"name": name, "data": await up.read()})
    if not items:
        raise HTTPException(400, "Aucun fichier fourni.")
    # Le job est créé SANS réveiller le worker (start=False) : on écrit
    # d'abord les fichiers sources sous le dossier du job, puis start_job —
    # sinon le worker pourrait partir sur des chemins vides.
    job_id = conv_queue.create_job(
        [{"name": it["name"], "path": ""} for it in items],
        doc_niveau, rag_index, start=False)
    job = conv_queue.get_job(job_id)
    in_dir = Path(job["dir"]) / "in"
    in_dir.mkdir(parents=True, exist_ok=True)
    for it, f in zip(items, job["files"]):
        p = in_dir / it["name"]
        p.write_bytes(it["data"])
        f["path"] = str(p)
    conv_queue._save(job)
    conv_queue.start_job(job_id)
    return {"job_id": job_id}


@router.get("/api/conversion/jobs/{job_id}")
def job_state(job_id: str) -> dict:
    job = conv_queue.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job inconnu.")
    jdir = Path(job["dir"])
    report, exigences, n = {}, [], 0
    if (jdir / "report.json").exists():
        report = json.loads((jdir / "report.json").read_text(encoding="utf-8"))
    if (jdir / "baseline.json").exists():
        baseline = json.loads((jdir / "baseline.json").read_text(encoding="utf-8"))
        exigences, n = baseline["exigences"], len(baseline["exigences"])
    report["exigences"] = exigences
    public = {k: v for k, v in job.items() if k != "events"}
    return {"job": public, "report": report, "baseline_n": n}


@router.get("/api/conversion/jobs/{job_id}/events")
def job_events(job_id: str, since: int = 0) -> StreamingResponse:
    if conv_queue.get_job(job_id) is None:
        raise HTTPException(404, "Job inconnu.")

    def gen():
        i = since
        while True:
            evts = conv_queue.events_since(job_id, i)
            for e in evts:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                i = e["i"] + 1
                if e["type"] == "fini":
                    return
            time.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.get("/api/conversion/jobs/{job_id}/baseline")
def job_baseline(job_id: str) -> FileResponse:
    job = conv_queue.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job inconnu.")
    path = Path(job["dir"]) / "baseline.json"
    if not path.exists():
        raise HTTPException(404, "Baseline pas encore produite.")
    return FileResponse(path, media_type="application/json",
                        filename=f"baseline-{job_id}.json")


class RedoBody(BaseModel):
    doc: str
    familles: list[str]


@router.post("/api/conversion/jobs/{job_id}/redo")
def job_redo(job_id: str, body: RedoBody) -> dict:
    try:
        return conv_queue.redo_doc(job_id, body.doc, body.familles)
    except KeyError:
        raise HTTPException(404, "Job inconnu.")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
```

Dans `api/main.py`, ajouter (avec les autres imports paresseux puis registrations) :

```python
from api.conversion import router as conversion_router  # noqa: E402
app.include_router(conversion_router)
```

- [ ] **Step 4 : Vérifier** — pytest `tests/conversion/test_api.py -v` puis suite complète : tout PASS.
- [ ] **Step 5 : Commit**

```bash
git add api/conversion.py api/main.py tests/conversion/test_api.py
git commit -m "feat(conversion): endpoints jobs/SSE/baseline/redo"
```

---

### Task 8 : Éval golden (`evals/run_conversion_eval.py`)

**Files:**
- Create: `evals/run_conversion_eval.py`
- Create (par exécution `--freeze`) : `evals/conversion_golden.json`

**Interfaces:**
- Consumes: `conversion.normalize.to_markdown`, `conversion.extract_marked.*`
- Produces: script CLI :
  - `--freeze` : parcourt `docs/Référentiel_système`, produit le golden `{doc: {"familles": {skeleton: n_definitions}, "n_marquees": int, "echantillon": {id: texte}}}` (échantillon = 1re, médiane, dernière exigence de chaque doc marqué)
  - sans option : rejoue et compare au golden ; sortie `OK doc (n)` / `ECART doc : attendu X, obtenu Y` ; code retour ≠ 0 si écart
  - `--llm <doc>` : mesure le rappel de la détection LLM sur un doc non marqué contre les entrées `echantillon_llm` du golden (renseignées à la main après revue humaine)
  - Pas de LLM par défaut (extraction marquée seule) : l'éval de masse reste rapide et déterministe.

- [ ] **Step 1 : Écrire le script** (pas de TDD ici : c'est l'outil de mesure lui-même ; il est validé par sa première exécution supervisée)

```python
# evals/run_conversion_eval.py
"""Éval golden de la conversion : rejoue l'extraction déterministe sur le
référentiel réel et compare au golden figé.

  python evals/run_conversion_eval.py --freeze   # (re)génère le golden
  python evals/run_conversion_eval.py            # vérifie (code 1 si écart)
  python evals/run_conversion_eval.py --llm IRS_62449617_506_K(OPSIC).doc

Le golden se fige après revue humaine : un --freeze n'est PAS une validation,
c'est une photographie ; relire le diff git avant de commiter.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conversion.extract_marked import detect_id_families, extract_marked  # noqa: E402
from conversion.normalize import to_markdown  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "Référentiel_système"
GOLDEN = ROOT / "evals" / "conversion_golden.json"
WORK = ROOT / "data" / "conversion_eval"


def _extract_all() -> dict:
    out = {}
    for path in sorted(CORPUS.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".doc", ".docx", ".pdf", ".md"):
            continue
        nd = to_markdown(path, WORK / path.stem)
        if nd.kind != "markdown":
            out[path.name] = {"erreur": nd.error or nd.kind}
            continue
        md = nd.md_path.read_text(encoding="utf-8")
        fams = [f for f in detect_id_families(md) if f.n_definitions > 0]
        reqs = extract_marked(md, path.name, fams)
        ech = {}
        if reqs:
            for r in (reqs[0], reqs[len(reqs) // 2], reqs[-1]):
                ech[r.id] = r.texte
        out[path.name] = {
            "familles": {f.skeleton: f.n_definitions for f in fams},
            "n_marquees": len(reqs), "echantillon": ech,
        }
        print(f"  {path.name}: {len(reqs)} exigences marquées")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--llm", metavar="DOC")
    args = ap.parse_args()

    if args.llm:
        return _eval_llm(args.llm)

    actual = _extract_all()
    if args.freeze:
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        print(f"Golden figé : {GOLDEN} — relire le diff avant commit.")
        return 0

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    ecarts = 0
    for doc, g in golden.items():
        a = actual.get(doc, {})
        if a.get("n_marquees") != g.get("n_marquees"):
            print(f"ECART {doc} : attendu {g.get('n_marquees')}, "
                  f"obtenu {a.get('n_marquees')}")
            ecarts += 1
            continue
        for rid, texte in g.get("echantillon", {}).items():
            if a.get("echantillon", {}).get(rid) != texte:
                print(f"ECART {doc} : texte de {rid} a changé")
                ecarts += 1
        print(f"OK {doc} ({g.get('n_marquees')})")
    return 1 if ecarts else 0


def _eval_llm(doc_name: str) -> int:
    """Rappel de la détection LLM contre le golden humain (echantillon_llm)."""
    from conversion.extract_llm import extract_unmarked
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    attendu = golden.get(doc_name, {}).get("echantillon_llm", [])
    if not attendu:
        print(f"Pas d'echantillon_llm pour {doc_name} dans le golden "
              "(à renseigner à la main après revue).")
        return 1
    path = next(CORPUS.rglob(doc_name))
    nd = to_markdown(path, WORK / path.stem)
    md = nd.md_path.read_text(encoding="utf-8")
    reqs, stats = extract_unmarked(md, doc_name)
    trouves = {" ".join(r.texte.split()) for r in reqs}
    hits = sum(1 for t in attendu if " ".join(t.split()) in trouves)
    print(f"Rappel LLM {doc_name} : {hits}/{len(attendu)} — stats {stats}")
    return 0 if hits == len(attendu) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2 : Première exécution supervisée (freeze)**

Run: `~/Documents/rag_project/.venv/bin/python evals/run_conversion_eval.py --freeze`
Expected: la SSS `SSS_61563710_305_2_Z.doc` doit afficher **817 exigences marquées** (mesuré au brainstorming). Si ce n'est pas 817, investiguer `extract_marked` AVANT de figer. Durée : plusieurs minutes (46 .doc × LibreOffice + Docling).

- [ ] **Step 3 : Vérifier le rejeu**

Run: `~/Documents/rag_project/.venv/bin/python evals/run_conversion_eval.py`
Expected: `OK` sur tous les docs, code retour 0.

- [ ] **Step 4 : Commit**

```bash
git add evals/run_conversion_eval.py evals/conversion_golden.json
git commit -m "feat(conversion): éval golden sur le référentiel réel (SSS 817 exigences)"
```

---

### Task 9 : Front — types, client API, SSE, onglet

**Files:**
- Modify: `web/src/lib/types.ts` (ajouts en fin de fichier), `web/src/lib/api.ts` (ajouts), `web/src/lib/sse.ts` (ajout d'une fonction), `web/src/app/requirements/page.tsx` (onglet)
- Create: `web/src/components/conversion/index.tsx` (coquille provisoire)

**Interfaces:**
- Consumes: endpoints Task 7 ; `API_BASE` (existant dans `api.ts`)
- Produces (utilisés par Tasks 10–12) :
  - types `ConvEvent`, `ConvDocReport`, `ConvExigence`, `ConvJobState`
  - `api.createConversionJob(files: File[], mapping: Record<string, number>, ragIndex: boolean): Promise<{job_id: string}>`
  - `api.getConversionJob(jobId: string): Promise<ConvJobState>`
  - `api.conversionBaselineUrl(jobId: string): string`
  - `api.redoConversionDoc(jobId: string, doc: string, familles: string[]): Promise<ConvDocReport>`
  - `sse.streamConversionEvents(jobId: string, onEvent: (e: ConvEvent) => void, signal?: AbortSignal): Promise<void>`
  - Onglet `"conversion"` dans `LynxTab` + rendu `<ConversionTab active={…}/>`

- [ ] **Step 1 : Ajouter les types**

```ts
// web/src/lib/types.ts — à la fin
/** Conversion documents → baseline JSON. */
export type ConvEvent =
  | { i: number; type: "etape"; doc: string; etage: string; detail: string }
  | { i: number; type: "doc_fini"; doc: string; n_marquees: number;
      n_llm: number; warnings: string[]; error: string }
  | { i: number; type: "fini"; status: "success" | "error" };

export type ConvExigence = {
  id: string; niveau: number; type: string; texte: string;
  parent_id: string | null; test_status: string; source: string;
};

export type ConvDocReport = {
  n_marquees: number; n_llm: number; familles: Record<string, number>;
};

export type ConvJobState = {
  job: { id: string; status: "queued" | "running" | "success" | "error";
         files: { name: string }[]; doc_niveau: Record<string, number>;
         rag_index: boolean; error: string };
  report: { documents?: Record<string, ConvDocReport>;
            liens?: { appliques: number; externes: string[]; conflits: string[][] };
            doublons?: string[]; erreurs_validation?: string[];
            exigences: ConvExigence[] };
  baseline_n: number;
};
```

- [ ] **Step 2 : Ajouter le client API**

```ts
// web/src/lib/api.ts — à la fin (réutilise API_BASE et le style des helpers existants)
export async function createConversionJob(
  files: File[], mapping: Record<string, number>, ragIndex: boolean,
): Promise<{ job_id: string }> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("mapping", JSON.stringify(mapping));
  fd.append("rag_index", String(ragIndex));
  const res = await fetch(`${API_BASE}/api/conversion/jobs`, { method: "POST", body: fd });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail
      ?? `conversion → HTTP ${res.status}`);
  return res.json();
}

export async function getConversionJob(jobId: string) {
  const res = await fetch(`${API_BASE}/api/conversion/jobs/${jobId}`);
  if (!res.ok) throw new Error(`job → HTTP ${res.status}`);
  return res.json();
}

export function conversionBaselineUrl(jobId: string): string {
  return `${API_BASE}/api/conversion/jobs/${jobId}/baseline`;
}

export async function redoConversionDoc(jobId: string, doc: string, familles: string[]) {
  const res = await fetch(`${API_BASE}/api/conversion/jobs/${jobId}/redo`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc, familles }),
  });
  if (!res.ok) throw new Error(`redo → HTTP ${res.status}`);
  return res.json();
}
```

- [ ] **Step 3 : Ajouter le flux SSE** (même mécanique lecteur/refus de lignes non-JSON que `streamAsk`)

```ts
// web/src/lib/sse.ts — à la fin
import type { ConvEvent } from "@/lib/types";

/** SSE GET de progression d'un job de conversion. S'achève sur `fini`. */
export async function streamConversionEvents(
  jobId: string,
  onEvent: (ev: ConvEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/conversion/jobs/${jobId}/events`, { signal });
  if (!res.ok || !res.body) throw new Error(`events → HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try { onEvent(JSON.parse(line.slice(6)) as ConvEvent); } catch { /* ignorée */ }
    }
  }
}
```

- [ ] **Step 4 : Coquille de l'onglet + câblage**

```tsx
// web/src/components/conversion/index.tsx
"use client";

/** Onglet Conversion : documents d'exigences → baseline JSON.
 * Trois états : dépôt → progression (SSE) → revue. */
export default function ConversionTab({ active }: { active: boolean }) {
  if (!active) return null;
  return <div data-testid="conversion-tab">Conversion — en construction.</div>;
}
```

Dans `web/src/app/requirements/page.tsx` : ajouter `"conversion"` au type `LynxTab` et au tableau `TABS` (entre `"chat"` et `"suivi"`), importer `ConversionTab` et rendre :

```tsx
<div className={tab === "conversion" ? "" : "hidden"}>
  <ConversionTab active={tab === "conversion"} />
</div>
```

et l'entrée de menu correspondante à côté des autres onglets (même composant de navigation que `chat`/`suivi` — libellé « Conversion »).

- [ ] **Step 5 : Vérifier** — Run `cd web && npx eslint src --max-warnings 0 && npx tsc --noEmit` — Expected : 0 erreur.

- [ ] **Step 6 : Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/lib/sse.ts \
        web/src/app/requirements/page.tsx web/src/components/conversion/
git commit -m "feat(conversion): types, client API, SSE et onglet LynX"
```

---

### Task 10 : Front — panneau Dépôt

**Files:**
- Create: `web/src/components/conversion/depot.tsx`
- Modify: `web/src/components/conversion/index.tsx` (machine à états)

**Interfaces:**
- Consumes: `createConversionJob` (Task 9)
- Produces: `DepotPanel({ onLaunched }: { onLaunched: (jobId: string) => void })` ; `index.tsx` gère `phase: "depot" | "progress" | "revue"` + persiste le dernier `jobId` dans `localStorage["lynx.conversion.job"]` (rechargement de la revue au retour sur l'onglet)

- [ ] **Step 1 : Implémenter DepotPanel**

```tsx
// web/src/components/conversion/depot.tsx
"use client";

import { useMemo, useState } from "react";
import { createConversionJob } from "@/lib/api";

const ACCEPT = ".pdf,.doc,.docx,.pptx,.xls,.xlsx,.html,.md";
const NIVEAUX = [0, 1, 2, 3, 4, 5, 6, 7];

/** Niveau par défaut : regroupement par préfixe de nom (fichiers qui
 * partagent les 4 premiers caractères → même niveau proposé, ordre
 * d'apparition des groupes). Aucun préréglage lié à un référentiel. */
function defaultMapping(files: File[]): Record<string, number> {
  const groups: string[] = [];
  const mapping: Record<string, number> = {};
  for (const f of files) {
    const key = f.name.slice(0, 4).toUpperCase();
    let idx = groups.indexOf(key);
    if (idx === -1) { groups.push(key); idx = groups.length - 1; }
    mapping[f.name] = Math.min(idx, NIVEAUX.length - 1);
  }
  return mapping;
}

export default function DepotPanel({ onLaunched }:
  { onLaunched: (jobId: string) => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [mapping, setMapping] = useState<Record<string, number>>({});
  const [ragIndex, setRagIndex] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const pick = (list: FileList | null) => {
    const fs = Array.from(list ?? []);
    setFiles(fs);
    setMapping(defaultMapping(fs));
    setError("");
  };

  const launch = async () => {
    setBusy(true); setError("");
    try {
      const { job_id } = await createConversionJob(files, mapping, ragIndex);
      onLaunched(job_id);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  };

  const ready = useMemo(() => files.length > 0 && !busy, [files, busy]);

  return (
    <section aria-label="Dépôt de documents">
      <p>Types acceptés : {ACCEPT.replaceAll(",", " ")} — .zip et .vsd non
        supportés en V1.</p>
      <input type="file" multiple accept={ACCEPT} aria-label="Documents"
             onChange={(e) => pick(e.target.files)} />
      {files.length > 0 && (
        <table>
          <thead><tr><th>Document</th><th>Niveau</th></tr></thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.name}>
                <td>{f.name}</td>
                <td>
                  <select aria-label={`Niveau de ${f.name}`}
                          value={mapping[f.name] ?? 0}
                          onChange={(e) => setMapping(
                            { ...mapping, [f.name]: Number(e.target.value) })}>
                    {NIVEAUX.map((n) => <option key={n} value={n}>L{n}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <label>
        <input type="checkbox" checked={ragIndex}
               onChange={(e) => setRagIndex(e.target.checked)} />
        Indexer aussi ces documents dans le RAG documentaire
      </label>
      {error && <p role="alert">{error}</p>}
      <button onClick={launch} disabled={!ready}>
        {busy ? "Lancement…" : "Convertir"}
      </button>
    </section>
  );
}
```

- [ ] **Step 2 : Machine à états dans index.tsx**

```tsx
// web/src/components/conversion/index.tsx
"use client";

import { useEffect, useState } from "react";
import DepotPanel from "./depot";
import ProgressPanel from "./progress";
import RevuePanel from "./revue";

const JOB_KEY = "lynx.conversion.job";
type Phase = "depot" | "progress" | "revue";

export default function ConversionTab({ active }: { active: boolean }) {
  const [phase, setPhase] = useState<Phase>("depot");
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    // Retour sur l'onglet : recharge la dernière revue si un job existe.
    const last = localStorage.getItem(JOB_KEY);
    if (last) { setJobId(last); setPhase("revue"); }
  }, []);

  if (!active) return null;

  const launched = (id: string) => {
    localStorage.setItem(JOB_KEY, id);
    setJobId(id); setPhase("progress");
  };

  return (
    <div data-testid="conversion-tab">
      <button onClick={() => { setPhase("depot"); }}
              disabled={phase === "depot"}>Nouveau lot</button>
      {phase === "depot" && <DepotPanel onLaunched={launched} />}
      {phase === "progress" && jobId &&
        <ProgressPanel jobId={jobId} onDone={() => setPhase("revue")} />}
      {phase === "revue" && jobId &&
        <RevuePanel jobId={jobId} onMissing={() => setPhase("depot")} />}
    </div>
  );
}
```

Note : `ProgressPanel` et `RevuePanel` n'existent pas encore — créer dans ce commit deux stubs minimaux (mêmes props que ci-dessus, rendu `null`) pour garder `tsc` vert ; ils sont remplis en Tasks 11–12.

- [ ] **Step 3 : Vérifier** — `cd web && npx eslint src --max-warnings 0 && npx tsc --noEmit` : 0 erreur.
- [ ] **Step 4 : Commit**

```bash
git add web/src/components/conversion/
git commit -m "feat(conversion): panneau dépôt (mapping niveaux, passerelle RAG)"
```

---

### Task 11 : Front — panneau Progression (SSE)

**Files:**
- Modify: `web/src/components/conversion/progress.tsx` (remplace le stub)

**Interfaces:**
- Consumes: `streamConversionEvents`, `ConvEvent` (Task 9)
- Produces: `ProgressPanel({ jobId, onDone }: { jobId: string; onDone: () => void })`

- [ ] **Step 1 : Implémenter**

```tsx
// web/src/components/conversion/progress.tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { streamConversionEvents } from "@/lib/sse";
import type { ConvEvent } from "@/lib/types";

type DocState = { etage: string; detail: string; n_marquees?: number;
                  n_llm?: number; warnings?: string[]; error?: string };

/** Progression glass box : une ligne par document, étage courant et
 * compteurs. Flux coupé sans `fini` → état dégradé, jamais de spinner
 * infini (même contrat que le chat). */
export default function ProgressPanel({ jobId, onDone }:
  { jobId: string; onDone: () => void }) {
  const [docs, setDocs] = useState<Record<string, DocState>>({});
  const [stalled, setStalled] = useState(false);
  const finished = useRef(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const onEvent = (e: ConvEvent) => {
      if (e.type === "etape") {
        setDocs((d) => ({ ...d, [e.doc]: { ...d[e.doc], etage: e.etage, detail: e.detail } }));
      } else if (e.type === "doc_fini") {
        setDocs((d) => ({ ...d, [e.doc]: { etage: "terminé", detail: "",
          n_marquees: e.n_marquees, n_llm: e.n_llm,
          warnings: e.warnings, error: e.error } }));
      } else if (e.type === "fini") {
        finished.current = true;
        onDone();
      }
    };
    streamConversionEvents(jobId, onEvent, ctrl.signal)
      .then(() => { if (!finished.current) setStalled(true); })
      .catch(() => { if (!finished.current) setStalled(true); });
    return () => ctrl.abort();
  }, [jobId, onDone]);

  return (
    <section aria-label="Progression de la conversion">
      {stalled && <p role="alert">Flux interrompu — recharger la page pour
        retrouver l'état du lot.</p>}
      <table>
        <thead><tr><th>Document</th><th>Étape</th><th>Marquées</th>
          <th>Détectées LLM</th><th>Signalements</th></tr></thead>
        <tbody>
          {Object.entries(docs).map(([name, s]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{s.error ? `échec : ${s.error}` : s.detail || s.etage}</td>
              <td>{s.n_marquees ?? "—"}</td>
              <td>{s.n_llm ?? "—"}</td>
              <td>{(s.warnings ?? []).join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 2 : Vérifier** — `npx eslint src --max-warnings 0 && npx tsc --noEmit` : 0 erreur.
- [ ] **Step 3 : Commit**

```bash
git add web/src/components/conversion/progress.tsx
git commit -m "feat(conversion): progression SSE glass box par document"
```

---

### Task 12 : Front — panneau Revue (exclusions, niveaux, export, envoi)

**Files:**
- Modify: `web/src/components/conversion/revue.tsx` (remplace le stub)

**Interfaces:**
- Consumes: `getConversionJob`, `conversionBaselineUrl`, `redoConversionDoc`, `ConvJobState`, `ConvExigence` (Task 9) ; endpoint LynX existant `POST /api/lynx/corpus/upload` (multipart `files`, routeur à préfixe `/api/lynx`) et `API_BASE`
- Produces: `RevuePanel({ jobId, onMissing }: { jobId: string; onMissing: () => void })` — construit côté client le JSON final (exclusions + niveaux édités appliqués) ; Télécharger = blob ; Envoyer vers LynX = POST du JSON édité vers `/corpus/upload` avec `confirm()` (remplacement du corpus)

- [ ] **Step 1 : Implémenter**

```tsx
// web/src/components/conversion/revue.tsx
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE, conversionBaselineUrl, getConversionJob, redoConversionDoc }
  from "@/lib/api";
import type { ConvExigence, ConvJobState } from "@/lib/types";

/** Revue avant export : la baseline n'entre dans LynX que sur geste
 * explicite, avec les exclusions et corrections de niveau appliquées. */
export default function RevuePanel({ jobId, onMissing }:
  { jobId: string; onMissing: () => void }) {
  const [state, setState] = useState<ConvJobState | null>(null);
  const [exclus, setExclus] = useState<Set<string>>(new Set());
  const [niveaux, setNiveaux] = useState<Record<string, number>>({});
  const [filtreDoc, setFiltreDoc] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    getConversionJob(jobId).then(setState).catch(() => onMissing());
  }, [jobId, onMissing]);
  useEffect(load, [load]);

  const exigences = useMemo(() => state?.report.exigences ?? [], [state]);
  const docs = useMemo(
    () => Array.from(new Set(exigences.map((e) => e.source.split(" §")[0]))),
    [exigences]);
  const visibles = filtreDoc
    ? exigences.filter((e) => e.source.startsWith(filtreDoc)) : exigences;

  const editee = useCallback((): ConvExigence[] =>
    exigences.filter((e) => !exclus.has(e.id))
      .map((e) => ({ ...e, niveau: niveaux[e.id] ?? e.niveau })), [exigences, exclus, niveaux]);

  const download = () => {
    const payload = { meta: { genere_par: "conversion", job: jobId },
                      exigences: editee() };
    const blob = new Blob([JSON.stringify(payload, null, 1)],
                          { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `baseline-${jobId}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const push = async () => {
    if (!window.confirm("Remplacer le corpus LynX par cette baseline "
        + `(${editee().length} exigences) ?`)) return;
    setBusy("push");
    try {
      const payload = { meta: { genere_par: "conversion", job: jobId },
                        exigences: editee() };
      const fd = new FormData();
      fd.append("files", new File([JSON.stringify(payload)],
                                  `baseline-${jobId}.json`,
                                  { type: "application/json" }));
      const res = await fetch(`${API_BASE}/api/lynx/corpus/upload`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(`corpus/upload → HTTP ${res.status}`);
      window.alert("Baseline envoyée à LynX.");
    } catch (e) {
      window.alert(String(e instanceof Error ? e.message : e));
    } finally { setBusy(""); }
  };

  const redo = async (doc: string) => {
    const familles = window.prompt(
      "Squelettes de familles à garder pour " + doc +
      " (séparés par des virgules ; vide = détection LLM) :", "");
    if (familles === null) return;
    setBusy(doc);
    try {
      await redoConversionDoc(jobId, doc,
        familles.split(",").map((s) => s.trim()).filter(Boolean));
      load();
    } finally { setBusy(""); }
  };

  if (!state) return <p>Chargement de la revue…</p>;
  const r = state.report;
  return (
    <section aria-label="Revue de la baseline">
      <p>
        {state.baseline_n} exigences · {r.liens?.appliques ?? 0} liens ·{" "}
        {(r.liens?.externes ?? []).length} références externes ·{" "}
        {(r.doublons ?? []).length} doublons ·{" "}
        {(r.erreurs_validation ?? []).length} erreurs de validation
      </p>
      <label>Document :{" "}
        <select value={filtreDoc} onChange={(e) => setFiltreDoc(e.target.value)}>
          <option value="">tous</option>
          {docs.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </label>
      {filtreDoc && (
        <button disabled={busy !== ""} onClick={() => redo(filtreDoc)}>
          {busy === filtreDoc ? "Re-extraction…" : "Corriger le motif d'ids…"}
        </button>
      )}
      <table>
        <thead><tr><th>Inclure</th><th>Id</th><th>Niveau</th><th>Type</th>
          <th>Texte</th><th>Parent</th><th>Source</th></tr></thead>
        <tbody>
          {visibles.map((e) => (
            <tr key={e.id}>
              <td><input type="checkbox" aria-label={`Inclure ${e.id}`}
                         checked={!exclus.has(e.id)}
                         onChange={() => setExclus((s) => {
                           const n = new Set(s);
                           if (n.has(e.id)) n.delete(e.id); else n.add(e.id);
                           return n;
                         })} /></td>
              <td>{e.id}</td>
              <td>
                <select aria-label={`Niveau de ${e.id}`}
                        value={niveaux[e.id] ?? e.niveau}
                        onChange={(ev) => setNiveaux(
                          { ...niveaux, [e.id]: Number(ev.target.value) })}>
                  {[0, 1, 2, 3, 4, 5, 6, 7].map((n) =>
                    <option key={n} value={n}>L{n}</option>)}
                </select>
              </td>
              <td>{e.type}</td>
              <td>{e.texte.length > 160 ? e.texte.slice(0, 160) + "…" : e.texte}</td>
              <td>{e.parent_id ?? "—"}</td>
              <td>{e.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <a href={conversionBaselineUrl(jobId)} download>Baseline brute (serveur)</a>
      <button onClick={download}>Télécharger le JSON (édité)</button>
      <button onClick={push} disabled={busy === "push"}>
        {busy === "push" ? "Envoi…" : "Envoyer vers LynX"}
      </button>
    </section>
  );
}
```

- [ ] **Step 2 : Vérifier** — `npx eslint src --max-warnings 0 && npx tsc --noEmit` : 0 erreur. Puis vérification live rapide : `python serve.py`, onglet Conversion, déposer `tests/conversion` fixture `.md`, dérouler le cycle jusqu'au téléchargement.
- [ ] **Step 3 : Commit**

```bash
git add web/src/components/conversion/revue.tsx
git commit -m "feat(conversion): revue éditable, export JSON et envoi corpus LynX"
```

---

### Task 13 : Smoke Playwright + documentation

**Files:**
- Create: `web/e2e/conversion.spec.ts`, `web/e2e/fixtures/spec-exigences.md`
- Modify: `ARCHITECTURE.md` (section Conversion), `README.md` (une ligne dans la partie LynX), composant d'infos LynX (onglet Paramètres — même fichier qui décrit les autres onglets : ajouter le rôle de la page Conversion, principe mémoire « tout nouveau skill visible dans l'onglet Informations »)

**Interfaces:**
- Consumes: UI Tasks 9–12, API Task 7.

- [ ] **Step 1 : Fixture + test**

```markdown
<!-- web/e2e/fixtures/spec-exigences.md -->
## 1 Exigences générales
REQ_E2E_01
Le service doit répondre en moins d'une seconde.
REQ_E2E_02
Le service doit tracer chaque erreur.
REQ_E2E_03
Le service doit redémarrer sans intervention.
```

```ts
// web/e2e/conversion.spec.ts
import { expect, test } from "@playwright/test";
import path from "path";

/** Smoke conversion : la page se monte toujours ; le cycle complet ne
 * tourne que si l'API répond (même tolérance que les autres smokes). */

test("onglet conversion se monte", async ({ page }) => {
  await page.goto("/requirements?tab=conversion");
  await expect(page.getByTestId("conversion-tab")).toBeVisible();
  await expect(page.getByLabel("Documents")).toBeVisible();
});

test("cycle dépôt → revue → téléchargement", async ({ page, request }) => {
  const api = await request.get("http://localhost:8000/api/conversion/jobs/zzz")
    .then((r) => r.status() === 404).catch(() => false);
  test.skip(!api, "API indisponible — cycle complet sauté");

  await page.goto("/requirements?tab=conversion");
  await page.getByRole("button", { name: "Nouveau lot" }).click().catch(() => {});
  await page.getByLabel("Documents").setInputFiles(
    path.join(__dirname, "fixtures", "spec-exigences.md"));
  await page.getByRole("button", { name: "Convertir" }).click();
  await expect(page.getByLabel("Revue de la baseline")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("3 exigences")).toBeVisible();
  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: "Télécharger le JSON (édité)" }).click();
  expect((await dl).suggestedFilename()).toMatch(/baseline-.*\.json/);
});
```

- [ ] **Step 2 : Lancer les e2e**

Run: `cd web && npm run test:e2e` (API + front démarrés via `python serve.py` au besoin)
Expected: les smokes existants restent verts + 2 nouveaux tests verts (ou 1 vert + 1 skipped si API coupée).

- [ ] **Step 3 : Documentation**

Dans `ARCHITECTURE.md`, ajouter une section « Conversion documents → baseline » : le schéma 4 étages (reprendre celui du spec), l'emplacement des artefacts (`data/conversions/<job_id>/`), le principe de transversalité, et le renvoi vers le spec. Dans `README.md`, une ligne dans la présentation LynX : « Page Conversion : transforme des documents d'exigences (.doc/.docx/.pdf/.xls/.xlsx/.md) en baseline JSON, avec revue avant import. » Dans le composant d'informations LynX (onglet Paramètres, fichier qui décrit déjà Matrice/Exigences/Chat/Suivi), ajouter le bloc Conversion : ce que fait la page, ce que veut dire « Exigence (détectée) », et que rien n'entre dans le corpus sans envoi explicite.

- [ ] **Step 4 : Vérification finale complète**

Run: `~/Documents/rag_project/.venv/bin/python -m pytest -q` puis `cd web && npx eslint src --max-warnings 0 && npx tsc --noEmit && npm run test:e2e`
Expected: tout vert.

- [ ] **Step 5 : Commit**

```bash
git add web/e2e/ ARCHITECTURE.md README.md web/src/components/
git commit -m "test(conversion): smoke Playwright + docs (ARCHITECTURE, README, onglet infos)"
```
