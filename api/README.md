# ⚡ Perception API Backend

High-performance asynchronous backend service for **Perception**, built with **FastAPI**, **MongoDB (Beanie ODM)**, and **Groq LLM** inference.

---

## 🏛️ Architecture Overview

The backend is structured into modular layers adhering to clean separation of concerns:

```
api/app/
├── core/               # App configuration, settings & environment management
├── db/                 # Database initialization and Beanie document models
├── models/             # Pydantic schemas for request validation & response serialization
├── routes/             # RESTful API route controllers (Auth, Core evaluation)
└── services/           # Business logic: AI evaluation, prompt defense & auth helpers
    └── grading/        # LangGraph multi-agent grader: state, nodes, tools, graph
```

---

## 🧠 Agentic Grading Pipeline (LangGraph)

`POST /api/evaluate` runs a **stateful multi-agent graph** (`app/services/grading/`) instead of a single LLM call. A typed `GradingState` flows through each node, and the graph routes conditionally, so a second opinion is only paid for when the first grade is uncertain.

```mermaid
graph TD;
    START([start]) --> gatekeeper
    gatekeeper -. injection confirmed .-> finalize
    gatekeeper -.-> decompose_rubric
    decompose_rubric -. math or lecture notes present .-> fact_check
    decompose_rubric -.-> grade
    fact_check --> grade
    grade -. "confidence ≥ 0.85, not borderline" .-> finalize
    grade -. "low confidence / borderline / suspected injection" .-> critic
    critic --> finalize
    finalize --> END([save to MongoDB])
```

| Node | Model | Role |
|---|---|---|
| **Gatekeeper** | regex + `llama-3.1-8b-instant` | Regex pre-filter; on a hit, a fast classifier confirms the injection. Confirmed attacks are blocked **before any grading tokens are spent**. Regex false positives (e.g. *"enzymes act as a catalyst"*) continue with a mandatory audit. |
| **Rubric Decomposer** | `gpt-oss-120b` | Splits the model answer into 2-6 atomic, weighted criteria (normalized to 10 pts). **Cached on the QuestionSet** and invalidated by a hash of the model answer. |
| **Fact-Checker** (tool calling) | `gpt-oss-120b` | Function-calling agent with two Python tools: `verify_calculation` (sympy) and `search_reference_material` (MongoDB `$text` search over uploaded lecture notes). Skipped when the answer has no math and no notes exist. |
| **Primary Grader** | `gpt-oss-120b` | Scores each criterion independently: Pydantic-validated JSON with `awarded`, `justification`, `confidence`. |
| **Critic / Auditor** | `gpt-oss-120b` | Adversarial review: too harsh? missed partial credit? too lenient? manipulated? Approves or adjusts, and flags large swings for teacher review. |
| **Finalize** | — | Clamps the score, runs the model-answer leak check, and assembles the audit trail. |

**Every submission stores a `grading_trace`** (node path, per-node latency, tokens, per-criterion scores, tool calls, critic verdict, `needs_review`). Teachers see it in `GET /api/teacher/question-sets/{id}/submissions`; students never do.

**Graceful degradation:** a failure in the decomposer, fact-checker or critic doesn't fail the submission. It falls back to a holistic rubric, grades without tool evidence, or keeps the draft score, and flags `needs_review`.

**Tool sandboxing:** tool arguments come from an LLM that has read untrusted student text, so `verify_calculation` uses an identifier allowlist, rejects attribute access and dunder names, parses in an inert pass to block exponent and factorial bombs, and has a 3 s timeout. `search_reference_material` receives its `question_set_id` from the server, never from the model.

### Reference material (for the fact-checker)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/teacher/question-sets/{id}/references` | Upload `.pdf` / `.txt` / `.md` (≤ 5 MB), chunked and indexed |
| `GET` | `/api/teacher/question-sets/{id}/references` | List uploaded sources |
| `DELETE` | `/api/teacher/question-sets/{id}/references/{source_name}` | Remove a source |

### Configuration

| Variable | Default | |
|---|---|---|
| `GRADING_PIPELINE` | `graph` | `legacy` = original single-call grader (handy for A/B comparison) |
| `GRADER_MODEL` / `CRITIC_MODEL` | `openai/gpt-oss-120b` | |
| `GATEKEEPER_MODEL` | `llama-3.1-8b-instant` | |
| `CONFIDENCE_THRESHOLD` | `0.85` | below this, the critic runs |
| `PASS_MARK` | `5.0` | scores within ±0.5 are borderline and audited |
| `MAX_TOOL_ROUNDS` | `3` | fact-checker tool-calling iterations |

### Tests

```bash
pip install -r requirements-dev.txt
pytest
```
The graph tests use a scripted fake LLM, so routing, short-circuiting, clamping and leak sanitization are verified deterministically with no API key.

---

## 🔒 Security & Prompt Injection Defense

The legacy single-call grader (`app/services/ai_service.py`) protects the LLM against adversarial student inputs. These layers are reused inside the graph:

1. **Regex Heuristic Pre-filter**: Instantly identifies common jailbreak phrases (`"ignore previous instructions"`, `"admin override"`) before invoking the LLM, saving inference tokens.
2. **XML Isolation Tags**: Student input is encapsulated within strict `<student_answer>` delimiters and framed as passive data, preventing instruction hijack.
3. **Instruction Sandwiching**: Core grading rules are repeated after user content to preserve prompt adherence.
4. **Leakage Detection & Masking**: Evaluates the output to verify that teacher model answers are not leaked verbatim back to students.
5. **Strict JSON Schema Parsing**: Enforces `{ "score": int, "feedback": str }` output and clamps scores strictly to `[0, 10]`.

---

## 🛠️ Tech Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.10+)
- **Server:** [Uvicorn](https://www.uvicorn.org/)
- **Database:** MongoDB via [Motor](https://motor.readthedocs.io/) & [Beanie ODM](https://beanie-odm.dev/)
- **AI Engine:** [Groq Cloud API](https://groq.com/) (`AsyncGroq`), orchestrated with [LangGraph](https://langchain-ai.github.io/langgraph/)
- **Tools:** [SymPy](https://www.sympy.org/) (math verification), [pypdf](https://pypdf.readthedocs.io/) (lecture-note ingestion)
- **Authentication:** Salted PBKDF2-SHA256 password hashing & custom header session tokens

---

## 🚀 Local Development Setup

1. **Navigate to the API folder:**
   ```bash
   cd api
   ```

2. **Create & activate virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env
   # Populate DATABASE_URL and GROQ_API_KEY
   ```

5. **Start development server:**
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

6. **Interactive Documentation:**
   - Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
   - ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

