FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

VOLUME ["/app/data"]

ENV BOOKBOX_DB=/app/data/bookbox.db

CMD ["python", "main.py"]
