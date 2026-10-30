# The golden question set, v1.0 (verified 30 October 2026)

`golden/questions.yaml` holds 122 questions over the four sample reports, each with the expected answer and the
evidence strings the answer rests on. The draft (v0.1, 88 questions, engineer-written and never checked) is what the
Phase 0 numbers were measured on; v1.0 is what the rebuild's evaluation and scorecard run on.

## How it was verified

1. The four Word files were run through the rebuilt ingest offline (`research_qa.ingest.pipeline.ingest`, vision off)
   into a local store, producing the Markdown+, the facts file (native chart series, digitised series, matrix cells) and
   the chunks for each report.
2. Every expected answer was read against that output: prose and tables in the Markdown, chart values in the facts
   file (digitised values quoted as approximate, as the facts mark them), matrix states cell by cell.
3. Every picture question was checked by looking at the picture: PNG assets directly, EMF assets rendered to PNG
   (Fig 4 of the April Money Map; Fig 1, 2, 6, 8, 9 and 14 of the Evolution report; Fig 8, 9, 10 and 11 of the
   CCEP report; the Fanta cans, the Aldi price match and the two Iceland shelf photos).
4. Every evidence string was tested against the normalised Markdown+ with the same matching the evaluation uses
   (`research_qa.evaluate.retention`): 122 of 122 questions have all their evidence present; for cross-document
   questions each of the two reports contributes at least one item.
5. The file is loaded and checked for unique ids, valid types, evidence on every answerable question, and the exact
   wording "The documents do not say." with no evidence on every unanswerable one.

Not verified independently: the published PDFs. The checks above prove the answers are what the Word files say,
through the conversion; an analyst should still sign off the set before it is used for anything outside this project.

## Counts

| type | RA01220 | RA01734 | RA02283 | RA02752 | total |
|---|---|---|---|---|---|
| chart_native | 4 | 10 | 0 | 0 | 14 |
| chart_picture | 1 | 1 | 2 | 3 | 7 |
| cross_document | 5 | 5 | 0 | 0 | 10 |
| image | 2 | 0 | 6 | 10 | 18 |
| relationship | 1 | 1 | 2 | 2 | 6 |
| symbol_matrix | 0 | 0 | 13 | 0 | 13 |
| table_lookup | 0 | 0 | 8 | 6 | 14 |
| text_fact | 7 | 7 | 9 | 11 | 34 |
| unanswerable | 1 | 2 | 1 | 2 | 6 |
| **all** | 21 | 26 | 41 | 34 | 122 |

Cross-document questions are counted under their primary report (`doc`); each also lists both Money Maps in `docs`.
Draft v0.1 had 88: text_fact 29, table_lookup 13, image 13, chart_native 9, symbol_matrix 7, relationship 6,
chart_picture 5, unanswerable 4, cross_document 2.

## Changes to existing questions

Every draft question survives. Of the 88, 60 are unchanged; 28 were corrected or tightened:

| id | what was wrong | what it is now |
|---|---|---|
| MM07-14 | Named one author; the July Money Map lists two analysts | "Who are the authors": Melissa Davies and Michael Taylor, both as evidence |
| MM04-14, MM07-15, EVO-27, CCEP-19 | Unanswerable answers worded four different ways, so the judge graded them inconsistently | Answer is exactly "The documents do not say."; EVO-27 now names Rothschild & Co Redburn's FY28E EBITDA estimate so the consensus-change charts (Fig 10, 11, which show 2028E) cannot be mistaken for it |
| MM07-10, MM07-11 | Chart data is stored as fractions (0.02, 0.0515) while the answer said per cent | Answer gives both forms; evidence pins the exact point (`2025-06-15: 0.02`, `2025-05-30: 0.0515`); M1 corrected to about 5.15% |
| MM04-06, MM04-07, MM04-15 | RA01220 has two figures numbered 27 (the native cyclically adjusted balance chart and a digitised ECB deposit rate chart); likewise two Fig 26 | Question names the caption as well as the number |
| MM04-08, MM07-06, MM07-07, MM07-08, MM07-09 | Evidence was a bare number that could match elsewhere | Evidence is the exact series point as rendered (`max 6.7 at 2022-06-15`, `2025-07-16: 0.58`) |
| EVO-15 | Answer "95.0" where the table says 95 | 95 |
| EVO-16, EVO-17, EVO-21, EVO-22 | Evidence was a whole matrix row (fragile to any column change) | Caption plus the first cells of the row |
| EVO-18, EVO-20, EVO-21 | Answers said "available" without naming the symbol | Answers name the tick or cross and the check dates |
| EVO-26, CCEP-18 | Dates in ISO form and a "UK time" assumption not in the report | Dates written out; time as printed (06:30) |
| CCEP-14, CCEP-15 | Answer did not say what the series measure | "share of UK cola retail volumes"; categories unchanged |
| IMG-06 | "all are Orange, Zero Sugar" lost what each can actually says | Vive (Orange, Zero Sugar), Old Fanta (Fanta Zero, Orange), New Fanta (Fanta Orange, Zero Sugar) |
| IMG-08 | Count without a breakdown | 10 tiles: 4, 3, 2, 1 by row, all Pragmatic Play |
| IMG-09, IMG-10, IMG-12 | Loose wording | Exact bet range and on-screen text; SEK 4,400m of about 5,500m; peak just under 1,800,000 on the $m axis |

Two facts worth knowing when reading results: RA01220 repeats the figure numbers 26 and 27 (two different charts
each), so a lookup by figure number alone is ambiguous there, and the Evolution matrices (Fig 3, 4, 7) are Word
tables, not pictures, so their states come from the extractor, not from vision.

## Dropped questions

None. Every draft question is supported by its report.

## New questions (34)

Cross-document, both Money Maps (`docs: [RA01220, RA01734]`):

- MMX-01 ECB next meeting: April expected a 25bp cut to 2.0% on 5 June; July expected a hold at 2.0% on 24 July.
- MMX-02 Fed next meeting: on hold in both editions (6-7 May; 29-30 July at 4.25-4.5%).
- MMX-03 Bank of England: April expected a 25bp cut to 4.25% on 8 May; July expected a cut to 4% in August.
- MMX-04 UK CPI: 2.6% for March (April edition), 3.6% for June (July edition).
- MMX-05 Atlanta Fed wage tracker: 4.2% for June 2025 (July, Fig 14); approximately 4.3% for March 2025 (April, Fig 18, digitised).
- MMX-06 Manufacturing ISM: approximately 49.0 for March 2025 (April, Fig 20) and 49.0 for June 2025 (July, Fig 16).
- MMX-07 ECB Deposit rate level: 2.25% after the 17 April cut; 2.0% after the June cut.
- MMX-08 July's 30% tariff scenario and April's year-end Deposit rate expectation of 1.5%.

Symbol matrices (Evolution report):

- EVO-28 Count of countries with a red cross for Evolution in October 2025: seven (Czech Republic, Denmark, Greece, Latvia, Netherlands, Romania, Sweden).
- EVO-29 Operators in Italy where Pragmatic Play is available: one (Boomerang); BC.Game is N/A.
- EVO-30 Italy operator with all three suppliers available: Boomerang only.
- EVO-31 Countries where Hacksaw was restricted in October 2025: Cyprus, Denmark, France, Greece, Malta, Spain, Sweden.
- EVO-32 France: Evolution available on seven of 12 sites.
- EVO-33 Evolution's availability did not change between the May, July and October checks.

Chart values that `lookup_facts` should answer exactly:

- MM07-16 Non-Manufacturing ISM June 2025: 50.8.
- MM07-17 Eurozone core CPI June 2025: 2.3% (0.023).
- MM07-18 Most negative 10yr-minus-2yr slope: -0.93 in June 2023.
- MM07-19 ECB Deposit Facility Rate July 2025 (Fig 24): 2.0%.
- MM04-15 Fig 27 Eurozone 2030: -3.7% of GDP.
- CCEP-20 Pepsi Max share December 2024 (Fig 8, digitised): approximately 34%.
- CCEP-21 CCE 1997-2015 organic growth (Fig 6 table): 3.4%.

Pictures (answers read from the picture; evidence is the caption):

- CCEP-22 Fig 40 sign: "2 FOR £4.50", "£2.75 EACH", "SAVE £1".
- CCEP-23 Fig 41 shelf: Pepsi Max 3 litre buy 2 get 1 free at £2.95; Coca-Cola 1.75 litre any 2 for £4.50, £2.65 each.
- CCEP-24 Fig 23: Coca-Cola Original Taste 8x330ml (4.3, 33 reviews) and 1.75L (4.3, 18 reviews), both Aldi Price Match.
- CCEP-25 Fig 9: CCEP sparkling 83%, energy 10%; market 37% and 6%.
- EVO-34 Fig 1 Shuffle message: "Sorry, the game is not available for your jurisdiction."
- EVO-35 Fig 2 group growth: 9% in 2026E, 10% in 2027E.

Metadata and text:

- EVO-36 Analyst Andrew Tam; market cap SEK146bn.
- CCEP-26 Analyst Charlie Higgs; Buy; USD89; priced 19 November 2025.
- MM07-20 Publication date 23 July 2025.
- CCEP-27 Ball Corporation owns the can plant next to Wakefield.
- MM07-21 A 30% tariff would probably be met with 50bp of Deposit rate cuts.

Unanswerable traps:

- CCEP-28 Fig 70 of the CCEP report (the report has 61 figures).
- MM07-22 Manufacturing ISM for December 2025 (the data ends in June 2025).

## Retention result on the rebuilt Markdown+ (no model)

| type | questions | retained | BM25 retrieved@5 |
|---|---|---|---|
| chart_native | 14 | 14/14 | 14/14 |
| chart_picture | 7 | 7/7 | 7/7 |
| image | 18 | 18/18 | 18/18 |
| relationship | 6 | 6/6 | 6/6 |
| symbol_matrix | 13 | 13/13 | 13/13 |
| table_lookup | 14 | 14/14 | 14/14 |
| text_fact | 34 | 34/34 | 31/34 |
| cross_document | 10 | 10/10 across both reports | see note |

The retention module currently reads one report per question (`doc`); the eight new MMX questions need both, so it
reports them as lost until it honours `docs`. Checked against the union of the two Money Maps, all 10 cross-document
questions retain every evidence item. The three text facts missed at BM25@5 are retrieval-ranking effects on a plain
keyword search, not lost evidence.

## What could not be verified here

- Vision descriptions (no model offline): the picture answers were read by a person from the rendered pictures; the
  descriptions the system produces will be judged against them.
- Two EMF charts the quality gate withholds (CCEP Fig 9 and 10) and the Treasury General Account chart (April Money
  Map Fig 4) were rendered and read by eye; their values in the answers are approximate by design.
