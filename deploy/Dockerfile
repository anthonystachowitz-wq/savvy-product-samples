# Five-sample suite, container build.
# Build context is the repo root: docker build -f deploy/Dockerfile .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SAVVY_LLM_MODE=cached \
    SAVVY_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY README.md ./

RUN mkdir -p /app/data/state
EXPOSE 8000

# The model key is supplied at runtime. It is never baked into the image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
