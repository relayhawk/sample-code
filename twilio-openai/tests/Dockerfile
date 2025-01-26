FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY twilio_openai/requirements.txt .
COPY tests/requirements.txt requirements.tests.txt
RUN pip install --no-cache-dir -r requirements.txt -r requirements.tests.txt

# Copy application and test code
COPY twilio_openai/ /app/twilio_openai/
COPY tests/ /app/tests/

# Set Python path
ENV PYTHONPATH=/app
