# evals/rag/validate_golden.py
"""Check each line of golden.jsonl for valid JSON and print problematic ones."""
import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.jsonl"

with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines, 1):
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
        # Check required fields
        if not all(k in data for k in ("question", "ideal_answer", "chunk_ids")):
            print(f"Line {i}: missing required keys. Fields: {list(data.keys())}")
        else:
            print(f"Line {i}: OK")
    except json.JSONDecodeError as e:
        print(f"Line {i}: JSON ERROR – {e}")
        print(f"  Content (first 200 chars): {line[:200]}")