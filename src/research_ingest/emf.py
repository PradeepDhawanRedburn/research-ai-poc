"""Pull text records out of EMF (Enhanced Metafile) images.

Charts pasted from Excel as EMF keep their axis labels, legends and data labels as
EMR_EXTTEXTOUTW records. Reading them gives a lossless fallback description of the
picture without a vision model: the numbers on the axes, the series names, the dates.
"""
from __future__ import annotations

import struct

EMR_EXTTEXTOUTA = 83
EMR_EXTTEXTOUTW = 84
EMR_SMALLTEXTOUT = 108
EMR_HEADER = 1


def emf_text_records(data: bytes, max_records: int = 2000) -> list[str]:
    texts: list[str] = []
    if len(data) < 8 or struct.unpack_from("<I", data, 0)[0] != EMR_HEADER:
        return texts
    off = 0
    n = len(data)
    while off + 8 <= n and len(texts) < max_records:
        rtype, size = struct.unpack_from("<II", data, off)
        if size < 8 or off + size > n:
            break
        if rtype in (EMR_EXTTEXTOUTW, EMR_EXTTEXTOUTA):
            # EMR_EXTTEXTOUT*: Bounds(16) iGraphicsMode(4) exScale(4) eyScale(4) then EmrText:
            # Reference(8) Chars(4) offString(4) Options(4) Rectangle(16) offDx(4)
            try:
                base = off + 8 + 16 + 4 + 4 + 4
                nchars, off_string = struct.unpack_from("<II", data, base + 8)
                start = off + off_string
                if rtype == EMR_EXTTEXTOUTW:
                    raw = data[start:start + nchars * 2]
                    s = raw.decode("utf-16le", "ignore")
                else:
                    raw = data[start:start + nchars]
                    s = raw.decode("latin-1", "ignore")
                s = s.replace("\x00", "").strip()
                if s:
                    texts.append(s)
            except struct.error:
                pass
        elif rtype == EMR_SMALLTEXTOUT:
            try:
                # x(4) y(4) cChars(4) fuOptions(4) iGraphicsMode(4) exScale(4) eyScale(4) [Bounds(16)] TextString
                nchars, fu = struct.unpack_from("<II", data, off + 16)
                pos = off + 8 + 28
                if not (fu & 0x100):  # ETO_NO_RECT absent -> bounds present
                    pos += 16
                if fu & 0x200:  # ETO_SMALL_CHARS
                    s = data[pos:pos + nchars].decode("latin-1", "ignore")
                else:
                    s = data[pos:pos + nchars * 2].decode("utf-16le", "ignore")
                s = s.replace("\x00", "").strip()
                if s:
                    texts.append(s)
            except struct.error:
                pass
        off += size
    return texts


def summarise_emf_text(texts: list[str]) -> dict:
    """Split records into numeric-looking axis labels and words (legends, titles)."""
    numeric, words = [], []
    for t in texts:
        core = t.replace(",", "").replace("%", "").replace("$", "").replace("€", "").replace("£", "").strip()
        try:
            float(core.replace("(", "-").replace(")", ""))
            numeric.append(t)
        except ValueError:
            words.append(t)
    return {"labels": words, "axis_values": numeric, "n_records": len(texts)}
