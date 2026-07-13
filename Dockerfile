FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "run_pipeline.py", "--forever"]
