"""Entry point for the 3D Printer Production Simulator API."""

from fastapi import FastAPI

app = FastAPI(
    title="3D Printer Production Simulator",
    description="Discrete event simulation of a 3D printer factory.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Return API health status."""
    return {"status": "ok"}
