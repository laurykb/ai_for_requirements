# indexing/chunking.py
# Découpe un fichier markdown en sections (titres) puis en chunks intelligents sans couper les tableaux
# Supporte 2 modes : "naive" (split par headers) et "technical" (hiérarchie numérotée, fusion parent-enfant)
# Étape 5 : détection tables/figures, injection contexte, tagging chunk_type
from pathlib import Path
from langchain_core.documents import Document
from semantic_text_splitter import MarkdownSplitter
from utils.text_utils import make_doc_id
from env_config import MAX_CHUNK_LENGTH
import re

# Regex pour détecter les marqueurs de page Docling : <!-- page:N -->
_PAGE_MARKER_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")

# Regex pour détecter un titre numéroté : "## 1.2.3. Titre" ou "## 1.2.3 Titre"
_NUMBERED_HEADING_RE = re.compile(
    r"^(#{1,6})\s+"                         # niveaux markdown
    r"((\d+\.)+\d*\.?)\s+"                  # numérotation hiérarchique (1. / 1.2. / 1.2.3.)
    r"(.+)$"                                # titre
)

# Regex pour détecter les entités normatives CC : D.xxx, T.xxx, O.xxx, OE.xxx, A.xxx, F.xxx, P.xxx
_CC_ENTITY_RE = re.compile(
    r"^(#{1,6})\s+"
    r"((?:D|T|O|OE|A|F|P|SF|SFR|SFT|ST)[\._][\w]+)"
    r"(.*)$"
)

# Regex pour détecter les lignes de tableau Markdown
_TABLE_LINE_RE = re.compile(r"^\s*\|")
# Regex pour détecter les marqueurs de figure Docling
_FIGURE_RE = re.compile(r"\[Figure(?:\s*:\s*|\s+)(.*?)\]")


# ─────────────────── Fonctions utilitaires communes ───────────────────

def _extract_page_number(text: str) -> int | None:
    """
    Extrait le premier numéro de page trouvé dans un chunk
    via les marqueurs <!-- page:N -->.
    """
    matches = _PAGE_MARKER_RE.findall(text)
    if matches:
        return int(matches[0])
    return None


def _strip_page_markers(text: str) -> str:
    """Supprime les marqueurs <!-- page:N --> du contenu du chunk."""
    return _PAGE_MARKER_RE.sub("", text).strip()


def regroup_tables(lines):
    """Regroupe les lignes d'un tableau Markdown pour éviter de les couper."""
    grouped = []
    buffer = []
    in_table = False
    for line in lines:
        if line.strip().startswith("|"):
            buffer.append(line)
            in_table = True
        else:
            if in_table:
                grouped.append("\n".join(buffer))
                buffer = []
                in_table = False
            grouped.append(line)
    if buffer:
        grouped.append("\n".join(buffer))
    return grouped


# ─────────────────── Détection type de chunk (Étape 5) ───────────────────

def _classify_chunk(text: str) -> str:
    """
    Classifie un chunk selon son contenu dominant :
      - "table"  : le chunk est principalement un tableau Markdown
      - "figure" : le chunk contient principalement des marqueurs de figure
      - "mixed"  : le chunk mélange texte + table ou texte + figure
      - "text"   : chunk purement textuel
    """
    lines = [l for l in text.splitlines()
             if l.strip() and not _PAGE_MARKER_RE.match(l.strip())]
    if not lines:
        return "text"

    table_lines = sum(1 for l in lines if _TABLE_LINE_RE.match(l))
    figure_count = len(_FIGURE_RE.findall(text))
    total = len(lines)

    table_ratio = table_lines / total if total else 0
    has_figure = figure_count > 0

    if table_ratio >= 0.6:
        return "table"
    if has_figure and table_ratio < 0.2 and total <= 5:
        return "figure"
    if table_ratio >= 0.3 or has_figure:
        return "mixed"
    return "text"


def _extract_table_headers(text: str) -> list[str]:
    """
    Extrait les en-têtes (première ligne de données) de chaque tableau
    trouvé dans le chunk. Utile pour générer un préambule descriptif.
    Retourne ex: ["Threats | Security objectives", "OSP | Security objectives"]
    """
    headers = []
    lines = text.splitlines()
    in_table = False
    for line in lines:
        stripped = line.strip()
        if _TABLE_LINE_RE.match(stripped):
            if not in_table:
                # Première ligne du tableau = en-tête
                # Nettoyer les pipes et tirets de séparation
                cells = [c.strip() for c in stripped.split("|") if c.strip()]
                # Ignorer les lignes de séparation (---|---|---)
                if cells and not all(re.match(r"^-+$", c) for c in cells):
                    headers.append(" | ".join(cells))
                in_table = True
        else:
            in_table = False
    return headers


def _extract_figure_captions(text: str) -> list[str]:
    """Extrait les légendes des figures trouvées dans le chunk."""
    return _FIGURE_RE.findall(text)


def _build_context_preamble(chunk_type: str, text: str, heading: str = "",
                            breadcrumb: str = "") -> str:
    """
    Construit un préambule de contexte pour les chunks table/figure/mixed.
    Ce préambule aide le retrieval en ajoutant des mots-clés sémantiques
    qui décrivent ce que contient le tableau ou la figure.

    Exemples de sortie :
      "[Contexte] Tableau dans la section '4.3.4. TABLES' — colonnes : Threats | Security objectives"
      "[Contexte] Figure 1:Mistral IP VS8 System — section '1.3. TOE OVERVIEW'"
    """
    parts = []

    if chunk_type == "table" or (chunk_type == "mixed" and _TABLE_LINE_RE.search(text)):
        table_headers = _extract_table_headers(text)
        desc = "Tableau"
        if table_headers:
            desc += f" — colonnes : {table_headers[0]}"
            if len(table_headers) > 1:
                desc += f" (+ {len(table_headers) - 1} autre(s) tableau(x))"
        if heading:
            desc += f" — section '{heading}'"
        elif breadcrumb:
            desc += f" — {breadcrumb}"
        parts.append(desc)

    if chunk_type == "figure" or (chunk_type == "mixed" and _FIGURE_RE.search(text)):
        captions = _extract_figure_captions(text)
        for cap in captions:
            desc = f"Figure : {cap}" if cap else "Figure sans légende"
            if heading and not parts:
                desc += f" — section '{heading}'"
            parts.append(desc)

    if not parts:
        return ""
    return "[Contexte] " + " ; ".join(parts)


# ─────────────────── MODE NAIVE (existant) ───────────────────

def split_by_titles(md_text):
    """Découpe le texte en sections selon les titres Markdown (ex : #, ##, ###)."""
    sections = []
    current = []
    for line in md_text.splitlines():
        if re.match(r"^#+ ", line):
            if current:
                sections.append("\n".join(current))
                current = []
        current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _chunk_sections_naive(text: str, src: str, max_characters: int) -> list[Document]:
    """
    Mode NAIVE : découpe par headers Markdown, puis split par taille.
    C'est le mode original du projet.
    """
    sections = split_by_titles(text)
    docs = []
    last_known_page: int | None = None

    for sec_idx, section in enumerate(sections):
        lines = section.splitlines()
        grouped_lines = regroup_tables(lines)
        grouped_text = "\n".join(grouped_lines)
        splitter = MarkdownSplitter(max_characters)
        chunks = splitter.chunks(grouped_text)
        for i, chunk in enumerate(chunks):
            page_num = _extract_page_number(chunk)
            if page_num is not None:
                last_known_page = page_num
            else:
                page_num = last_known_page
            clean_content = _strip_page_markers(chunk)

            # Étape 5 : détecter le type de chunk et injecter le contexte
            chunk_type = _classify_chunk(clean_content)
            if chunk_type in ("table", "figure", "mixed"):
                preamble = _build_context_preamble(chunk_type, clean_content)
                if preamble:
                    clean_content = f"{preamble}\n{clean_content}"

            doc_id = make_doc_id(clean_content, src, f"{sec_idx}_{i}")
            meta = {
                "id": doc_id,
                "chunk_idx": i,
                "section_idx": sec_idx,
                "source": src,
                "chunking_mode": "naive",
                "chunk_type": chunk_type,
            }
            if page_num is not None:
                meta["page_number"] = page_num
            docs.append(Document(page_content=clean_content, metadata=meta))
    return docs


# ─────────────────── MODE TECHNICAL (nouveau) ───────────────────

def _heading_depth(numbering: str) -> int:
    """
    Profondeur hiérarchique d'une numérotation.
    "1." → 1,  "1.2." → 2,  "1.2.3." → 3,  "1.2.3" → 3
    """
    parts = [p for p in numbering.split(".") if p]
    return len(parts)


def _parse_heading(line: str) -> dict | None:
    """
    Parse un titre Markdown et extrait les infos de structure.
    Retourne {"numbering": "1.2.3.", "depth": 3, "title": "...", "is_entity": False}
    ou None si ce n'est pas un titre structuré.
    """
    # Essayer d'abord la numérotation hiérarchique
    m = _NUMBERED_HEADING_RE.match(line)
    if m:
        numbering = m.group(2)
        title = m.group(4).strip()
        return {
            "numbering": numbering,
            "depth": _heading_depth(numbering),
            "title": title,
            "full_title": f"{numbering} {title}",
            "is_entity": False,
        }
    # Essayer les entités normatives CC (D.xxx, T.xxx, O.xxx...)
    m2 = _CC_ENTITY_RE.match(line)
    if m2:
        entity_id = m2.group(2)
        rest = m2.group(3).strip()
        return {
            "numbering": entity_id,
            "depth": 99,  # profondeur max = feuille, ne regroupe pas
            "title": rest if rest else entity_id,
            "full_title": f"{entity_id} {rest}".strip(),
            "is_entity": True,
        }
    return None


def _split_into_headed_blocks(text: str) -> list[dict]:
    """
    Découpe le texte en blocs headed : chaque bloc commence par un titre
    et contient le texte jusqu'au prochain titre.
    Retourne [{"heading_info": {...} | None, "content": "...", "raw_heading": "..."}]
    """
    blocks = []
    current_lines = []
    current_heading_info = None
    current_raw_heading = ""

    for line in text.splitlines():
        is_heading = re.match(r"^#+ ", line)
        if is_heading:
            # Flush le bloc précédent
            if current_lines or current_heading_info:
                blocks.append({
                    "heading_info": current_heading_info,
                    "content": "\n".join(current_lines),
                    "raw_heading": current_raw_heading,
                })
            current_heading_info = _parse_heading(line)
            current_raw_heading = line
            current_lines = []
        else:
            current_lines.append(line)

    # Flush le dernier bloc
    if current_lines or current_heading_info:
        blocks.append({
            "heading_info": current_heading_info,
            "content": "\n".join(current_lines),
            "raw_heading": current_raw_heading,
        })
    return blocks


def _merge_short_children(blocks: list[dict], min_chars: int = 200) -> list[dict]:
    """
    Fusionne les sous-sections trop courtes avec leur parent.
    Si un bloc enfant (depth > parent) fait moins de min_chars,
    il est fusionné dans le bloc parent.
    """
    if not blocks:
        return blocks

    merged = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        info = block["heading_info"]

        # Si ce bloc a un heading numéroté, chercher les enfants courts
        if info and not info["is_entity"]:
            parent_depth = info["depth"]
            # Accumuler le contenu des enfants fusionnés (sans le heading parent,
            # qui sera ré-ajouté par _chunk_sections_technical via raw_heading)
            combined_body = block["content"]

            # Regarder les blocs suivants : tant qu'ils sont des enfants (depth > parent)
            j = i + 1
            while j < len(blocks):
                child = blocks[j]
                child_info = child["heading_info"]

                # Si pas de heading ou profondeur ≤ parent → stop
                if child_info is None or (not child_info["is_entity"] and child_info["depth"] <= parent_depth):
                    break

                child_text = child["raw_heading"] + "\n" + child["content"]

                # Si l'enfant est court, le fusionner
                if len(child_text.strip()) < min_chars:
                    combined_body += "\n\n" + child_text
                    j += 1
                else:
                    break

            merged.append({
                "heading_info": info,
                "content": combined_body,
                "raw_heading": block["raw_heading"],
            })
            i = j
        else:
            merged.append(block)
            i += 1

    return merged


def _build_breadcrumb(blocks: list[dict], current_idx: int) -> str:
    """
    Construit le fil d'Ariane (breadcrumb) du bloc courant :
    "1. INTRODUCTION > 1.2. TOE IDENTIFICATION > 1.2.1. Hardware"
    en remontant les parents dans la hiérarchie numérotée.
    """
    current = blocks[current_idx]
    info = current["heading_info"]
    if not info or info["is_entity"]:
        return ""

    # Collecter les ancêtres
    ancestors = []
    target_depth = info["depth"]

    for k in range(current_idx - 1, -1, -1):
        prev = blocks[k]
        prev_info = prev["heading_info"]
        if prev_info and not prev_info["is_entity"] and prev_info["depth"] < target_depth:
            ancestors.append(prev_info["full_title"])
            target_depth = prev_info["depth"]
            if target_depth <= 1:
                break

    ancestors.reverse()
    if ancestors:
        return " > ".join(ancestors)
    return ""


def _chunk_sections_technical(text: str, src: str, max_characters: int) -> list[Document]:
    """
    Mode TECHNICAL : adapté aux documents normatifs (ANSSI, CC, guides techniques).
    
    Améliorations par rapport au mode naive :
    1) Détection de la hiérarchie via numérotation (1., 1.2., 1.2.3.)
    2) Fusion des sous-sections trop courtes avec leur parent
    3) Injection du contexte parent (breadcrumb) dans chaque chunk
    4) Détection des entités normatives CC (D.xxx, T.xxx, O.xxx...)
    """
    blocks = _split_into_headed_blocks(text)
    blocks = _merge_short_children(blocks, min_chars=200)

    docs = []
    last_known_page: int | None = None

    for sec_idx, block in enumerate(blocks):
        info = block["heading_info"]
        full_text = block["raw_heading"] + "\n" + block["content"] if block["raw_heading"] else block["content"]

        # Regrouper les tableaux
        lines = full_text.splitlines()
        grouped_lines = regroup_tables(lines)
        grouped_text = "\n".join(grouped_lines)

        # Construire le breadcrumb (contexte parent)
        breadcrumb = _build_breadcrumb(blocks, sec_idx)

        # Split par taille
        splitter = MarkdownSplitter(max_characters)
        chunks = splitter.chunks(grouped_text)

        for i, chunk in enumerate(chunks):
            page_num = _extract_page_number(chunk)
            if page_num is not None:
                last_known_page = page_num
            else:
                page_num = last_known_page

            clean_content = _strip_page_markers(chunk)

            # Étape 5 : détecter le type de chunk et injecter le contexte
            heading_title = info["full_title"] if info else ""
            chunk_type = _classify_chunk(clean_content)
            if chunk_type in ("table", "figure", "mixed"):
                preamble = _build_context_preamble(
                    chunk_type, clean_content,
                    heading=heading_title, breadcrumb=breadcrumb,
                )
                if preamble:
                    clean_content = f"{preamble}\n{clean_content}"

            # Injecter le breadcrumb en tête du chunk si disponible
            if breadcrumb:
                clean_content = f"[{breadcrumb}]\n{clean_content}"

            doc_id = make_doc_id(clean_content, src, f"{sec_idx}_{i}")
            meta = {
                "id": doc_id,
                "chunk_idx": i,
                "section_idx": sec_idx,
                "source": src,
                "chunking_mode": "technical",
                "chunk_type": chunk_type,
            }
            if page_num is not None:
                meta["page_number"] = page_num
            if breadcrumb:
                meta["breadcrumb"] = breadcrumb
            if info:
                meta["heading"] = info["full_title"]
                if info["is_entity"]:
                    meta["entity_id"] = info["numbering"]

            docs.append(Document(page_content=clean_content, metadata=meta))

    return docs


# ─────────────────── API PUBLIQUE ───────────────────

CHUNKING_MODES = {
    "naive": "Découpe par headers Markdown (générique, pour tout type de document)",
    "technical": "Découpe hiérarchique (optimisée pour documents normatifs ANSSI/CC/guides techniques)",
}

def decoupe_semantic_md(md_path: str, max_characters: int = MAX_CHUNK_LENGTH,
                        mode: str = "technical") -> list[Document]:
    """
    Découpe un fichier markdown en chunks avec la stratégie choisie.

    Args:
        md_path: Chemin du fichier markdown à découper.
        max_characters: Nombre maximal de caractères par chunk.
        mode: "naive" ou "technical" (défaut: "technical").

    Returns:
        list[Document]: Liste d'objets Document, chacun correspondant à un chunk.
    """
    text = Path(md_path).read_text(encoding="utf-8")
    src = Path(md_path).name

    if mode == "technical":
        return _chunk_sections_technical(text, src, max_characters)
    else:
        return _chunk_sections_naive(text, src, max_characters)
