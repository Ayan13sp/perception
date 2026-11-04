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
```

---

## 🔒 Security & Prompt Injection Defense

The evaluation pipeline (`app/services/ai_service.py`) protects the LLM against adversarial student inputs:

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
- **AI Engine:** [Groq Cloud API](https://groq.com/) (`AsyncGroq`)
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

