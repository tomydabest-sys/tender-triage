# Tender triage

Reads a UK public sector tender document, extracts every individual requirement,
and tells the bid team which ones are already answerable from material they have
written before — and, more usefully, which ones are not.

Some terms, for anyone outside bidding. An **ITT** (Invitation to Tender) is the
document a public body issues when it wants to buy something: a set of questions
and requirements that each supplier answers in writing. Some are **Pass/Fail**
(fail one and the bid is rejected); the rest are **weighted** (scored, each worth
a stated percentage of the total). An **answer library** is a bid team's bank of
answers that were written and approved for previous bids.

## Why

An IT reseller bidding a four-year local authority framework answers 30 to 80
scored questions per submission. Most of that content exists somewhere in a
previous bid. The expensive part is not the writing, it is working out which
previous answer applies, who owns the ones that don't exist yet, and discovering
on the final weekend that nobody has written the social value response that
carries 10% of the score.

Running this on the sample ITT in this repo (synthetic: a county council buying
end user devices and support, £6.4m over 4 years, 35 requirements) against the
sample answer library of 18 answers:

```
$ python3 tender_triage.py sample_itt.md answer_library.json
35 requirements extracted (35 by numbered)
  REUSE    20  (57.1%)
  PARTIAL  10
  GAP       5
Scored weighting with no source material: 21.0%
Pass/Fail gaps: 1.5

Written: triage_output.json, triage_report.md
```

Each requirement gets one of three verdicts: **REUSE** (an approved answer
exists — lift and edit), **PARTIAL** (there is a starting point, and a person
should look) or **GAP** (nothing to start from).

Twenty questions have an approved answer ready. Five have nothing, carrying 21
points of scored weighting, and one of them is Pass/Fail — a requirement that
disqualifies the bid outright if it goes in blank. (The `1.5` on the last line is
that requirement's reference number, not a count.) That is the output: a work
queue, split by owning team, with the commercially dangerous items at the top.

The weighting figure is a plain sum of the percentages stated in the document.
The sample ITT's weightings add up to 123% rather than 100%, so its 21% is 21
points out of 123.

[`triage_report.md`](triage_report.md) is the full report from that run.
`triage_report.html` is the same report as a single page; GitHub shows HTML files
as source, so download it and open it in a browser.

## Running it

```bash
git clone https://github.com/tomydabest-sys/tender-triage.git
cd tender-triage
pip install -r requirements.txt   # pdfplumber, python-docx: only needed for PDF and Word input
```

Needs Python 3 (checked on 3.14). The sample run uses only the standard library.
On Windows, use `python` if `python3` is not found.

```bash
python3 tender_triage.py sample_itt.md answer_library.json   # -> triage_report.md, triage_output.json
python3 report_html.py triage_output.json                    # -> triage_report.html
python3 evaluate.py triage_output.json gold_labels.json      # matching accuracy against the manual labels
```

To triage a real tender, pass it in place of `sample_itt.md`, and your own answer
library in place of `answer_library.json` (same shape: a list of `answers`, each
with an `id`, `title`, `text` and `tags`). Output files are written to the
current directory and overwrite the previous run.

Accepts `.pdf`, `.docx`, `.md` and `.txt`. No API key, no model call, deterministic
output.

It refuses documents that are not question sets:

```
$ python3 tender_triage.py real/docs/lincs_journey.pdf answer_library.json
lincs_journey.pdf: CONTRACT — not a question set, nothing to triage.
  Numbered items in this document are contract clauses, not questions.
```

(`lincs_journey.pdf` is one of the real test documents described below. They are
not included in this repo.)

## How it decides

Requirements are extracted with their scoring type (Pass/Fail or weighted), the
weighting, the word limit and any mandatory MUST language. Each is routed to a
subject area and owning team, then scored against the answer library using
log-IDF weighted token coverage, with a distinctive-token floor and a penalty for
matches that cross subject areas.

In plain terms: words that appear in most stored answers ("customer", "process")
count for little or nothing, a match has to share words that are specific to a
subject, and a match from a different subject area (a security answer for a
sustainability question) is marked down.

Routing is by keyword list. The lists in `tender_triage.py` are written for IT
supply and support bids and would need changing for another sector.

## How good it is

Measured, not asserted. Two things are measured separately because they fail
differently.

**Ingestion** — tested against ten files downloaded through the API of Contracts
Finder (the UK government's public register of contract notices), each opened and
checked by hand:

| | |
|---|---|
| Classification accuracy | 70% (7/10) |
| Contracts correctly refused | 2/2 |
| Requirement recall | 99% (80/81) |
| Phantom requirements from non-question documents | 1 |

Eight of the ten are readable documents. The other two are downloads that failed
— an error page saved under a `.pdf` or `.docx` name — which the tool is expected
to report as unreadable; they are two of the seven it got right. Recall compares
how many requirements were extracted from each question set with how many were
counted by hand, not item by item. `real/ground_truth.json` lists what each file
is and what was counted.

**The ten files are not in this repo.** They are other organisations' tender
documents and are not redistributed here, so `evaluate_ingest.py` has nothing to
run on in a fresh clone and the ingestion figures cannot be reproduced from this
repo alone. To run it on documents of your own, put them in `real/docs/` and list
each one in `real/ground_truth.json` with its true verdict and question count:

```bash
python3 evaluate_ingest.py real/docs real/ground_truth.json
```

Contracts Finder publishes notices through its OCDS search API
(`https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search`).
Attachment links in the results return 403 unless the request carries a browser
`User-Agent` header.

**Matching** — tested against 35 hand-written labels on the synthetic ITT:

| | |
|---|---|
| REUSE precision | 95% (19/20) |
| REUSE recall | 95% (19/20) |
| Correct source answer identified | 100% (19/19) |
| Real gaps wrongly called reusable | 0 |

The three commands under *Running it* reproduce this table. The labels were
written before the scoring was tuned and not edited afterwards, but the
thresholds were then tuned against those same 35 labels, on one synthetic
document. So this shows the matcher works on that document. It is not a
measurement of matching accuracy on real tenders, and this repo does not contain
one.

Both numbers started far worse. The first matcher reported 74% of the document
reusable at 73% precision, having matched the largest question in the bid to an
unrelated answer. The first parser scored 0% precision and 0% recall on real
documents — it extracted 358 requirements from contracts containing no questions,
and nothing at all from the two documents that did. `CALIBRATION.md` records both
failures, what caused them and what fixed them.

## Files

| | |
|---|---|
| `ingest.py` | Loads PDF/Word/Markdown, classifies the document, extracts requirements |
| `tender_triage.py` | Routing, matching and reporting |
| `report_html.py` | Renders the triage output as a single HTML page |
| `evaluate.py` | Scores matching against the manual labels |
| `evaluate_ingest.py` | Scores ingestion against the real-document ground truth (needs the documents, which are not included) |
| `real/ground_truth.json` | Hand-checked verdict and question count for each real document |
| `sample_itt.md` | Synthetic ITT, structured on UK public sector conventions |
| `answer_library.json` | 18 reusable answers standing in for a bid team's answer bank |
| `gold_labels.json` | Manual labels, written before the scoring was tuned |
| `triage_report.md`, `triage_report.html` | Output of the sample run, kept as an example |
| `CALIBRATION.md` | Why the thresholds are what they are |
| `requirements.txt` | The two packages needed for PDF and Word input |
