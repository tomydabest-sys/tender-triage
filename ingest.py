#!/usr/bin/env python3
"""
ingest.py — turn a real tender document into a requirement list.

The first version of this project parsed one synthetic Markdown file with tidy
'1.1'-numbered questions. Run against ten real documents pulled from Contracts
Finder it scored 0% precision and 0% recall: it produced 358 phantom
requirements from three documents that contain no questions at all (they are
contracts and scheme rules, where '1.1' is a clause), and zero requirements from
the two documents that do contain questions, because real question sets live in
tables, not numbered paragraphs.

This module fixes both halves:

  1. A document classifier that decides whether a file is a question set at all
     before trying to extract anything from it. Contracts, specifications and
     covering letters are rejected rather than mined for false requirements.

  2. Three extraction strategies — table matrix, labelled question, numbered
     paragraph — chosen by what the document actually contains.

Formats: .pdf, .docx, .md, .txt
"""

import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Document loading — a common shape for every format
# ---------------------------------------------------------------------------

class Doc:
    """Paragraphs and tables, however the file stored them."""

    def __init__(self, name, paragraphs, tables):
        self.name = name
        self.paragraphs = [p for p in paragraphs if p.strip()]
        self.tables = tables          # list of list-of-rows, each row a list of cells

    @property
    def text(self):
        flat = list(self.paragraphs)
        for table in self.tables:
            for row in table:
                flat.append(" | ".join(row))
        return "\n".join(flat)


def load(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix == ".docx":
        return _load_docx(path)
    if suffix in (".md", ".txt"):
        return Doc(path.name, path.read_text(encoding="utf-8", errors="replace").splitlines(), [])
    raise ValueError(f"unsupported format: {suffix}")


def _load_pdf(path):
    import pdfplumber
    paragraphs, tables = [], []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            paragraphs.extend((page.extract_text() or "").splitlines())
            for raw in page.extract_tables() or []:
                rows = [[(c or "").strip().replace("\n", " ") for c in row] for row in raw]
                if len(rows) > 1:
                    tables.append(rows)
    return Doc(path.name, paragraphs, tables)


def _load_docx(path):
    import docx
    document = docx.Document(path)
    paragraphs = [p.text for p in document.paragraphs]
    tables = []
    for table in document.tables:
        rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in table.rows]
        if len(rows) > 1:
            tables.append(rows)
    return Doc(path.name, paragraphs, tables)


# ---------------------------------------------------------------------------
# Document classification
# ---------------------------------------------------------------------------

# Contract language. Dense in terms and conditions, near-absent in question sets.
CONTRACT_MARKERS = [
    r"\bthe Supplier shall\b", r"\bthe Authority shall\b", r"\bthe Parties\b",
    r"\bhereby\b", r"\bherein\b", r"\bthereof\b", r"\bnotwithstanding\b",
    r"\bCall-Off Contract\b", r"\bin accordance with Clause\b", r"\bsubject to Clause\b",
    r"\bthis Agreement\b", r"\bshall be deemed\b", r"\bwithout prejudice\b",
]

# Question language, and the scoring metadata that only appears in response docs.
QUESTION_MARKERS = [
    r"\bplease (?:describe|confirm|provide|state|outline|detail)\b",
    r"\bdescribe (?:your|how|the)\b", r"\bbidders? (?:must|should|are required)\b",
    r"\byour response\b", r"\bmax(?:imum)? (?:score|word)\b", r"\bword (?:limit|count)\b",
    r"\bevaluation criteria\b", r"\bweighting\b", r"\bpass\s*/\s*fail\b",
    r"\bscored out of\b", r"\bhow will you\b", r"\bhow do you\b",
]


def classify_document(doc):
    """Decide whether this file is a question set worth extracting from.

    Returns (verdict, evidence). Verdict is one of:
      QUESTION_SET  — contains questions to answer
      CONTRACT      — terms, conditions or scheme rules; numbered items are clauses
      INFORMATION   — specification, covering letter or other prose
    """
    text = doc.text
    if not text.strip():
        return "INFORMATION", {"reason": "empty document"}

    words = max(1, len(text.split()))
    contract_hits = sum(len(re.findall(p, text, re.I)) for p in CONTRACT_MARKERS)
    question_hits = sum(len(re.findall(p, text, re.I)) for p in QUESTION_MARKERS)

    # Per 1000 words, so a long contract and a short one score alike.
    contract_density = 1000 * contract_hits / words
    question_density = 1000 * question_hits / words

    has_matrix = find_question_tables(doc) is not None

    evidence = {
        "words": words,
        "contract_markers": contract_hits,
        "question_markers": question_hits,
        "contract_density_per_1k": round(contract_density, 2),
        "question_density_per_1k": round(question_density, 2),
        "question_table_found": has_matrix,
    }

    # A question matrix is near-conclusive: contracts do not contain one.
    if has_matrix:
        return "QUESTION_SET", evidence
    if contract_density >= 1.5 and contract_density > question_density:
        return "CONTRACT", evidence
    if question_density >= 0.8 or question_hits >= 8:
        return "QUESTION_SET", evidence
    return "INFORMATION", evidence


# ---------------------------------------------------------------------------
# Strategy 1 — table matrix (the dominant real format)
# ---------------------------------------------------------------------------

ID_COLUMN = re.compile(r"\b(question|q\s*(no|ref|id)?|ref(erence)?|item|criteri[ao]n?)\b", re.I)
TEXT_COLUMN = re.compile(r"\b(requirement|question|description|detail|criteri[ao]n?|"
                         r"title|focus|response|narrative)\b", re.I)
SCORE_COLUMN = re.compile(r"\b(score|weight|marks?|points?|%)\b", re.I)

# A question ID: 1.1, Q3, Q1A, 0.01, C2.4, B7.
# It must carry a dot or a section letter. A bare single digit is rejected, because
# scoring rubrics ("4 = excellent, 3 = good") are tables of bare digits beside long
# descriptive text, and were otherwise being extracted as if they were questions.
QUESTION_ID = re.compile(
    r"^(?:Q\s*)?(?:"
    r"\d+\.\d+(?:\.\d+)*[A-Za-z]?"      # 1.1, 0.01, 2.4.1
    r"|\d+[A-Za-z]"                        # 1A, 12b
    r"|[A-Za-z]\d+(?:\.\d+)*"             # B1, C4, D2.1
    r")\.?$", re.I)


def find_question_tables(doc):
    """Locate every table that pairs a question-ID column with a question-text column.

    Real PDFs fragment one question set across many tables — one per section, each
    re-stating its own header. Returning only the largest table lost most of the
    document, so every qualifying table is collected and the results merged.
    """
    found = []
    for table in doc.tables:
        header = [c.lower() for c in table[0]]
        joined = " ".join(header)
        # A header naming both columns is the clean case. Many real tables carry a
        # section banner instead of a header row, so a table whose first column is
        # mostly question IDs also qualifies.
        header_names_columns = ID_COLUMN.search(joined) and TEXT_COLUMN.search(joined)
        # The ID column is the one whose cells actually look like question IDs.
        id_col = None
        for idx in range(len(table[0])):
            values = [row[idx] for row in table[1:] if idx < len(row) and row[idx]]
            if not values:
                continue
            if sum(bool(QUESTION_ID.match(v.strip())) for v in values) >= max(3, len(values) // 3):
                id_col = idx
                break
        if id_col is None:
            continue
        # Reject the scoring rubric: a column headed "score" is a mark scale, not
        # a question reference, however much its rows look like IDs.
        if id_col < len(header) and SCORE_COLUMN.search(header[id_col]):
            continue
        if not header_names_columns:
            ids = [row[id_col] for row in table[1:] if id_col < len(row) and row[id_col].strip()]
            if len(ids) < 2:
                continue
        # The text column is the widest remaining one.
        widths = {}
        for idx in range(len(table[0])):
            if idx == id_col:
                continue
            cells = [row[idx] for row in table[1:] if idx < len(row)]
            widths[idx] = sum(len(c) for c in cells) / max(1, len(cells))
        if not widths:
            continue
        text_col = max(widths, key=widths.get)
        if widths[text_col] < 25:          # too narrow to be question text
            continue
        score_col = next((i for i, h in enumerate(header) if SCORE_COLUMN.search(h)), None)
        found.append((table, id_col, text_col, score_col))
    return found or None


def extract_from_table(doc):
    tables = find_question_tables(doc)
    if not tables:
        return []
    requirements, seen = [], set()
    for table, id_col, text_col, score_col in tables:
        for row in table:
            if id_col >= len(row) or text_col >= len(row):
                continue
            ref, body = row[id_col].strip(), row[text_col].strip()
            if not QUESTION_ID.match(ref) or len(body) < 15:
                continue
            # Section header rows repeat the same value across every cell.
            if len(set(c.strip() for c in row if c.strip())) <= 1:
                continue
            key = (ref.upper().rstrip("."), body[:60].lower())
            if key in seen:            # the same table re-extracted per PDF page
                continue
            seen.add(key)
            raw_score = (row[score_col].strip()
                         if score_col is not None and score_col < len(row) else "")
            requirements.append(_build(ref.rstrip("."), body, raw_score, "table"))
    requirements.sort(key=lambda r: (r["ref"][0].isdigit(), r["ref"]))
    return requirements


# ---------------------------------------------------------------------------
# Strategy 2 — labelled questions in running text ("Question 4 — ...")
# ---------------------------------------------------------------------------

LABELLED = re.compile(r"^\s*(?:Question|Q)\s*[-–:]?\s*(\d+[A-Za-z]?|\d+\.\d+)\s*[-–:.)]?\s*(\S.*)$",
                      re.I)


def extract_from_labels(doc):
    requirements, current = [], None
    for line in doc.paragraphs:
        match = LABELLED.match(line)
        if match:
            if current:
                requirements.append(current)
            current = {"ref": match.group(1), "body": [match.group(2)]}
        elif current and line.strip():
            if len(" ".join(current["body"])) < 1200:
                current["body"].append(line.strip())
    if current:
        requirements.append(current)

    out = []
    for item in requirements:
        body = " ".join(item["body"]).strip()
        # Without this guard the strategy fired on specification documents, where
        # numbered headings like "3 Geographical Scope" are topic labels, not
        # questions. A labelled question has to actually ask for something.
        if len(body) < 25 or not ASKS.search(body):
            continue
        out.append(_build(item["ref"], body, "", "label"))
    return out


# ---------------------------------------------------------------------------
# Strategy 3 — numbered paragraphs (the original format)
# ---------------------------------------------------------------------------

NUMBERED = re.compile(r"^\s*(\d+\.\d+)\s+(\S.*)")
SECTION = re.compile(r"^#{0,3}\s*(?:SECTION\s+)?(\d+)\s*[—\-–:]\s*(.+)$", re.I)

# A numbered item only counts as a requirement if it asks for something.
ASKS = re.compile(r"\b(describe|confirm|provide|state|outline|detail|explain|complete|"
                  r"please|how (?:will|do|would)|what|submit|demonstrate|set out)\b", re.I)


def extract_from_numbering(doc):
    sections = {}
    for line in doc.paragraphs:
        match = SECTION.match(line)
        if match:
            sections[match.group(1)] = match.group(2).strip().title()

    requirements = []
    for line in doc.paragraphs:
        match = NUMBERED.match(line)
        if not match:
            continue
        ref, body = match.group(1), match.group(2).strip()
        # This is the guard the first version lacked. Without it, every clause in
        # a contract became a requirement.
        if not ASKS.search(body):
            continue
        req = _build(ref, body, "", "numbered")
        req["section"] = sections.get(ref.split(".")[0], "Unknown")
        requirements.append(req)
    return requirements


# ---------------------------------------------------------------------------
# Shared field extraction
# ---------------------------------------------------------------------------

WEIGHT = re.compile(r"(?:weight(?:ed|ing)?|score|marks?)\D{0,12}(\d{1,3}(?:\.\d+)?)\s*%"
                    r"|(\d{1,3}(?:\.\d+)?)\s*%", re.I)
WORDCAP = re.compile(r"(?:max(?:imum)?|limit|no more than|up to)\D{0,15}(\d{2,5})\s*words"
                     r"|(\d{2,5})\s*words?\s*(?:max|limit)", re.I)
PASSFAIL = re.compile(r"pass\s*/?\s*fail|not scored|for information only", re.I)


def _build(ref, body, raw_score, strategy):
    combined = f"{body} {raw_score}"
    weight_match = WEIGHT.search(combined)
    word_match = WORDCAP.search(combined)
    weight = None
    if weight_match:
        weight = float(weight_match.group(1) or weight_match.group(2))
    words = None
    if word_match:
        words = int(word_match.group(1) or word_match.group(2))

    question = re.sub(r"\s*\([^)]*(?:Pass/Fail|Weighted)[^)]*\)\s*$", "", body,
                      flags=re.I).strip()

    return {
        "ref": ref,
        "section": "Unknown",
        "question": question,
        "scoring": "Pass/Fail" if PASSFAIL.search(combined) else "Weighted",
        "weight_pct": weight,
        "word_limit": words,
        "mandatory_language": bool(re.search(r"\bMUST\b", body)),
        "extracted_by": strategy,
    }


# ---------------------------------------------------------------------------

def extract(path):
    """Classify, then extract with whichever strategy fits. Returns (verdict, reqs, evidence).

    A file that cannot be opened is reported as UNREADABLE rather than raising:
    in a real intake, a tender pack routinely contains a download that failed or a
    scanned image with no text layer, and one bad file must not stop the batch.
    """
    try:
        doc = load(path)
    except Exception as exc:
        return "UNREADABLE", [], {"reason": f"{type(exc).__name__}: {exc}"[:120]}
    verdict, evidence = classify_document(doc)
    if verdict != "QUESTION_SET":
        return verdict, [], evidence

    for strategy in (extract_from_table, extract_from_labels, extract_from_numbering):
        requirements = strategy(doc)
        if requirements:
            return verdict, requirements, evidence
    return verdict, [], evidence


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        verdict, requirements, evidence = extract(arg)
        print(f"\n{Path(arg).name}")
        print(f"  verdict: {verdict}   "
              f"(contract {evidence.get('contract_density_per_1k')}/1k, "
              f"question {evidence.get('question_density_per_1k')}/1k, "
              f"matrix={evidence.get('question_table_found')})")
        print(f"  requirements: {len(requirements)}")
        for req in requirements[:3]:
            print(f"    {req['ref']:<6} [{req['extracted_by']}] {req['question'][:72]}")
        if requirements:
            out = Path(arg).with_suffix(".requirements.json")
            out.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
            print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
