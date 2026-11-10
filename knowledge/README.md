# Client knowledge base

Each folder here is one client's collection of documents (Markdown or plain text), searched by the
Product Manager's `search_knowledge` tool. A client's documents are never searched together with another
client's: the tool takes a collection name and only looks in that folder.

`acme-bakery/` is a **fictional** client used for tests and demos. Every name, number, address and
email in it is invented. `supplier-notes.md` deliberately contains a line that tries to give the AI
orders (an "indirect prompt injection", the classic attack through a knowledge base), so you can
watch the guardrail withhold it.

Add your own: create `knowledge/<client-name>/` and put `.md` or `.txt` files in it (200 KB each, 200
files per collection). The index rebuilds when a file changes. PDFs are not read here; Vertex AI RAG
Engine handles them on Google Cloud.

Evaluate retrieval with `python -m evals.rag_eval` (recall, ranking, and refusing unanswerable questions).
