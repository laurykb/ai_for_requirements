# Backend FastAPI et moteurs RAG/LynX. MongoDB et Ollama restent des services
# séparés dans compose.yaml afin que chaque conteneur n'ait qu'une responsabilité.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Bibliothèques utilisées par Docling, EasyOCR et les traitements Office/PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl libgl1 libglib2.0-0 libgomp1 libreoffice-calc libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && pip install -r requirements.txt

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl --fail http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
