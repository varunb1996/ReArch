# The ReArch API as a small container: public mode, no embedding model, sized for a 512 MB host
# (Render's free web service is 512 MB RAM and 0.1 CPU).
#
#   docker build -t rearch-api .
#   docker run -p 10000:10000 -e GROQ_API_KEY=... -e REARCH_CORS_ORIGINS=https://your-ui rearch-api
#
# Built and smoke-tested in CI (.github/workflows/ci.yml) under `--memory=512m --cpus=0.1`.
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    REARCH_PUBLIC_MODE=1 \
    # The UI serves the bundled examples as static files, so the server skips seeding them (saves startup time).
    REARCH_SEED_DEMO=0 \
    # Cluster by dependency structure: the embedding model alone needs ~1.2 GB of RAM.
    REARCH_CLUSTERING=graph \
    # Measured on real repos: ~12 MB of RAM per 1,000 edges on top of a ~55 MB base; 500 files stays well under 512 MB.
    REARCH_MAX_CODE_FILES=500 \
    # Prose is written after the analysis is usable, inside this budget (Groq's free tier is ~8K tokens/minute,
    # shared by every visitor). Past it, what is already written is kept and the rest is skipped.
    REARCH_NARRATION_BUDGET_SECONDS=90 \
    REARCH_KUZU_BUFFER_MB=64 \
    REARCH_MAX_OPEN_GRAPHS=2 \
    REARCH_DATA_DIR=/home/user/data
WORKDIR /home/user/app

# requirements-hosted.txt is the subset the API and graph clustering import: no fastembed, onnxruntime, numpy or scikit-learn.
COPY --chown=user requirements-hosted.txt .
RUN pip install --no-cache-dir -r requirements-hosted.txt

# Fetch at build time what would otherwise download during the first visitor's analysis: the tree-sitter grammars.
RUN python -c "import tree_sitter_language_pack as t; t.download(['python', 'javascript', 'java', 'csharp', 'php'])"

COPY --chown=user api ./api
COPY --chown=user parser ./parser
COPY --chown=user demo ./demo

# Render injects PORT; 10000 is its default.
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
