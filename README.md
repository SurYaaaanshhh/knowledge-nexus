# Knowledge Nexus

Knowledge Nexus is a Python web application for studying facts extracted from PDFs. FastAPI serves the HTML pages and JSON API; Jinja2 renders the interface without a JavaScript frontend. Each account has an isolated SQLite database and PDF upload directory.

> The included sample PDFs are fictional practice content, not real current affairs.

## Features

- Sign up, sign in, sign out, and keep a private workspace per account.
- Upload PDFs and extract text page by page; scanned pages can use locally installed Tesseract OCR.
- Extract source-backed facts with optional Claude-assisted extraction or the deterministic fallback.
- Read original PDFs, find related facts, search passages, and follow document/page citations.
- Explore the SQLite-backed knowledge graph.
- Ask evidence-filtered questions; the app abstains when it cannot find supported facts.
- Generate and submit quizzes, create downloadable revision notes, and review flashcards.
- Compare facts across documents and track quiz, flashcard, and topic progress.

## Technology

- **Application and pages:** Python, FastAPI, Jinja2 templates
- **Data:** SQLite and local PDF files
- **PDF processing:** PyMuPDF; optional Tesseract OCR
- **Passage retrieval:** Sentence Transformers (`all-MiniLM-L6-v2`)
- **Optional fact extraction:** Anthropic Claude

The knowledge graph is stored in SQLite; Neo4j is not connected. Claude is optional and is only used for fact extraction when configured. Chat and study materials are assembled from stored, source-checked facts. Evidence checks reduce unsupported results but cannot guarantee that hallucinations are impossible. The first semantic-search request may download its model if it is not cached.

## Requirements

- Python 3.10 or newer
- macOS for the one-command launcher
- Optional: Tesseract on `PATH` for scanned-PDF OCR
- Optional: a GitHub account and Render account for cloud deployment

## Setup

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

Claude extraction is optional. To enable it, add a valid key to `backend/.env`:

```env
ANTHROPIC_API_KEY=your_key_here
```

Do not commit `.env` or paste API keys into source files. The first account created on a machine inherits any existing local Knowledge Nexus database; later accounts start with their own empty workspace.

## Run

### macOS

```bash
./start.command
```

The launcher starts FastAPI, waits for its health check, and opens [http://localhost:8000/login](http://localhost:8000/login). It can also be double-clicked in Finder. Keep its Terminal window open while using the app; press **Ctrl+C** there to stop the server. Running it again while the API is healthy simply opens the login page.

### Manual startup

```bash
cd backend
PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [http://localhost:8000/login](http://localhost:8000/login) and create an account. The API health endpoint is [http://localhost:8000/health](http://localhost:8000/health).

For an HTTPS deployment, set `AUTH_COOKIE_SECURE=true` in the backend environment. The local launcher uses HTTP and leaves the secure-cookie flag disabled. This project is intended for local use; use HTTPS and a hardened deployment configuration before exposing it to a network.

## Deploy to Render

The included [`render.yaml`](./render.yaml) defines a free Render web service. Push this repository to GitHub, sign in to Render, choose **New → Blueprint**, connect the repository, and apply the blueprint. The service builds from `backend/`, serves the FastAPI/Jinja pages over HTTPS, and checks `/health`. `AUTH_COOKIE_SECURE=true` is enabled for the hosted service.

**Free-tier storage is temporary.** Render's free web service does not provide a persistent disk: account records, SQLite data, and uploaded PDFs can disappear when the service restarts, redeploys, or sleeps. This deployment is suitable for a demo only. Use a paid Render plan with an attached persistent disk (or migrate auth/data/uploads to managed persistent services) before relying on real user data. Do not put private or important PDFs in the free deployment.

## Tests

```bash
cd backend
PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_app.py -q
```

## Repository layout

```text
backend/
  app/
    routes/        FastAPI JSON API
    services/      PDF, retrieval, graph, and study logic
    templates/     Server-rendered HTML pages
    static/        Stylesheet
  sample_pdfs/     Fictional practice PDFs
  tests/           Backend and authentication tests
start.command      macOS development launcher
```

The auth database, per-account databases, uploaded PDFs, Python environment, and test caches are local runtime data and excluded by `.gitignore`.

## Security and limitations

- Passwords use salted PBKDF2 hashes; sessions use random revocable tokens stored as hashes in HttpOnly cookies.
- Sign-in is temporarily locked after repeated failed password attempts. Email verification and password reset are not implemented.
- Data is isolated per account on one local machine. This is not a hardened public multi-tenant deployment.
- Scanned-PDF OCR requires Tesseract to be installed separately.
- Deterministic fact extraction and entity matching support limited patterns.
- The graph is SQLite-backed; Neo4j is not integrated.

## License

No license has been selected yet. Choose a license before inviting reuse or contributions.
