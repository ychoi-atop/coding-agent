FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN adduser --disabled-password --gecos "" --home /app appuser

COPY requirements.txt requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY contracts ./contracts
COPY scripts ./scripts
COPY .semgrep.yml ./.semgrep.yml

RUN chown -R appuser:appuser /app

ENV PYTHONPATH=/app/src

EXPOSE 8000

USER appuser
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
