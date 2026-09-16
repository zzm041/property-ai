FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN python -c "import sys; assert sys.version_info[:3] == (3, 12, 10), sys.version"

RUN useradd --create-home --uid 10001 appuser

COPY backend/ /app/
RUN pip install --upgrade pip && pip install .

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
