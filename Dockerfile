FROM python:3.12-slim

RUN pip install --no-cache-dir fastapi uvicorn pydantic

WORKDIR /app

COPY scripts/ scripts/
COPY schemas/ schemas/

EXPOSE 8080

CMD ["uvicorn", "scripts.server:app", "--host", "0.0.0.0", "--port", "8080"]
