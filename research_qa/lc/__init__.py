"""The LangChain + Chroma retrieval engine, selected with RETRIEVAL_BACKEND=langchain.

Same two arms, same two modes, same schema.Answer as the Azure engine, so the app, the evaluation and the CLI do
not change. Runs on this machine: Chroma persists under settings.chroma_dir (derived data, rebuilt by `index`),
embeddings and the answer model are the Azure OpenAI deployments through LangChain. The Word arm here is the
stock MarkItDown conversion of the .docx with no plugin (word_source.py); Markdown+ is the same chunks, facts and
pictures as everywhere else.
"""
