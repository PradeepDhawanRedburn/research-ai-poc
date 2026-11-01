"""Single-pass mode streams the answer: partial text reaches the caller, first-token time is recorded, tokens still count."""
from dataclasses import replace

from research_qa.agent import single_pass as D
from research_qa.config import settings
from research_qa.lc import answer as A
from research_qa.schema import Chunk


class Chunky:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage

    def __add__(self, other):
        return Chunky(self.content + other.content, other.usage_metadata or self.usage_metadata)

class StreamingModel:
    def __init__(self):
        self.invoked = 0

    def stream(self, messages):
        for part in ("Three ", "cans ", "[1]."):
            yield Chunky(part)
        yield Chunky("", {"input_tokens": 700, "output_tokens": 9})

    def invoke(self, messages):
        self.invoked += 1
        return Chunky("Three cans [1].", {"input_tokens": 700, "output_tokens": 9})


def wire(monkeypatch, model, stream_flag=True):
    import research_qa.lc.models as models

    monkeypatch.setattr(models, "chat_model", lambda: model)
    monkeypatch.setattr(A, "settings", replace(settings, stream_single_pass=stream_flag, single_pass_images=False))
    chunks = [Chunk(id="D1-00001", doc_id="D1", chunk=1, kind="text", text="Report D1 three cans", source_file="D1.md")]
    monkeypatch.setattr(A, "_retrieve", lambda *a, **k: chunks)


def test_langchain_single_pass_streams_and_records_first_token(monkeypatch):
    model = StreamingModel()
    wire(monkeypatch, model)
    seen = []
    a = A.ask_single_pass("Which cans?", "markdown-plus", on_token=seen.append)
    assert seen == ["Three ", "Three cans ", "Three cans [1]."], "the text so far, each time it grows"
    assert a.text == "Three cans [1]." and a.input_tokens == 700 and a.output_tokens == 9
    assert 0 < a.first_token_s <= a.total_s and model.invoked == 0


def test_flag_off_uses_invoke(monkeypatch):
    model = StreamingModel()
    wire(monkeypatch, model, stream_flag=False)
    a = A.ask_single_pass("Which cans?", "markdown-plus")
    assert model.invoked == 1 and a.first_token_s == 0.0 and a.text == "Three cans [1]."


def test_stream_answer_falls_back_without_stream():
    class Plain:
        def invoke(self, messages):
            return Chunky("done", {"input_tokens": 1, "output_tokens": 1})

    text, msg, first = A.stream_answer(Plain(), [])
    assert text == "done" and first >= 0 and msg.usage_metadata["input_tokens"] == 1


def test_openai_stream_accumulation():
    class Delta:
        def __init__(self, c):
            self.content = c

    class Choice:
        def __init__(self, c):
            self.delta = Delta(c)

    class Ch:
        def __init__(self, c=None, usage=None):
            self.choices = [Choice(c)] if c is not None else []
            self.usage = usage

    class Usage:
        prompt_tokens, completion_tokens = 500, 7

    got = []
    text, usage, first = D.stream_text([Ch("On "), Ch("hold"), Ch(" [1]."), Ch(None, Usage())], on_token=got.append)
    assert text == "On hold [1]." and usage.prompt_tokens == 500 and first > 0
    assert got == ["On ", "On hold", "On hold [1]."]


def test_first_token_in_eval_rows(tmp_path):
    from research_qa.evaluate import baseline
    from research_qa.evaluate import run as runner
    from research_qa.schema import Answer
    from research_qa.storage import LocalStore

    store = LocalStore(tmp_path / "s")
    key = runner.run(["markdown-plus"], ["single-pass"], ids=["MM04-01"], run_name="stream", store=store,
                     ask_fn=lambda q, a, m: Answer(arm=a, mode=m, question=q, text="x", total_s=3.0, first_token_s=1.2),
                     judge_fn=lambda q, t: {"verdict": "correct", "reason": "fake"}, progress=None)
    rows = store.jsonl(f"{key}/rows.jsonl")
    assert rows[0]["first_token_s"] == 1.2
    md = baseline.markdown("stream", baseline.summarise(baseline.recall_rows(rows, store, 10)), 10)
    assert "first token" in md and "/ 1.2" in md
