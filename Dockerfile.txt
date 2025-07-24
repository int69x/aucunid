FROM python:3.11-slim
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Procfile utilisation via gunicorn-worker? Ici on lance directement
CMD ["python", "discord_bot.py"]
