FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

# PA-116: the built source commit travels with the image and is echoed by
# /healthz and by worker startup, so "which code is running" is never guessed.
ARG PROPERTY_AI_SOURCE_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROPERTY_AI_SOURCE_SHA=${PROPERTY_AI_SOURCE_SHA}

LABEL property-ai.source-sha=${PROPERTY_AI_SOURCE_SHA}

WORKDIR /app

RUN python -c "import sys; assert sys.version_info[:3] == (3, 12, 10), sys.version"

RUN useradd --create-home --uid 10001 appuser

COPY deploy/requirements.delivery.lock /requirements.delivery.lock
COPY backend/ /app/
# Install the wheel, then drop the source tree: before PA-116 both copies
# existed and `sys.path[0]=''` (cwd=/app) made /app/app shadow site-packages,
# which is how a three-day-old image silently kept running pre-fix code.
RUN pip install --no-cache-dir --requirement /requirements.delivery.lock \
    && pip install --no-cache-dir --no-deps . \
    && rm -rf /app/app
# Fail the build if a second importable copy ever comes back.
RUN python -c "import pathlib, app.domain.observed_event as m; p = pathlib.Path(m.__file__).resolve(); print(p); assert '/app/app/' not in str(p), p; assert not pathlib.Path('/app/app').exists()"

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
