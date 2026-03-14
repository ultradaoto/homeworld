"""
Vector memory via ChromaDB — Phase 2 will populate this with harvested findings.
"""
import os
import chromadb
from config import STATE_DIR

_client = None

def _get_client():
    global _client
    if _client is None:
        persist_dir = os.path.join(STATE_DIR, "chroma")
        os.makedirs(persist_dir, exist_ok=True)
        _client = chromadb.PersistentClient(path=persist_dir)
    return _client

def get_collection(name: str = "findings"):
    return _get_client().get_or_create_collection(name)

def add(texts: list[str], ids: list[str], metadatas: list[dict] = None):
    col = get_collection()
    col.add(documents=texts, ids=ids, metadatas=metadatas or [{} for _ in texts])

def query(text: str, n: int = 5) -> list[str]:
    col = get_collection()
    results = col.query(query_texts=[text], n_results=n)
    return results["documents"][0] if results["documents"] else []
