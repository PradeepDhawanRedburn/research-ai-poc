# Word (docx) vs MarkItDown (A+): evaluation summary

- 88 questions, docx vs md input format
- Scoring: 1 = correct, 0.5 = partial, 0 = incorrect (graded automatically against the reference answer). 
- Retrieval: 1 = all golden evidence found in the retrieved chunks, 0.5 = some of it, 0 = none, `-` = question has no defined evidence (unanswerable).

## 1. TLDR

- **The two approaches scores are mostly the same:** mean answer score docx 91%, markdown 91%; retrieval 94% vs 95%. On 81 of 88 questions both got the same score; docx is higher on 4, markdown on 3.
- **Query cost is the same:** about 3.1 s per question in both, and now about the same tokens (3,702 vs 3,650 input tokens per question).
- **Recommendation:** A+ remains the target: the same accuracy as docx, standard tooling, and slightly cheaper ingest (34 vs 42 vision descriptions). There is no longer an accuracy reason to prefer the docx loader.


## 2. Scores per question

| id | type | docx retrieval | docx answer | markdown retrieval | markdown answer |
|---|---|---|---|---|---|
| MM04-01 | text_fact | 1 | 1 | 1 | 1 |
| MM04-02 | text_fact | 1 | 1 | 1 | 1 |
| MM04-03 | text_fact | 1 | 1 | 1 | 1 |
| MM04-04 | cross_document | 1 | 1 | 1 | 1 |
| MM04-05 | text_fact | 1 | 1 | 1 | 1 |
| MM04-06 | chart_native | 1 | 1 | 1 | 1 |
| MM04-07 | chart_native | 1 | 1 | 1 | 1 |
| MM04-08 | chart_native | 0.5 | 1 | 1 | 1 |
| MM04-09 | chart_picture | 1 | 1 | 1 | 1 |
| MM04-10 | relationship | 1 | 1 | 1 | 1 |
| MM04-11 | text_fact | 0 | 0 | 0 | 0 |
| MM04-12 | text_fact | 1 | 1 | 1 | 1 |
| MM04-13 | text_fact | 1 | 1 | 1 | 1 |
| MM04-14 | unanswerable | - | 1 | - | 1 |
| MM07-01 | text_fact | 1 | 1 | 1 | 1 |
| MM07-02 | text_fact | 1 | 1 | 1 | 1 |
| MM07-03 | text_fact | 1 | 1 | 0 | 1 |
| MM07-04 | text_fact | 1 | 1 | 1 | 1 |
| MM07-05 | cross_document | 1 | 1 | 1 | 1 |
| MM07-06 | chart_native | 1 | 1 | 1 | 1 |
| MM07-07 | chart_native | 1 | 1 | 1 | 1 |
| MM07-08 | chart_native | 1 | 1 | 1 | 1 |
| MM07-09 | chart_native | 1 | 1 | 1 | 1 |
| MM07-10 | chart_native | 1 | 1 | 1 | 1 |
| MM07-11 | chart_native | 1 | 1 | 1 | 1 |
| MM07-12 | chart_picture | 1 | 1 | 1 | 1 |
| MM07-13 | relationship | 1 | 1 | 1 | 1 |
| MM07-14 | text_fact | 0 | 0 | 0 | 0 |
| MM07-15 | unanswerable | - | 1 | - | 1 |
| EVO-01 | text_fact | 1 | 1 | 1 | 1 |
| EVO-02 | text_fact | 1 | 1 | 1 | 1 |
| EVO-03 | text_fact | 1 | 1 | 1 | 0.5 |
| EVO-04 | text_fact | 0 | 0.5 | 0 | 0.5 |
| EVO-05 | text_fact | 1 | 1 | 1 | 1 |
| EVO-06 | text_fact | 1 | 1 | 1 | 1 |
| EVO-07 | text_fact | 1 | 1 | 1 | 1 |
| EVO-08 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-09 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-10 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-11 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-12 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-13 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-14 | table_lookup | 1 | 1 | 1 | 1 |
| EVO-15 | table_lookup | 1 | 1 | 1 | 0 |
| EVO-16 | symbol_matrix | 1 | 1 | 1 | 1 |
| EVO-17 | symbol_matrix | 1 | 1 | 1 | 1 |
| EVO-18 | symbol_matrix | 0.5 | 1 | 0.5 | 1 |
| EVO-19 | symbol_matrix | 1 | 1 | 1 | 1 |
| EVO-20 | symbol_matrix | 1 | 0 | 1 | 0.5 |
| EVO-21 | symbol_matrix | 1 | 1 | 1 | 1 |
| EVO-22 | symbol_matrix | 1 | 1 | 1 | 1 |
| EVO-23 | chart_picture | 1 | 1 | 1 | 1 |
| EVO-24 | relationship | 1 | 1 | 1 | 1 |
| EVO-25 | relationship | 1 | 1 | 1 | 1 |
| EVO-26 | text_fact | 1 | 1 | 1 | 1 |
| EVO-27 | unanswerable | - | 1 | - | 0 |
| CCEP-01 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-02 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-03 | text_fact | 1 | 0.5 | 1 | 0.5 |
| CCEP-04 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-05 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-06 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-07 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-08 | text_fact | 1 | 1 | 1 | 1 |
| CCEP-09 | table_lookup | 1 | 1 | 1 | 1 |
| CCEP-10 | table_lookup | 1 | 1 | 1 | 1 |
| CCEP-11 | table_lookup | 1 | 1 | 1 | 1 |
| CCEP-12 | table_lookup | 1 | 1 | 1 | 1 |
| CCEP-13 | table_lookup | 1 | 1 | 1 | 1 |
| CCEP-14 | chart_picture | 1 | 1 | 1 | 1 |
| CCEP-15 | chart_picture | 0.5 | 1 | 1 | 1 |
| CCEP-16 | relationship | 0.5 | 0 | 1 | 1 |
| CCEP-17 | relationship | 1 | 1 | 1 | 1 |
| CCEP-18 | text_fact | 1 | 1 | 1 | 0.5 |
| CCEP-19 | unanswerable | - | 1 | - | 1 |
| IMG-01 | image | 1 | 1 | 1 | 1 |
| IMG-02 | image | 1 | 1 | 1 | 1 |
| IMG-03 | image | 1 | 1 | 1 | 1 |
| IMG-04 | image | 1 | 1 | 1 | 1 |
| IMG-05 | image | 1 | 0.5 | 1 | 0.5 |
| IMG-06 | image | 1 | 1 | 1 | 1 |
| IMG-07 | image | 1 | 1 | 1 | 1 |
| IMG-08 | image | 1 | 0 | 1 | 1 |
| IMG-09 | image | 1 | 1 | 1 | 1 |
| IMG-10 | image | 1 | 0.5 | 1 | 0.5 |
| IMG-11 | image | 1 | 1 | 1 | 1 |
| IMG-12 | image | 1 | 1 | 1 | 1 |
| IMG-13 | image | 1 | 0.5 | 1 | 0.5 |
| **SUM** | **88 questions** | **79**/84 | **80.5**/88 | **79.5**/84 | **80**/88 |

**Takeaways:** answers sum to 80.5 (docx) vs 80 (markdown) out of 88, retrieval 79 vs 79.5 out of 84.

## 3. Scores per question type

| type | n | docx retrieval | docx answer | markdown retrieval | markdown answer |
|---|---|---|---|---|---|
| chart_native | 9 | 94% | 100% | 100% | 100% |
| chart_picture | 5 | 90% | 100% | 100% | 100% |
| cross_document | 2 | 100% | 100% | 100% | 100% |
| image | 13 | 100% | 81% | 100% | 88% |
| relationship | 6 | 92% | 83% | 100% | 100% |
| symbol_matrix | 7 | 93% | 86% | 93% | 93% |
| table_lookup | 13 | 100% | 100% | 100% | 92% |
| text_fact | 29 | 90% | 90% | 86% | 86% |
| unanswerable | 4 | - | 100% | - | 75% |
| **ALL** | 88 | 94% | 91% | 95% | 91% |

**Takeaways:**
- **image:** 81% (docs) vs 88% (md). Retrieval is 100%; errors come from reading the picture itself.
- **text_fact:** 90% vs 86%, including shared failures (report authors MM04-11, details in EVO-04 and CCEP-03).
- **unanswerable (4):** 100% vs 75%. The difference is one question, EVO-27 (FY28E EBITDA): docx declined, markdown gave €1,671m.
- **All other types** (chart_native, chart_picture, cross_document, table_lookup, relationship) are at or near 100% in both.

## 4. Time and tokens

1. Retrieval = query embedding + vector search + BM25 fusion; 
2. generation = the answering model call; 
3. total = retrieval + context assembly + generation. 
4. Tokens are those of the answering call (query embedding and grading not counted).

| metric | docx | markdown |
|---|---|---|
| retrieval time, mean [s] | 0.20 | 0.20 |
| generation time, mean [s] | 2.93 | 2.82 |
| total time, mean [s] | 3.14 | 3.03 |
| total time, p95 [s] | 6.04 | 6.13 |
| input tokens, mean | 3,702 | 3,650 |
| output tokens, mean | 50 | 51 |
| total tokens, sum over 88 questions | 330,165 | 325,674 |

**Takeaways:** time is practically identical. Docx input tokens per question fell from 3,894 in run 2 to 3,702 (a digitised chart is shorter than a vision description), so docx is now only +1% vs markdown. Markdown is unchanged. The remaining cost difference is at ingest (section 5), not at query time.

## 5. Summary: where the approaches differ and what to do about it

### Word (docx)

| area | what we see | cause | what to do |
|---|---|---|---|
| Tick/cross matrices | retrieval 93%, answers 86%; EVO-20 is still wrong (0) although the right chunk was retrieved | the matrix is still flattened into a single line with no rows or columns, and its headers are separated from the data (not changed in this round) | render docx tables as Markdown with headers (same renderer as the plugin). The retrieval gain since run 2 came without a table change; a likely reason is that digitised chart chunks are shorter than vision descriptions, but this was not tested |
| Ingest cost | 42 figures are described by the vision model (was 177) and 135 are digitised with the plugin's EMF reader; the index is 501k characters (was 581k) | the docx loader now reuses the plugin's EMF digitiser, so only pictures without digitised values go to the model | none; this is done |
| EMF charts | IMG-13 dropped to 0.5 (was 1): the Fig 4 chart now carries the digitiser's wrong values | the digitiser output for Fig 4 has negative values and no dates (25 of 135 digitisations are "lower confidence", 27 have unnamed series) | see the shared table |
| Query tokens | 3,702 input tokens per question on average | chunks with a median of 889 characters | none needed |

### MarkItDown (A+)

| area | what we see | cause | what to do |
|---|---|---|---|
| Tick/cross matrices | retrieval 93%, answers 93% (was 29% and 36%); EVO-20 is partial (0.5) | the table chunks now carry their caption ("Fig 7: ..."), so a question that names the figure finds the matrix | none for the matrices |
| EMF charts | IMG-13 is partial (0.5); digitised values are sometimes wrong (Fig 4: negative values, no dates) | same digitiser issue as in docx | see the shared table |
| Ingest cost | 34 pictures are described by the vision model; the index is 458k characters | the plugin reads Excel charts pasted as EMF from their vector drawing, without a model (135 of 148 EMFs in the four reports) | none |
| Query tokens | 3,650 input tokens per question on average | chunks with a median of 804 characters | none needed |

Ingest cost is counted here as the number of vision calls and the amount of text. Ingest time and tokens were not measured.

### Shared by both approaches

| area | what we see (docx / markdown answer score) | cause | what to do |
|---|---|---|---|
| Wrong digitised values | IMG-13 (0.5 / 0.5) | the EMF digitiser returns wrong values for some charts (Fig 4: negative values, no dates); now used by both loaders | when digitisation has low confidence, negative values or unnamed series, do not output numbers; send the image to the vision model instead (or give both, flagged) |
| Reading pictures | IMG-05 line colours (0.5 / 0.5), IMG-08 counting tiles (0 / 1), IMG-10 reading the axis (0.5 / 0.5) | limits of the vision model, independent of the loader | ingest prompt that forces a description of legend, colours and counts; higher resolution or cropping; a stronger vision model |
| Text questions | MM04-11 authors (0 / 0), MM07-14 (0 / 0), EVO-04 (0.5 / 0.5), CCEP-03 incomplete answer (0.5 / 0.5), EVO-27 (1 / 0; the reference answer looks wrong, see section 3) | not investigated; for MM04-11 both have retrieval 0 of 2, so the evidence does not reach the top 8 | first check the reference answers against the PDFs; add the analysts' names to every chunk header; strengthen the "decline when the sources do not say" rule in the prompt |
| Czech Republic matrix | EVO-20 (0 / 0.5) | docx: the model reads Hacksaw as not available too (wrong), consistent with the flattened matrix; markdown: Evolution and Hacksaw are right but the answer omits that Evolution is restricted in all three checks | docx: render the table as Markdown; markdown: none needed |