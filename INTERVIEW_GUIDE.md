# Knowledge Nexus: Interview Guide

## 30-second explanation

Knowledge Nexus is a local study app that extracts facts from text-based PDFs and keeps each fact linked to its source passage and page. Users can search, ask evidence-filtered questions, and create quizzes, notes, and flashcards from those facts.

## 60-second explanation

Students often have related facts scattered across monthly PDFs. Knowledge Nexus uploads PDFs, extracts page-level text with PyMuPDF, and stores documents, passages, facts, graph links, and study records in SQLite. FastAPI serves both the HTML interface (rendered with Jinja2) and JSON endpoints. Account sessions isolate each user’s database and uploaded files. The app provides a reader, document search, graph exploration, evidence-filtered chat, quizzes, revision notes, flashcards, comparisons, and progress tracking. Claude can optionally assist with fact extraction, while chat and study outputs are assembled from source-checked facts.

## 3-minute technical explanation

The project is a local multi-account web application. FastAPI handles page requests, forms, and JSON API endpoints. Jinja2 renders the pages without a JavaScript frontend. A session uses a random token in an HttpOnly cookie; only a hash of the token is stored. Passwords are stored as salted PBKDF2 hashes. Each account’s request is scoped to its own SQLite database and upload directory.

Uploaded PDFs are stored locally. PyMuPDF reads page text, and SQLite stores document metadata, extracted passages, facts, graph nodes/edges, quiz attempts, revision notes, flashcards, and topic progress.

Fact extraction has two paths. If an Anthropic key is configured, the backend asks Claude for structured candidate facts, then checks that the reported evidence exists in the source passage and that the subject/object are present in that evidence. Without a key, a small deterministic parser extracts a limited set of explicit statements. Unsupported generic “mention” facts are not accepted as validated facts.

For retrieval, Magic Click searches verified facts and can fall back to passage similarity. Chat filters relevant facts using query terms and relation intent, builds its answer from those facts, and returns source citations. Quiz answers and revision notes are built from the same evidence-checked rows; quiz distractors are only offered when enough alternative source-backed values exist, otherwise the question is a short-answer question. Progress is based on actual quiz attempts, flashcard reviews, and topic status changes.

The current implementation uses SQLite graph tables and Sentence Transformers for passage embeddings. It does **not** connect to Neo4j. The deterministic fact parser and entity matching are limited and should be described honestly.

## Architecture

```text
Browser (HTML forms + server-rendered Jinja2)
     | page requests and form submissions
     v
FastAPI routes and services
   |          |             |
   |          |             +-- optional Claude extraction
   |          +---------------- per-account SQLite + upload storage
   +--------------------------- PyMuPDF + local PDF files
     |
     v
Evidence-linked response (source PDF + page + passage)
```

## Technology choices

- **FastAPI:** page routes, form handling, and JSON endpoints in Python.
- **Jinja2 and HTML/CSS:** server-rendered user interface without a JavaScript frontend.
- **Pydantic:** validated API request payloads.
- **PyMuPDF:** page-by-page text extraction for text-based PDFs.
- **SQLite:** simple local storage, separated by account.
- **Claude API:** optional structured fact extraction; not a factual database.
- **SQLite graph tables:** the graph is implemented locally in this version. Neo4j is not integrated.
- **Sentence Transformers:** local passage embeddings; Neo4j vector search is not integrated.

## RAG vs GraphRAG

RAG finds relevant passages and gives them to an answer process. For example, a query about a named bank can retrieve the page where the bank appears.

GraphRAG also uses connected entities and topics. A topic can connect facts from different PDFs, allowing the user to discover related items. In this application, graph-like links are currently stored in SQLite. The system should not be described as a Neo4j implementation.

## PDF and evidence flow

1. The user uploads a PDF.
2. The backend stores it locally.
3. PyMuPDF extracts text page by page.
4. Passage records retain document ID, filename relationship, and page number.
5. Fact extraction returns candidate subject/relation/object/topic/evidence records.
6. Backend validation checks evidence against the original passage.
7. Valid facts are used by Magic Click, graph exploration, chat, and study tools.
8. Citation links open the reader at the source document and page.

Evidence checks reduce the chance of unsupported output, but they do not prove that hallucinations are impossible. The deterministic parser also has limited sentence-pattern coverage.

## 15 technical interview questions and answers

### 1. What is RAG?
Retrieval-Augmented Generation retrieves relevant source material and uses it as context for producing an answer. Retrieval helps ground the answer in a specific corpus.

### 2. What is GraphRAG?
GraphRAG adds relationships between entities, facts, and topics to passage retrieval. This can surface connections that are spread across different source files.

### 3. Why Neo4j instead of only SQL?
Graph databases are designed to represent and traverse relationships naturally. In this current implementation, I used SQLite graph tables to keep local setup simple; Neo4j is not integrated yet.

### 4. What are vector embeddings?
Embeddings represent text as numerical features so similarity can be estimated. This project currently uses a lightweight token-vector similarity baseline, not a neural embedding model.

### 5. What is semantic similarity?
It is a measure of how related two pieces of text are based on their representations. The current baseline is limited and mostly responds to shared terms.

### 6. How are relationships extracted?
Claude can optionally propose structured facts from a passage. The backend validates returned evidence and subject/object against the source. Without a configured key, a narrow rule parser handles explicit supported sentence patterns.

### 7. How are duplicate entities handled?
The local graph normalizes entity names to lowercase identifiers and merges exact normalized names. It does not solve all aliases or ambiguous entities.

### 8. How does Magic Click work?
The reader captures selected text and sends it to the API. The API searches source-validated facts for matching entity, product, or topic text, then returns related facts with PDF/page citations.

### 9. How does the system reduce hallucinations?
It verifies extracted evidence against stored page text, uses supported facts for study outputs, includes citations, and abstains when it cannot find matching evidence. This reduces risk but cannot eliminate hallucination.

### 10. What happens when PDFs contradict each other?
The source records remain separate so both can be cited. The current app does not have a complete contradiction classifier; a human should compare the passages before deciding whether one is an update.

### 11. How is PDF text extracted?
PyMuPDF opens the document and reads page text. Scanned/image-only documents can use local OCR when Tesseract is installed.

### 12. How does FastAPI connect to Neo4j?
It currently does not. This build uses SQLite for its graph representation. A future adapter could replace or supplement that service with Neo4j.

### 13. Why is the LLM not used as the database?
An LLM can interpret or reorganize text, but durable facts and citations should be stored in a database with stable document and page references.

### 14. What are the limitations?
No password reset or email verification, local file storage, a limited deterministic extraction fallback, heuristic entity matching, and SQLite rather than Neo4j.

### 15. How would you scale it?
First benchmark real data. Then add stronger entity resolution and a proper embedding model, integrate Neo4j if graph traversal needs it, move files to object storage, and add background processing only if upload latency becomes a measured problem.

## Resume bullets

Use only after personally checking the workflows:

- Built a Python/FastAPI and Jinja2 application for private PDF workspaces, page-level extraction, and source-linked retrieval.
- Implemented account-scoped SQLite data, session authentication, graph exploration, and evidence-filtered chat with citations.

Deployment note: the Render free-tier blueprint is demo-only because its filesystem is ephemeral; use persistent storage before handling important user data.
- Added source-derived quizzes, downloadable revision notes, flashcards, cross-document fact comparisons, and learning progress tracking.

Do not claim Neo4j, Sentence Transformers, OCR, LLM-generated quizzes, or performance improvements until those are actually implemented and tested.
