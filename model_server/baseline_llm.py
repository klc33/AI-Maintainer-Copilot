# model_server/baseline_llm.py
"""LLM baseline: batched classification via Groq Llama 3.1 8B Instant.

- Uses plain JSON responses (no tool calling) for reliability.
- Processes issues in batches of BATCH_SIZE to stay under token limits.
- Automatically retries after rate‑limit delays (doubled wait).
- Checkpoints after every batch → resume safely after interruption.
"""

import os
import re
import json
import time
import collections
import pandas as pd
from groq import Groq, RateLimitError
from sklearn.metrics import accuracy_score, f1_score

# ── Config ────────────────────────────────────────────
PRICE_INPUT_PER_M = 0.05    # USD per 1M input tokens  (llama-3.1-8b-instant)
PRICE_OUTPUT_PER_M = 0.08   # USD per 1M output tokens
BATCH_SIZE = 10
MODEL_NAME = "llama-3.1-8b-instant"

client = Groq(api_key=os.environ["GROQ_API_KEY"])

# ── Rate‑limit throttle ───────────────────────────────
_token_window: collections.deque = collections.deque()
_req_window: collections.deque = collections.deque()

def _throttle(estimated_tokens: int) -> None:
    now = time.monotonic()
    window = 60.0
    while _token_window and now - _token_window[0][0] >= window:
        _token_window.popleft()
    while _req_window and now - _req_window[0][0] >= window:
        _req_window.popleft()
    tokens_used = sum(t for _, t in _token_window)
    reqs_used = len(_req_window)
    wait = 0.0
    if tokens_used + estimated_tokens > 20_000 and _token_window:
        wait = max(wait, window - (now - _token_window[0][0]))
    if reqs_used >= 30 and _req_window:
        wait = max(wait, window - (now - _req_window[0][0]))
    if wait > 0:
        print(f"\n[throttling {wait:.1f}s]", flush=True)
        time.sleep(wait + 0.5)
    now = time.monotonic()
    _token_window.append((now, estimated_tokens))
    _req_window.append((now, 1))

def parse_retry_seconds(error_message: str) -> float:
    """Extract wait time from Groq rate‑limit error (e.g. 'try again in 45.2s')."""
    match = re.search(r"try again in (\d+\.?\d*)s", error_message)
    return float(match.group(1)) + 5.0 if match else 60.0

# ── Text cleaning ─────────────────────────────────────
def clean_body(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`\n]{1,80}`", "", text)
    text = re.sub(r"<[^>]{1,100}>", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\s*(at |#\d+|\w+\.\w+\()", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ── Few‑shot examples ─────────────────────────────────
def load_few_shot_examples(train_df, n_per_class=1):
    examples = []
    for label in ["bug", "feature", "docs", "question"]:
        subset = train_df[train_df["label"] == label]
        if len(subset) >= n_per_class:
            sampled = subset.sample(n=n_per_class, random_state=42)
            examples.extend(sampled.to_dict("records"))
    return examples

# ── Prompt builder ────────────────────────────────────
def build_batch_prompt(issues: list, few_shot_examples: list) -> list:
    n = len(issues)
    system = {
        "role": "system",
        "content": (
            "You classify GitHub issues. The only valid labels are: bug, feature, docs, question.\n"
            f"You receive {n} issues numbered 1–{n}.\n"
            "Reply with a valid JSON object containing exactly one key `labels` whose value is an "
            f"array of {n} strings, one label per issue in the same order. Do not add any other text."
        ),
    }
    example_block = "Examples:\n" + "\n".join(
        f"Title: {ex['title']}\nBody: {clean_body(ex['body'])[:120]}\nLabel: {ex['label']}"
        for ex in few_shot_examples
    )
    issues_block = "\n\n".join(
        f"Issue {i+1}:\nTitle: {issues[i]['title']}\nBody: {clean_body(issues[i]['body'])[:400]}"
        for i in range(n)
    )
    return [system, {"role": "user", "content": example_block + "\n\nNow classify these:\n\n" + issues_block}]

# ── Core batch call with retry ────────────────────────
def classify_batch(issues: list, few_shot_examples: list, max_retries=3):
    """Return (labels, latency, usage) with automatic retry on rate limit (doubled wait)."""
    messages = build_batch_prompt(issues, few_shot_examples)
    estimated_tokens = sum(len(str(m.get("content") or "")) for m in messages) // 4 + 50

    for attempt in range(max_retries):
        _throttle(estimated_tokens)
        start = time.time()
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=0,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            break
        except RateLimitError as e:
            err_msg = str(e)
            wait = parse_retry_seconds(err_msg) * 2   # doubled for safety
            print(f"\n[rate limit: waiting {wait:.1f}s]", flush=True)
            time.sleep(wait)
            if attempt == max_retries - 1:
                raise
    else:
        raise RuntimeError("Max retries exceeded without success")

    latency = time.time() - start
    usage = response.usage
    print(f"[tokens: in={usage.prompt_tokens} out={usage.completion_tokens}]", end=" ")

    # Parse JSON response
    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
        labels = data["labels"]
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fallback: try to extract an array from the output
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                labels = json.loads(match.group())
            except Exception:
                labels = [None] * len(issues)
        else:
            labels = [None] * len(issues)

    # Pad/trim to match batch size
    labels = (labels + [None] * len(issues))[:len(issues)]
    return labels, latency, usage

# ── Main loop ─────────────────────────────────────────
def main():
    train = pd.read_csv("datasets/train.csv")
    test = pd.read_csv("datasets/test.csv")

    progress_file = "datasets/llm_progress.json"
    if os.path.exists(progress_file):
        with open(progress_file, "r") as f:
            progress = json.load(f)
    else:
        progress = {
            "predicted": {},
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_latency": 0.0,
            "count": 0,
        }

    few_shot = load_few_shot_examples(train, n_per_class=1)
    predictions_map = progress["predicted"]
    total_input = progress["total_input_tokens"]
    total_output = progress["total_output_tokens"]
    total_lat = progress["total_latency"]
    count = progress["count"]

    # Keep running until all issues are classified
    while True:
        pending = [(idx, row) for idx, row in test.iterrows() if str(idx) not in predictions_map]
        if not pending:
            break

        print(f"\nAlready done: {count}, remaining: {len(pending)}")
        batch = pending[:BATCH_SIZE]
        indices = [idx for idx, _ in batch]
        issues = [row for _, row in batch]
        true_labels = [row["label"] for _, row in batch]

        print(f"Batch issues {indices[0]}–{indices[-1]} ...", end=" ", flush=True)
        labels, lat, usage = classify_batch(issues, few_shot)

        for idx, true, pred in zip(indices, true_labels, labels):
            predictions_map[str(idx)] = {"label": pred, "true": true}
            count += 1

        total_input += usage.prompt_tokens
        total_output += usage.completion_tokens
        total_lat += lat

        # Save checkpoint after every batch
        progress.update({
            "predicted": predictions_map,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_latency": total_lat,
            "count": count,
        })
        with open(progress_file, "w") as f:
            json.dump(progress, f, indent=2)

        print(f"done  lat={lat:.2f}s  ({count} total)")

    # ── Final metrics (skip None predictions) ──────────
    print("\n=== LLM Baseline Results ===")
    y_true, y_pred = [], []
    for _, row in test.iterrows():
        entry = predictions_map.get(str(row.name))
        if entry and entry["label"] is not None:
            y_true.append(entry["true"])
            y_pred.append(entry["label"])
        elif entry and entry["label"] is None:
            print(f"Warning: None prediction for index {row.name} (true={entry['true']})")
        else:
            print(f"Missing prediction for index {row.name}")

    if not y_true:
        print("No valid predictions to evaluate.")
        return

    acc = accuracy_score(y_true, y_pred)
    macro_f1 = f1_score(y_true, y_pred, average="macro")
    per_class_f1 = f1_score(y_true, y_pred, average=None, labels=["bug", "feature", "docs", "question"])

    print(f"Accuracy: {acc:.4f}")
    print(f"Macro F1: {macro_f1:.4f}")
    for cls, f1 in zip(["bug", "feature", "docs", "question"], per_class_f1):
        print(f"  {cls}: {f1:.4f}")

    cost = (total_input / 1e6) * PRICE_INPUT_PER_M + (total_output / 1e6) * PRICE_OUTPUT_PER_M
    avg_lat = total_lat / count if count else 0
    print(f"Tokens: {total_input} in / {total_output} out | Cost: ${cost:.4f}")
    print(f"Avg latency: {avg_lat:.2f}s")
    print(f"Evaluated on {len(y_true)} valid predictions (out of {count} total)")

    os.makedirs("datasets", exist_ok=True)
    results = {
        "model": "llm_8b_batch",
        "sample_size": len(y_true),
        "accuracy": acc,
        "macro_f1": macro_f1,
        "per_class_f1": {cls: float(f) for cls, f in zip(["bug", "feature", "docs", "question"], per_class_f1)},
        "avg_latency_seconds": avg_lat,
        "total_cost_usd": cost,
    }
    with open("datasets/llm_baseline_results.json", "w") as f:
        json.dump(results, f, indent=2)

    os.remove(progress_file)
    print("All issues classified. Checkpoint removed.")

if __name__ == "__main__":
    main()