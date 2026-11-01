# Audit PR 5: the original picture at answer time, single-pass mode, behind a flag

**Before.** Single-pass mode answered from text only: the picture's v3 description and facts were in the passage, but
the model never saw the picture. The agent could call `inspect_figure`; single-pass mode had no equivalent.

**Now.** With `SINGLE_PASS_IMAGES=1`, single-pass mode attaches the original pictures behind the top-ranked figure passages
(at most `SINGLE_PASS_IMAGES_MAX`, default 3, in rank order, de-duplicated, skipping pictures missing from the store) to
the answer call as image content blocks, each preceded by a line naming its passage, and appends one sentence to
the system prompt telling the model what they are. Both engines: `research_qa/lc/answer.py` and
`research_qa/agent/single_pass.py` share `agent/tools.picture_blocks`. Only the Markdown+ arm has pictures; the Word arm
is unchanged by construction. Off by default: the no-image prompt is byte-identical to before.

**What the answer records.** A `look` step with the pictures sent (the tiles show them under "Looked at"), the
passages marked `inspected`, and the image tokens inside `input_tokens` of the answer call, since that is where
the provider bills them. The flag lands in every run's `config.json` (`retrieval.single_pass_images`), so a run with
pictures and a run without are an A/B on the Scorecard and in `report --run`.

**Structured data.** The chunk text already carries the structured picture facts (printed text, counts, labelled
numbers) and the chart series; nothing else needs attaching. The picture adds what a description cannot carry:
layout, colour, and anything the ingest-time model did not transcribe.

**Cost.** Every direct answer that retrieves a figure pays for up to three high-detail images. That is the trade
to measure: picture and image questions should gain; everything else gains nothing and pays tokens. Keep the flag
off for the cost comparison and on for a picture-question demo, or run both and compare.

**Tests.** `tests/test_single_pass_images.py`: block builder order, cap, de-duplication and missing assets; flag off
sends text only with the unchanged system prompt; flag on attaches the capped pictures, marks passages, records the
look step and the tokens; the Word arm never gets pictures; the flag is in the run's retrieval flags.

**Not verified live.** That gpt-5-mini through Azure OpenAI accepts the `image_url` content blocks in this exact
message shape inside LangChain's `HumanMessage` has not been exercised here; the same block shape is already used
by the ingest describe step and by `inspect_figure` through the OpenAI client, which have run live.

```bash
SINGLE_PASS_IMAGES=1 uv run python -m research_qa eval --arm markdown-plus --mode direct --types image,chart_picture --run direct-images-on
```
