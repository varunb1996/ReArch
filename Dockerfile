# API image for a Hugging Face Docker Space (also runs anywhere Docker does).
#
#   docker build -t rearch-api .
#   docker run -p 7860:7860 -e GROQ_API_KEY=... rearch-api
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs the container as uid 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed \
    REARCH_PUBLIC_MODE=1 \
    REARCH_DATA_DIR=/home/user/data \
    REARCH_CORS_ORIGINS=https://varunb1996.github.io \
    REARCH_UI_URL=https://varunb1996.github.io/ReArch/
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fetch at build time what would otherwise download during the first visitor's
# analysis: the embedding model and the tree-sitter grammars.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" \
    && python -c "import tree_sitter_language_pack as t; t.download(['python', 'javascript'])"

COPY --chown=user api ./api
COPY --chown=user parser ./parser
COPY --chown=user demo ./demo

EXPOSE 7860
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
