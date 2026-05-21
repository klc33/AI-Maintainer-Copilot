# model_server/main.py
"""Model inference server: classifier, NER, summarizer, RAG search, embeddings."""
import os
import torch
from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from model_server.ner import extract_entities
from model_server.summarizer import summarize_thread
from model_server.rag_retrieval import get_pool, retrieve, embed_model

app = FastAPI()

# ── Request bodies ─────────────────────────────────────
# FastAPI treats a bare `text: str` parameter as a query string. Callers
# (chatbot, memory service) POST JSON, so we declare explicit body models.
class TextIn(BaseModel):
    text: str

class TextsIn(BaseModel):
    texts: list[str]

# ── Load fine‑tuned classifier ────────────────────────
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/models/classifier/v1")
LABELS = ["bug", "feature", "docs", "question"]
ID2LABEL = {i: l for i, l in enumerate(LABELS)}
LABEL2ID = {l: i for i, l in enumerate(LABELS)}

classifier_loaded = False
tokenizer = None
model = None
groq_loaded = False

@app.on_event("startup")
async def load_classifier():
    global tokenizer, model, classifier_loaded
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
        model = AutoModelForSequenceClassification.from_pretrained(
            MODEL_PATH,
            num_labels=len(LABELS),
            id2label=ID2LABEL,
            label2id=LABEL2ID,
        )
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device)
        model.eval()
        classifier_loaded = True
        print(f"Classifier loaded on {device}")
    except Exception as e:
        print(f"Classifier not loaded: {e}")

@app.on_event("startup")
async def load_groq_key_from_vault():
    """Fetch GROQ_API_KEY from Vault and put it in os.environ for the lazy
    Groq client in summarizer.py. Soft-fail so the rest of the endpoints
    (classifier, NER, RAG) still serve if Vault is unreachable; /summarize
    will simply error at call time."""
    global groq_loaded
    import hvac
    addr = os.environ.get("VAULT_ADDR", "http://vault:8200")
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        print("VAULT_TOKEN not set; skipping Groq key load. /summarize will fail.")
        return
    try:
        client = hvac.Client(url=addr, token=token)
        resp = client.secrets.kv.v2.read_secret_version(path="shared/groq", mount_point="secret")
        secret = resp["data"]["data"].get("secret")
        if not secret:
            print("secret/shared/groq has no 'secret' field; /summarize will fail.")
            return
        os.environ["GROQ_API_KEY"] = secret
        groq_loaded = True
        print("GROQ_API_KEY loaded from Vault")
    except Exception as e:
        print(f"Could not load Groq key from Vault: {e}. /summarize will fail.")

# ── Health check ───────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "classifier_loaded": classifier_loaded,
        "groq_loaded": groq_loaded,
    }

# ── Classification endpoint ────────────────────────────
@app.post("/classify")
async def classify_text(body: TextIn):
    if not classifier_loaded:
        return {"error": "Classifier not loaded"}
    device = next(model.parameters()).device
    inputs = tokenizer(body.text, return_tensors="pt", truncation=True, max_length=256).to(device)
    with torch.no_grad():
        logits = model(**inputs).logits
        pred = torch.argmax(logits, dim=1).item()
    return {"label": ID2LABEL[pred]}

# ── NER endpoint ───────────────────────────────────────
@app.post("/extract")
async def extract(body: TextIn):
    entities = extract_entities(body.text)
    return {"entities": entities}

# ── Summarization endpoint ─────────────────────────────
@app.post("/summarize")
async def summarize(body: TextIn):
    summary = summarize_thread(body.text)
    return {"summary": summary}

# ── Embedding endpoint ─────────────────────────────────
# Used by app/services/memory.py for write_memory / recall_memories.
# Returns 768-dim BAAI/bge-base-en-v1.5 vectors matching the
# `memories.embedding vector(768)` column.
@app.post("/embed")
async def embed(body: TextsIn):
    if not body.texts:
        return {"embeddings": []}
    vectors = embed_model.encode(body.texts, normalize_embeddings=True).tolist()
    return {"embeddings": vectors}

# ── RAG search endpoint ────────────────────────────────
@app.get("/rag/search")
async def search_knowledge(query: str, content_type: str = None, top_k: int = 5):
    pool = await get_pool()
    async with pool.acquire() as conn:
        results = await retrieve(
            conn,
            query,
            top_k=top_k,
            content_types=[content_type] if content_type else None,
        )
        # Convert asyncpg records to dicts
        return {"results": [dict(r) for r in results]}