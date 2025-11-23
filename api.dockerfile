# Start with Node.js to build frontend
FROM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Python application
FROM python:3.13-alpine

WORKDIR /app
COPY requirements.txt .
# instala toolchain para build como .build-deps, instala libgcc/libstdc++ como runtime permanente
RUN apk add --no-cache --virtual .build-deps build-base \
    && apk add --no-cache libgcc libstdc++ \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

# Copy project files
COPY ./api ./api
COPY ./core ./core
COPY ./cli.py ./cli.py
COPY ./main.py ./main.py
COPY ./system_prompt.txt ./system_prompt.txt
COPY ./translations_normalized.json ./translations_normalized.json
COPY ./data ./data
COPY ./integrations ./integrations
COPY ./configs ./configs

# Copy built frontend from previous stage
COPY --from=frontend-builder /frontend/public ./frontend/public

# Expose FastAPI port
EXPOSE 8001

# Start FastAPI server
CMD ["python", "main.py"]
