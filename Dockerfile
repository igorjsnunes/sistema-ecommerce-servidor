FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --upgrade pip && pip install -r requirements.txt
EXPOSE 5000
ENV PORT=5000
CMD exec gunicorn --bind 0.0.0.0:$PORT app:app --workers 2
