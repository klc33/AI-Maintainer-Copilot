# model_server/ner.py
import spacy
from spacy.pipeline import EntityRuler
import re

nlp = spacy.load("en_core_web_sm")

# Add custom patterns for code-shaped entities
ruler = nlp.add_pipe("entity_ruler", before="ner")
patterns = [
    {"label": "IDENTIFIER", "pattern": [{"TEXT": {"REGEX": r"^[a-zA-Z_][\w]*$"}}]},
    {"label": "FILE_PATH", "pattern": [{"TEXT": {"REGEX": r"^[\w/\.-]+\.[a-z]{2,4}$"}}]},
    {"label": "ERROR_TYPE", "pattern": [{"TEXT": {"REGEX": r"^[A-Z][a-zA-Z]*Error$"}}]},
    {"label": "VERSION", "pattern": [{"TEXT": {"REGEX": r"^\d+\.\d+\.\d+$"}}]},
    {"label": "PR_REF", "pattern": [{"TEXT": {"REGEX": r"^#\d+$"}}]},
    {"label": "URL", "pattern": [{"TEXT": {"REGEX": r"^https?://[^\s]+$"}}]},
    {"label": "MODULE", "pattern": [{"TEXT": {"REGEX": r"^[\w]+\.[\w]+$"}}]},
]
ruler.add_patterns(patterns)

def extract_entities(text: str):
    doc = nlp(text)
    entities = [{"text": ent.text, "label": ent.label_} for ent in doc.ents]
    return entities