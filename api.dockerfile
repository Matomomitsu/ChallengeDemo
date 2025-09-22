# Start with Node.js to build frontend
FROM node:20.11-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Python application
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY ./api ./api
COPY ./core ./core
COPY ./cli.py ./cli.py
COPY ./main.py ./main.py
COPY ./system_prompt.txt ./system_prompt.txt
COPY ./translations_normalized.json ./translations_normalized.json
COPY ./data ./data

# Copy built frontend from previous stage
COPY --from=frontend-builder /frontend/public ./frontend/public

# Expose FastAPI port
EXPOSE 8001

# Start FastAPI server
CMD ["python", "main.py"]
