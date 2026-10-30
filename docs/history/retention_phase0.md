# Evidence retention and retrieval, by pipeline and question type

Questions: 71 answerable (unanswerable ones are for the LLM stage). k = 5.

Retained = all evidence strings present anywhere in the pipeline's full output. Retrieved@5 = all evidence strings present in the top-5 BM25 chunks for the question.

| Type | A retained | A retrieved@5 | A+ retained | A+ retrieved@5 | C retained | C retrieved@5 | C+route retained | C+route retrieved@5 |
|---|---|---|---|---|---|---|---|---|
| chart_native | 0/9 | 0/9 | 9/9 | 8/9 | 9/9 | 7/9 | 9/9 | 9/9 |
| chart_picture | 2/5 | 2/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| cross_document | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| relationship | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 |
| symbol_matrix | 0/7 | 0/7 | 7/7 | 4/7 | 7/7 | 6/7 | 7/7 | 7/7 |
| table_lookup | 13/13 | 12/13 | 13/13 | 12/13 | 13/13 | 13/13 | 13/13 | 13/13 |
| text_fact | 29/29 | 27/29 | 29/29 | 28/29 | 29/29 | 26/29 | 29/29 | 26/29 |
| **All** | **52/71** | **49/71** | **71/71** | **65/71** | **71/71** | **65/71** | **71/71** | **68/71** |

## Size of what the model would see

| Doc | A full-doc tokens | A+ full-doc tokens | C full-doc tokens | C+route full-doc tokens | A avg top-5 context tokens | A+ avg top-5 context tokens | C avg top-5 context tokens | C+route avg top-5 context tokens |
|---|---|---|---|---|---|---|---|---|
| RA01220 | 7768 | 34988 | 34988 | 34988 | 1808 | 2113 | 1231 | 1215 |
| RA01734 | 8319 | 31047 | 31047 | 31047 | 1742 | 2165 | 1431 | 1435 |
| RA02283 | 12812 | 13028 | 13029 | 13029 | 2330 | 2124 | 1214 | 1245 |
| RA02752 | 31307 | 43976 | 43977 | 43977 | 2247 | 2307 | 1513 | 1513 |

Token counts use tiktoken cl100k_base when available, otherwise characters/4.

## Misses by pipeline (first evidence string missing)

### Pipeline A: 19 questions with lost evidence
- MM04-06 (chart_native, RA01220): missing `2030: -4.7`
- MM04-07 (chart_native, RA01220): missing `2026: -3.3`
- MM04-08 (chart_native, RA01220): missing `1.15145`
- MM04-09 (chart_picture, RA01220): missing `u-mich 1yr inflation expectations`
- MM07-06 (chart_native, RA01734): missing `median wage growth`
- MM07-07 (chart_native, RA01734): missing `6.7`
- MM07-08 (chart_native, RA01734): missing `manufacturing ism`
- MM07-09 (chart_native, RA01734): missing `10yr minus 2yr`
- MM07-10 (chart_native, RA01734): missing `cpi yoy`
- MM07-11 (chart_native, RA01734): missing `0.0515`
- MM07-12 (chart_picture, RA01734): missing `budget as written`
- EVO-16 (symbol_matrix, RA02283): missing `evolution cross red tick green cross red tick green tick green tick green tick green tick green cross red cross red cross red cross red`
- EVO-17 (symbol_matrix, RA02283): missing `pragmatic cross red n/a cross red`
- EVO-18 (symbol_matrix, RA02283): missing `pragmatic / october 2025`
- EVO-19 (symbol_matrix, RA02283): missing `denmark cross red cross red cross red cross red cross red`
- EVO-20 (symbol_matrix, RA02283): missing `czech republic cross red cross red cross red tick green tick green`
- EVO-21 (symbol_matrix, RA02283): missing `evolution tick green tick green tick green tick green cross red tick green tick green cross red cross red cross red cross red tick green`
- EVO-22 (symbol_matrix, RA02283): missing `hacksaw tick green cross red cross red cross red`
- CCEP-15 (chart_picture, RA02752): missing `water/sports`

### Pipeline A+: 0 questions with lost evidence

### Pipeline C: 0 questions with lost evidence

### Pipeline C+route: 0 questions with lost evidence
