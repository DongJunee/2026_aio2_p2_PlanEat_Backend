from fastapi import FastAPI

from app.api.v1.endpoints.chat.router import router as chat_router

app = FastAPI(title="PlanEat API", version="0.1.0")
app.include_router(chat_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
