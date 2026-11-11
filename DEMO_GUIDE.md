# Knowledge Nexus Demo Guide

## Before the demo

Complete the setup in [README.md](./README.md), then start the app with `./start.command` on macOS. Create an account, sign in, and use the fictional PDFs in `backend/sample_pdfs/` or upload other text-based practice PDFs.

## 3–5 minute walkthrough

### 1. Introduce the problem

“Study facts are spread across many PDFs. Knowledge Nexus keeps extracted facts linked to their source and page, then helps me find related evidence.”

### 2. Upload and extract

Open **Library**, upload a practice PDF, and point out its processing status. Explain that PyMuPDF extracts text page by page and SQLite stores document, passage, and fact records.

### 3. Reader and Magic Click

Open **Reader**, choose a PDF, and preview its pages. Search a phrase with the related-facts form to find matching evidence and source-page links.

### 4. Explore the graph

Open **Graph**, select a node, and inspect its facts and citations. The graph is backed by SQLite tables; it is not connected to a Neo4j server.

### 5. Grounded chat

Ask one question answered in the uploaded PDFs, then ask about a fact that is absent. Show the citation for supported information and the abstention response when evidence is unavailable.

### 6. Study workflows

- Generate and submit a quiz for an available topic.
- Create quick or detailed revision notes and download them.
- Review a flashcard and update its status.
- Compare related facts in **Exam Traps** and review **Progress**.

## Talking points

- Study results are built from stored facts and linked to source documents/pages.
- If the app cannot find supporting facts, chat should abstain.
- Evidence checks reduce unsupported results but cannot guarantee that hallucinations are impossible.
- Claude can optionally assist with fact extraction; chat and study material are assembled deterministically from stored facts.
- Sentence Transformers is used for passage embeddings. The knowledge graph itself is SQLite-backed, not Neo4j.
- Included sample PDFs are fictional practice content, not real news.

## Demo checklist

- [ ] The backend health endpoint responds.
- [ ] Server-rendered pages and navigation load.
- [ ] A text-based practice PDF is processed.
- [ ] Reader and Magic Click show related facts.
- [ ] A citation opens the correct document/page.
- [ ] Chat cites supported information and abstains when evidence is absent.
- [ ] Quiz, revision, flashcards, comparison, and progress workflows load.
