"""Single-pass mode sends the original pictures behind the top figure passages when SINGLE_PASS_IMAGES is on."""
import io
from dataclasses import replace

import pytest

from research_qa.agent import tools
from research_qa.config import settings
from research_qa.lc import answer as A
from research_qa.schema import Chunk, Passage
from research_qa.storage import LocalStore


def png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "store")
    for k in ("assets/D1/aaa.png", "assets/D1/bbb.png", "assets/D1/ccc.png", "assets/D1/ddd.png"):
        s.put(k, png(), "image/png")
    return s


def passage(rank, assets, kind="figure", heading="Fig 1"):
    return Passage(rank=rank, id=f"D1-{rank:05d}", doc_id="D1", source_file="D1.md", kind=kind, heading=heading,
                   text=f"Report D1 {heading}", assets=assets)


def test_picture_blocks_rank_order_cap_and_missing(store):
    ps = [passage(3, ["assets/D1/ccc.png"]), passage(1, ["assets/D1/aaa.png", "assets/D1/missing.png"]),
          passage(2, ["assets/D1/bbb.png", "assets/D1/aaa.png"]), passage(4, ["assets/D1/ddd.png"])]
    blocks, shown = tools.picture_blocks(store, ps, max_n=3)
    assert [k for _, k in shown] == ["assets/D1/aaa.png", "assets/D1/bbb.png", "assets/D1/ccc.png"]
    assert [p.rank for p, _ in shown] == [1, 2, 3]
    assert len(blocks) == 6 and blocks[0]["type"] == "text" and "[1]" in blocks[0]["text"]
    assert blocks[1]["type"] == "image_url" and blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert tools.picture_blocks(store, ps, max_n=0) == ([], [])


class Captured:
    def __init__(self):
        self.messages = None

    def invoke(self, messages):
        self.messages = messages

        class Msg:
            content = "Three cans [1]."
            usage_metadata = {"input_tokens": 900, "output_tokens": 20}

        return Msg()


@pytest.fixture
def direct(monkeypatch, store):
    cap = Captured()
    import research_qa.lc.models as models

    monkeypatch.setattr(models, "chat_model", lambda: cap)
    monkeypatch.setattr(models, "usage_of", lambda m: (900, 20, 0))
    monkeypatch.setattr(A, "open_store", lambda: store)
    chunks = [Chunk(id="D1-00001", doc_id="D1", chunk=1, kind="figure", text="Report D1 Fig 22 three cans",
                    caption="Fig 22", assets=["assets/D1/aaa.png", "assets/D1/bbb.png"], source_file="D1.md"),
              Chunk(id="D1-00002", doc_id="D1", chunk=2, kind="text", text="Report D1 prose", source_file="D1.md")]
    monkeypatch.setattr(A, "_retrieve", lambda arm, q, k, d=None, info=None, **kw: chunks)
    return cap


def test_flag_off_sends_text_only(monkeypatch, direct):
    monkeypatch.setattr(A, "settings", replace(settings, single_pass_images=False))
    a = A.ask_single_pass("Which cans?", "markdown-plus")
    human = direct.messages[1]
    assert isinstance(human.content, str) and "Passages:" in human.content
    assert not [s for s in a.steps if s.kind == "look"] and not a.inspected_assets
    assert direct.messages[0].content == A.ANSWER_SYSTEM


def test_flag_on_attaches_pictures_in_rank_order(monkeypatch, direct):
    monkeypatch.setattr(A, "settings", replace(settings, single_pass_images=True, single_pass_images_max=1))
    a = A.ask_single_pass("Which cans?", "markdown-plus")
    human = direct.messages[1]
    assert isinstance(human.content, list) and human.content[0]["type"] == "text"
    images = [b for b in human.content if b["type"] == "image_url"]
    assert len(images) == 1, "capped by SINGLE_PASS_IMAGES_MAX"
    assert tools.PICTURES_NOTE in direct.messages[0].content
    look = next(s for s in a.steps if s.kind == "look")
    assert look.assets == ["assets/D1/aaa.png"]   # exactly what was sent; inspected_assets lists the passage's pictures
    assert a.passages[0].inspected and not a.passages[1].inspected
    assert a.input_tokens == 900 and a.text == "Three cans [1]."


def test_word_arm_never_gets_pictures(monkeypatch, direct):
    monkeypatch.setattr(A, "settings", replace(settings, single_pass_images=True))
    a = A.ask_single_pass("Which cans?", "word")
    assert isinstance(direct.messages[1].content, str) and not a.inspected_assets


def test_flag_recorded_in_retrieval_flags():
    assert replace(settings, single_pass_images=True, single_pass_images_max=2).retrieval_flags()["single_pass_images"] == 2
    assert replace(settings, single_pass_images=False).retrieval_flags()["single_pass_images"] is False
