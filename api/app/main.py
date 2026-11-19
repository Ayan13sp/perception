from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.db.database import init_db
from app.routes import auth_routes, core_routes
from app.services.errors import DomainError

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting up...")
    await init_db()
    yield
    print("Shutting down...")

app = FastAPI(
    title="Perception API",
    description="API for the AI-Powered Online Evaluation Portal.",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://perception-ui.onrender.com", "https://ai-evaluation-plat4m.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    # Same body shape as HTTPException, so the frontend's `detail` handling keeps working
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

app.include_router(auth_routes.router, prefix="/auth", tags=["Authentication"])
app.include_router(core_routes.router, prefix="/api", tags=["Core API"])


@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "Welcome to the Perception API!"}

