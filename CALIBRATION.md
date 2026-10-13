# Calibration record

How the matching thresholds were set, and why the first version's numbers were
not trustworthy. Kept because the headline figure is only meaningful if the way
it was measured is visible.

## Method

All 35 requirements in the sample ITT were read by hand against the 18-answer
library and labelled REUSE / PARTIAL / GAP before the scoring was tuned
(`gold_labels.json`). The labels were not edited afterwards, so the evaluation
is not fitted to the result it reports.

`evaluate.py` scores the tool output against those labels. The metric that
matters is **precision on REUSE**: of the answers the tool tells the bid team to
reuse, how many genuinely are. A false REUSE is the expensive failure — it means
an unanswered requirement ships inside a submission nobody re-checked. A false
GAP only costs someone reading a question they didn't need to.

## Version 1 — rejected

Raw IDF-weighted token overlap, thresholds 0.42 / 0.22.

| | |
|---|---|
| Reported reusable | 26 of 35 (74%) |
| Actually reusable | 19 of those 26 |
| **REUSE precision** | **73%** |
| Real gaps called REUSE | 7 |

The 74% headline was wrong, and wrong in the dangerous direction. Three examples
of what it was doing:

- **5.1 Social value** (10% weighting, the single largest question in the ITT) was
  matched to the *UK service desk* answer. Both texts contain "deliver",
  "contract" and "customer".
- **6.4 Acceptance of contract terms** was matched to the *staff vetting* answer.
- **1.6 Modern Slavery Act statement** was matched to the *ISO 27001* answer, on
  the shared word "compliance".

The cause was the IDF form. `n / (1 + df)` still gives weight near 1 to a word
appearing in most answers, so a match could be carried entirely by bid
boilerplate. With short questions there was little else to outvote it.

## Version 2 — current

Three changes:

1. **Log IDF.** `log(n / (1 + df))` drives common vocabulary to zero weight
   instead of near one.
2. **Distinctive-token floor.** A match must share at least two tokens that
   appear in no more than a third of the library (one token for PARTIAL). A high
   overlap ratio built only from boilerplate no longer qualifies.
3. **Subject-area gate.** Every stored answer is classified with the same routing
   rules as the requirements. A cross-area match is penalised by 55%, so a
   security answer cannot win a sustainability question on wording alone.

An intermediate attempt used the *median* IDF as the distinctive-token cutoff.
That was too strict — it admitted only words appearing in exactly one answer, and
threw out the genuine Windows 11 (2.2) and data erasure (4.3) matches as gaps.
Precision was perfect and recall collapsed to 48%. The third-of-library cutoff
recovered those without readmitting any false matches.

## Result

| | v1 | v2 |
|---|---|---|
| REUSE precision | 73% | **95%** |
| REUSE recall | 95% | **95%** |
| Correct source answer identified | — | **100%** (19/19) |
| Real gaps wrongly called REUSE | 7 | **0** |

The one remaining false REUSE is 2.6, where the tool proposes the catalogue
answer for lifecycle refresh scheduling. Manual review called that partial rather
than full coverage. It is a disagreement about depth, not subject, so it costs an
edit rather than a miss.

PARTIAL remains deliberately noisy: 8 true gaps land there. That is the intended
direction of error — PARTIAL means "a human should look", and the cost of being
wrong is one person reading one question.

## What this does not yet do

- Only parses the numbered-paragraph ITT format. Real documents arrive as PDF,
  Word and spreadsheet response templates, which is the next parsing problem.
- Lexical matching only. Semantic embeddings would catch rewording the token
  overlap misses, at the cost of needing a model call per requirement.
- No draft generation. Every answer shown is one a human already wrote and
  approved; the tool only decides which one to put in front of them.

---

# Part 2 — the real-document test

The calibration above was done against one synthetic Markdown file with tidy
numbered questions. That file was written to be parseable, which made the
measurement worthless as evidence about real tenders. So the parser was run
against ten genuine documents pulled from the Contracts Finder OCDS API.

## What happened

| | |
|---|---|
| Phantom requirements from documents containing no questions | **358** |
| Requirements found in the two documents that do contain questions | **0** |

Precision 0%. Recall 0%.

Two separate failures, both invisible in the synthetic test:

**It mined contracts.** `lincs_journey.pdf` is a Lincolnshire County Council
call-off contract. It has 314 numbered items and not one of them is a question —
they are contract clauses. The parser extracted 188 of them as requirements to
answer. `creative_c.pdf` (scheme rules) gave another 131. A bid team handed that
output would have a work queue full of clauses nobody has to respond to.

**It missed every real question.** Real question sets are not numbered
paragraphs. They are tables: `Heading | Question | Requirement` in a Word
response template, or `Q1A | Organisation Overview | Max Score` in a question
schedule. The parser read paragraphs only, so it found nothing in either of the
documents that actually contained questions.

## What changed

`ingest.py` now does two things the first version did not.

**Classify before extracting.** Each document is scored for contract language
("the Supplier shall", "notwithstanding", "in accordance with Clause") and
question language ("please describe", "max score", "weighting") per 1000 words,
and checked for a question matrix. A document is only mined if it is a question
set. Contracts are refused with a message saying why.

**Three extraction strategies, chosen by structure.** A table matrix (the
dominant real format), labelled questions in running text, and numbered
paragraphs. Numbered paragraphs now additionally require the item to actually ask
for something — the guard whose absence caused the contract mining.

Two smaller fixes came out of the real documents: a scoring rubric
(`4 = excellent, 3 = good`) was being read as a question table, so a question
reference now has to carry a dot or a section letter rather than being a bare
digit; and real PDFs fragment one question set across a dozen section tables, so
every qualifying table is collected rather than only the largest.

## Result on the same ten documents

| | before | after |
|---|---|---|
| Classification accuracy | — | **70%** (7/10) |
| Contracts correctly refused | 0/2 | **2/2** |
| Requirements recall | 0/81 | **80/81 (99%)** |
| Phantom requirements | **358** | **1** |

The three misclassifications are all the same error in the same direction:
prose documents called question sets. That is the harmless direction — extraction
then finds nothing, so the cost is a document opened unnecessarily. No contract
was misclassified as a question set, which is the error that matters.

The one missed requirement is in `creative_a.pdf`, where PDF table extraction
splits a section table across a page break and loses a row. The Word version of
the same tender extracts all 59.

## A result worth reading correctly

Running the real Creative United ITT against the answer library gives 4 reuses
out of 59. That is not a failure — the library holds IT reseller answers and
that tender is for a musical instrument finance scheme. The tool is correctly
reporting that this bid cannot be built from this answer bank. Reuse rate is a
property of the pairing, not a score the tool is trying to maximise.
