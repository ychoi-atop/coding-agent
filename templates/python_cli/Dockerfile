FROM python:3.11-slim

WORKDIR /app

RUN adduser --disabled-password --gecos "" --home /app appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY contracts ./contracts
COPY scripts ./scripts
COPY .semgrep.yml ./.semgrep.yml

RUN chown -R appuser:appuser /app

ENV PYTHONPATH=/app/src

USER appuser
CMD ["python", "-m", "app.cli", "--hello", "docker"]
