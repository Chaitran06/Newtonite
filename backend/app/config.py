import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/opsdesk",
)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-please-use-a-long-random-value")
JWT_TTL_MINUTES = int(os.getenv("JWT_TTL_MINUTES", str(12 * 60)))
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]
