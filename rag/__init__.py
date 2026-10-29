"""Multimodal RAG over the research reports: Azure AI Foundry models, LangChain, local Chroma.

Two solutions share chunking, storage, retrieval and answering, and differ only in their input:
  docx      the original Word files (data/raw), read directly            -> rag/loaders/docx_source.py
  markdown  the MarkItDown + research plugin output (out/A+)             -> rag/loaders/markdown_source.py
"""
