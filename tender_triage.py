#!/usr/bin/env python3
"""
tender_triage.py — ITT requirement extraction and answer-reuse triage.

Takes a raw tender document and an existing answer library, and produces:
  1. A structured register of every individual requirement
  2. Pass/Fail vs weighted classification, with the weighting captured
  3. Category and owning-team routing
  4. Best-match reusable answer with a reuse confidence score
  5. A gap list: the requirements nobody can answer from existing material

No API key, no network, deterministic output. Standard library only for .md
and .txt input; .pdf and .docx input need pdfplumber and python-docx.

Accepts .pdf, .docx, .md and .txt. Documents are loaded and classified by
ingest.py, which rejects contracts and specifications before extraction so the
work queue contains questions rather than clauses.

Usage:
    python tender_triage.py <tender document> answer_library.json
"""

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Requirements are numbered like "1.1", "4.3" at the start of a line.
REQ_PATTERN = re.compile(r"^(\d+\.\d+)\s+(.*)", re.MULTILINE)
SECTION_PATTERN = re.compile(r"^##\s+SECTION\s+(\d+)\s*[—\-–]\s*(.+)$", re.MULTILINE)

WEIGHT_PATTERN = re.compile(r"weighted\s+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
WORDCAP_PATTERN = re.compile(r"max\s+(\d+)\s+words", re.IGNORECASE)
PASSFAIL_PATTERN = re.compile(r"pass\s*/\s*fail", re.IGNORECASE)

# Category routing. First list that scores highest wins.
CATEGORIES = {
    "Security & Data Protection": {
        "owner": "Information Security",
        "terms": ["security", "cyber", "iso 27001", "penetration", "vulnerability",
                  "breach", "incident", "personal data", "erasure", "vetting",
                  "bpss", "clearance", "data residency", "access control"],
    },
    "Service & Support": {
        "owner": "Service Delivery",
        "terms": ["service desk", "service level", "sla", "incident", "resolution",
                  "warranty", "repair", "account management", "escalation",
                  "reporting", "management information", "support", "service credit"],
    },
    "Technical Solution": {
        "owner": "Technical Services",
        "terms": ["device", "laptop", "desktop", "tablet", "catalogue", "windows",
                  "intune", "provisioning", "imaging", "asset tag", "configuration",
                  "enrolment", "estate", "migration", "supply chain", "lifecycle",
                  "refresh"],
    },
    "Social Value & Sustainability": {
        "owner": "Sustainability",
        "terms": ["social value", "circular", "reuse", "refurbish", "recycle",
                  "carbon", "net zero", "environmental", "iso 14001", "packaging",
                  "apprenticeship", "skills", "local", "employment"],
    },
    "Commercial": {
        "owner": "Commercial",
        "terms": ["pricing", "price", "margin", "open book", "cost", "savings",
                  "added value", "terms and conditions", "schedule", "value"],
    },
    "Corporate & Compliance": {
        "owner": "Bid Team",
        "terms": ["registered office", "company registration", "audited accounts",
                  "financial standing", "modern slavery", "legal entity",
                  "certificate", "accreditation"],
    },
    "Mobilisation": {
        "owner": "Project Management",
        "terms": ["mobilisation", "transition", "incumbent", "go-live", "onboard",
                  "implementation", "week window"],
    },
    "Innovation & AI": {
        "owner": "AI & Transformation",
        "terms": ["copilot", "artificial intelligence", "automation", "emerging technology",
                  "innovation", "microsoft 365", "adoption"],
    },
}

STOPWORDS = {
    "the", "and", "for", "you", "your", "our", "with", "that", "this", "would",
    "will", "are", "any", "all", "from", "have", "has", "how", "please", "describe",
    "provide", "state", "confirm", "including", "approach", "within", "over",
    "under", "must", "should", "may", "which", "their", "them", "they", "where",
    "what", "each", "been", "into", "than", "then", "also", "its", "not", "but",
    "contract", "authority", "bidders", "bidder", "organisation", "term", "terms",
    "max", "words", "weighted", "pass", "fail", "section", "please",
}

# Calibrated against a manually labelled pass over the sample ITT (see
# CALIBRATION.md). The first pass used raw token overlap and claimed 74% of the
# document was reusable; manual review showed that was mostly common bid
# vocabulary matching across unrelated subject areas. These thresholds, plus the
# distinctive-token floor and the subject-area gate below, are set to minimise
# false REUSE — a wrongly confident match is worse than a flagged gap, because
# the gap gets written and the false match gets shipped.
REUSE_STRONG = 0.50     # Lift and edit
REUSE_PARTIAL = 0.30    # Useful starting point only
MIN_DISTINCTIVE_REUSE = 2    # Subject-specific matched tokens required to call it reusable
MIN_DISTINCTIVE_PARTIAL = 1  # ...and to call it a usable starting point
CROSS_AREA_PENALTY = 0.45  # Applied when answer and requirement sit in different areas


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def tokenise(text):
    """Lowercase alphanumeric tokens, stopwords and very short tokens dropped."""
    raw = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in raw if len(t) > 2 and t not in STOPWORDS]


def build_idf(documents):
    """Log inverse document frequency across the answer library.

    The linear form (n / (1 + df)) still gave weight ~1 to words appearing in
    most answers, so boilerplate like 'customer' or 'process' could carry a
    match on its own. Log form drives those to zero and leaves the weight with
    words that actually identify a subject.
    """
    n = len(documents)
    df = Counter()
    for doc in documents:
        for token in set(doc):
            df[token] += 1
    return {token: max(0.0, math.log(n / (1 + count))) for token, count in df.items()}


def distinctive(tokens, idf, cutoff):
    """Tokens carrying real subject signal rather than bid boilerplate."""
    return {t for t in tokens if idf.get(t, cutoff) >= cutoff}


def similarity(req_tokens, ans_tokens, idf, cutoff):
    """IDF-weighted coverage of the requirement, plus a count of how many of the
    matched tokens were subject-specific.

    Returns (score, distinctive_hit_count). The count is the guard: a high ratio
    built entirely out of boilerplate is not evidence of a usable answer.
    """
    if not req_tokens:
        return 0.0, 0
    req_set, ans_set = set(req_tokens), set(ans_tokens)
    default = idf.get("__unseen__", 0.0)
    total = sum(idf.get(t, default) for t in req_set)
    if total <= 0:
        return 0.0, 0
    matched = req_set & ans_set
    hit = sum(idf.get(t, default) for t in matched)
    return hit / total, len(distinctive(matched, idf, cutoff))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_sections(text):
    """Map requirement number prefix -> section title."""
    sections = {}
    for match in SECTION_PATTERN.finditer(text):
        sections[match.group(1)] = match.group(2).strip().title()
    return sections


def classify(text):
    """Score the requirement text against each category's term list."""
    lowered = text.lower()
    scores = {}
    for name, spec in CATEGORIES.items():
        score = sum(2 if " " in term else 1
                    for term in spec["terms"] if term in lowered)
        scores[name] = score
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "Unclassified", "Bid Team"
    return best, CATEGORIES[best]["owner"]


def parse_requirements(text):
    """Extract one record per numbered requirement."""
    sections = parse_sections(text)
    requirements = []

    for match in REQ_PATTERN.finditer(text):
        ref = match.group(1)
        body = match.group(2).strip()

        weight_match = WEIGHT_PATTERN.search(body)
        wordcap_match = WORDCAP_PATTERN.search(body)
        is_passfail = bool(PASSFAIL_PATTERN.search(body))

        # Strip the trailing scoring bracket from the question text itself.
        question = re.sub(r"\s*\([^)]*(?:Pass/Fail|Weighted)[^)]*\)\s*$", "", body,
                          flags=re.IGNORECASE).strip()

        mandatory = bool(re.search(r"\bMUST\b", body))

        requirements.append({
            "ref": ref,
            "section": sections.get(ref.split(".")[0], "Unknown"),
            "question": question,
            "scoring": "Pass/Fail" if is_passfail else "Weighted",
            "weight_pct": float(weight_match.group(1)) if weight_match else None,
            "word_limit": int(wordcap_match.group(1)) if wordcap_match else None,
            "mandatory_language": mandatory,
        })

    return requirements


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_answers(requirements, library):
    answers = library["answers"]
    answer_tokens = [tokenise(a["title"] + " " + a["text"] + " " + " ".join(a["tags"]))
                     for a in answers]
    idf = build_idf(answer_tokens)

    # A token is subject-specific if it appears in no more than a third of the
    # answer library. The median was tried first and was far too strict: it only
    # admitted words appearing in exactly one answer, so real matches like the
    # Windows 11 and data erasure answers were being thrown out as gaps.
    n_answers = len(answers)
    cutoff = math.log(n_answers / (1 + n_answers / 3)) if n_answers else 0.0

    # Each stored answer is classified with the same routing rules as the
    # requirements, so a match can be rejected for being in the wrong subject
    # area even when the wording overlaps.
    answer_areas = [classify(a["title"] + " " + a["text"] + " " + " ".join(a["tags"]))[0]
                    for a in answers]

    for req in requirements:
        category, owner = classify(req["question"])
        req["category"] = category
        req["owner"] = owner

        req_tokens = tokenise(req["question"])

        scored = []
        for i, tokens in enumerate(answer_tokens):
            raw, hits = similarity(req_tokens, tokens, idf, cutoff)
            cross_area = (answer_areas[i] != category and category != "Unclassified")
            score = raw * CROSS_AREA_PENALTY if cross_area else raw
            scored.append((score, hits, cross_area, answers[i]))

        scored.sort(key=lambda row: row[0], reverse=True)
        best_score, best_hits, best_cross, best_answer = scored[0]

        if best_score >= REUSE_STRONG and best_hits >= MIN_DISTINCTIVE_REUSE:
            status = "REUSE"
        elif best_score >= REUSE_PARTIAL and best_hits >= MIN_DISTINCTIVE_PARTIAL:
            status = "PARTIAL"
        else:
            status = "GAP"

        req["status"] = status
        req["reuse_score"] = round(best_score, 3)
        req["distinctive_hits"] = best_hits
        req["cross_area_match"] = best_cross
        req["best_match_id"] = best_answer["id"] if status != "GAP" else None
        req["best_match_title"] = best_answer["title"] if status != "GAP" else None

    return requirements


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarise(requirements):
    counts = Counter(r["status"] for r in requirements)
    total = len(requirements)

    gap_weight = sum(r["weight_pct"] or 0 for r in requirements if r["status"] == "GAP")
    covered_weight = sum(r["weight_pct"] or 0 for r in requirements if r["status"] == "REUSE")
    passfail_gaps = [r for r in requirements
                     if r["scoring"] == "Pass/Fail" and r["status"] == "GAP"]

    return {
        "total_requirements": total,
        "reuse": counts["REUSE"],
        "partial": counts["PARTIAL"],
        "gap": counts["GAP"],
        "reuse_pct": round(100 * counts["REUSE"] / total, 1) if total else 0,
        "scored_weight_covered_pct": round(covered_weight, 1),
        "scored_weight_at_risk_pct": round(gap_weight, 1),
        "passfail_gaps": [r["ref"] for r in passfail_gaps],
    }


def render_report(requirements, summary, source_name):
    lines = []
    lines.append(f"# Tender triage report — {source_name}\n")
    lines.append(f"**{summary['total_requirements']} requirements extracted.** "
                 f"{summary['reuse']} answerable from existing material "
                 f"({summary['reuse_pct']}%), {summary['partial']} partially, "
                 f"{summary['gap']} with no usable source.\n")
    lines.append(f"- Scored weighting covered by reusable answers: "
                 f"**{summary['scored_weight_covered_pct']}%**")
    lines.append(f"- Scored weighting with no source material: "
                 f"**{summary['scored_weight_at_risk_pct']}%**")
    if summary["passfail_gaps"]:
        lines.append(f"- Pass/Fail requirements with no source material: "
                     f"**{', '.join(summary['passfail_gaps'])}** "
                     f"(these sink the bid if unanswered)")
    lines.append("")

    # Work queue by owner
    lines.append("## Work queue by owning team\n")
    by_owner = {}
    for req in requirements:
        if req["status"] != "REUSE":
            by_owner.setdefault(req["owner"], []).append(req)
    for owner in sorted(by_owner, key=lambda o: -len(by_owner[o])):
        items = by_owner[owner]
        words = sum(r["word_limit"] or 0 for r in items)
        passfail = sum(1 for r in items if r["scoring"] == "Pass/Fail")
        load = f"up to {words} words to draft" if words else "no narrative"
        if passfail:
            load += f", {passfail} Pass/Fail"
        lines.append(f"**{owner}** — {len(items)} requirements, {load}")
        for req in items:
            flag = "GAP " if req["status"] == "GAP" else "part"
            lines.append(f"  - `{flag}` {req['ref']} — {req['question'][:90]}")
        lines.append("")

    # Full register
    lines.append("## Full requirement register\n")
    lines.append("| Ref | Section | Scoring | Wt % | Words | Category | Status | Source |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for req in requirements:
        lines.append(
            f"| {req['ref']} | {req['section']} | {req['scoring']} | "
            f"{req['weight_pct'] if req['weight_pct'] is not None else '—'} | "
            f"{req['word_limit'] or '—'} | {req['category']} | {req['status']} | "
            f"{req['best_match_id'] or '—'} |"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    itt_path = Path(sys.argv[1])
    lib_path = Path(sys.argv[2])
    library = json.loads(lib_path.read_text(encoding="utf-8"))

    import ingest
    verdict, extracted, evidence = ingest.extract(itt_path)

    if verdict != "QUESTION_SET":
        print(f"{itt_path.name}: {verdict} — not a question set, nothing to triage.")
        if verdict == "CONTRACT":
            print("  Numbered items in this document are contract clauses, not questions.")
        elif verdict == "UNREADABLE":
            print(f"  {evidence.get('reason')}")
        return 2
    if not extracted:
        print(f"{itt_path.name}: classified as a question set, but no requirements "
              f"could be extracted. Structure not recognised.")
        return 2

    requirements = match_answers(extracted, library)
    summary = summarise(requirements)

    Path("triage_output.json").write_text(
        json.dumps({"source": itt_path.name, "summary": summary,
                    "requirements": requirements}, indent=2),
        encoding="utf-8")
    Path("triage_report.md").write_text(
        render_report(requirements, summary, itt_path.name), encoding="utf-8")

    strategies = Counter(r.get("extracted_by", "numbered") for r in requirements)
    print(f"{summary['total_requirements']} requirements extracted "
          f"({', '.join(f'{v} by {k}' for k, v in strategies.most_common())})")
    print(f"  REUSE   {summary['reuse']:>3}  ({summary['reuse_pct']}%)")
    print(f"  PARTIAL {summary['partial']:>3}")
    print(f"  GAP     {summary['gap']:>3}")
    print(f"Scored weighting with no source material: {summary['scored_weight_at_risk_pct']}%")
    if summary["passfail_gaps"]:
        print(f"Pass/Fail gaps: {', '.join(summary['passfail_gaps'])}")
    print("\nWritten: triage_output.json, triage_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
