# Model API for Hugging Face Spaces (Docker SDK) / Render / Railway / Fly.io. CPU only.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEVICE=cpu
RUN useradd -m -u 1000 app
WORKDIR /home/app
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install numpy soundfile fastapi uvicorn python-multipart httpx
COPY --chown=app:app server ./server
COPY --chown=app:app model ./model
COPY --chown=app:app samples ./samples
USER app
EXPOSE 7860
# FORENSIC_DB lives in /tmp (ephemeral): set SUPABASE_URL + SUPABASE_SERVICE_KEY for durable storage.
ENV FORENSIC_DB=/tmp/analyses.db
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
