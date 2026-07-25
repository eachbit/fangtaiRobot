FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8000

WORKDIR /app

COPY app ./app
COPY public ./public
COPY data ./data
COPY server.py README.md ./

EXPOSE 8000

CMD ["python", "server.py"]
