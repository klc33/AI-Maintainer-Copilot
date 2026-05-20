# model_server/main.py
"""Model inference server: classifier, NER, summarizer, RAG search."""
import os
import torch
from fastapi import FastAPI
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from model_server.ner import extract_entities
from model_server.summarizer import summarize_thread
from model_server.rag_retrieval import create_pool, retrieve

app = FastAPI()

# ── Load fine‑tuned classifier ────────────────────────
MODEL_PATH = os.environ.get("MODEL_PATH", "/app/models/classifier/v1")
LABELS = ["bug", "feature", "docs", "question"]
ID2LABEL = {i: l for i, l in enumerate(LABELS)}
LABEL2ID = {l: i for i, l in enumerate(LABELS)}

classifier_loaded = False
tokenizer = None
model = None

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

# ── Health check ───────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "classifier_loaded": classifier_loaded,
    }

# ── Classification endpoint ────────────────────────────
@app.post("/classify")
async def classify_text(text: str):
    if not classifier_loaded:
        return {"error": "Classifier not loaded"}
    device = next(model.parameters()).device
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=256).to(device)
    with torch.no_grad():
        logits = model(**inputs).logits
        pred = torch.argmax(logits, dim=1).item()
    return {"label": ID2LABEL[pred]}

# ── NER endpoint ───────────────────────────────────────
@app.post("/extract")
async def extract(text: str):
    entities = extract_entities(text)
    return {"entities": entities}

# ── Summarization endpoint ─────────────────────────────
@app.post("/summarize")
async def summarize(text: str):
    summary = summarize_thread(text)
    return {"summary": summary}

# ── RAG search endpoint ────────────────────────────────
@app.get("/rag/search")
async def search_knowledge(query: str, content_type: str = None, top_k: int = 5):
    pool = await create_pool()
    async with pool.acquire() as conn:
        results = await retrieve(
            conn,
            query,
            top_k=top_k,
            content_types=[content_type] if content_type else None,
        )
        # Convert asyncpg records to dicts
        return {"results": [dict(r) for r in results]}