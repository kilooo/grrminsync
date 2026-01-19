FROM python:3.11-slim

WORKDIR /app

# Set timezone (optional but good practice)
ENV TZ=America/New_York
# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1

# Copy requirements first to leverage cache
COPY requirements.txt .

# Install system dependencies (tzdata for timezone support)
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the Flask port
EXPOSE 5000

# Run the server
CMD ["python", "server.py"]
