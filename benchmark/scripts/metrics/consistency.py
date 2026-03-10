"""
Output consistency metrics using TF-IDF cosine similarity.

Measures how consistent multiple runs of the same benchmark case are in
terms of output content and structure.

Public API
----------
ConsistencyMetrics              — aggregated consistency scores.
compute_consistency(outputs)    — compute all consistency metrics.
compute_pairwise_similarity(outputs) -> list[float]
compute_structural_consistency(outputs) -> float
extract_structure(output) -> list[str]
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ConsistencyMetrics:
    """Aggregated consistency scores across multiple outputs."""
    mean_similarity: float = 0.0
    similarity_std: float = 0.0
    identical_structure_rate: float = 0.0


def extract_structure(output: str) -> list[str]:
    """
    Extract structural elements (headings, numbered steps) from output.

    Returns a list of normalized structural markers.
    """
    lines = output.splitlines()
    structure = []
    for line in lines:
        stripped = line.strip()
        # Markdown headings
        if re.match(r"^#{1,6}\s+", stripped):
            structure.append(stripped.lower())
        # Numbered steps
        elif re.match(r"^\d+[\.\)]\s+", stripped):
            # Normalize to just the step number
            match = re.match(r"^(\d+)[\.\)]\s+(.+)", stripped)
            if match:
                structure.append(f"step_{match.group(1)}")
        # Bullet points at top level
        elif re.match(r"^[-*]\s+\S", stripped):
            structure.append("bullet")
    return structure


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer."""
    return re.findall(r"\b\w+\b", text.lower())


def _build_tfidf_vectors(documents: list[str]) -> list[dict[str, float]]:
    """
    Build TF-IDF vectors for a list of documents.

    Self-contained implementation (no sklearn dependency).
    """
    # Tokenize
    doc_tokens = [_tokenize(doc) for doc in documents]
    n_docs = len(doc_tokens)

    if n_docs == 0:
        return []

    # Document frequency
    df: Counter = Counter()
    for tokens in doc_tokens:
        unique = set(tokens)
        for token in unique:
            df[token] += 1

    # IDF (smoothed to avoid zero for terms appearing in all documents)
    idf = {term: math.log((1 + n_docs) / (1 + count)) + 1 for term, count in df.items()}

    # TF-IDF per document
    vectors = []
    for tokens in doc_tokens:
        tf: Counter = Counter(tokens)
        total = len(tokens) or 1
        vec = {}
        for term, count in tf.items():
            vec[term] = (count / total) * idf.get(term, 0)
        vectors.append(vec)

    return vectors


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Compute cosine similarity between two sparse vectors."""
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0.0

    dot = sum(a[k] * b[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def compute_pairwise_similarity(outputs: list[str]) -> list[float]:
    """
    Compute pairwise TF-IDF cosine similarity between all output pairs.

    Returns a list of similarity scores (one per unique pair).
    """
    if len(outputs) < 2:
        return []

    vectors = _build_tfidf_vectors(outputs)
    similarities = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            sim = _cosine_similarity(vectors[i], vectors[j])
            similarities.append(sim)

    return similarities


def compute_structural_consistency(outputs: list[str]) -> float:
    """
    Compute structural consistency as the rate of identical heading sequences.

    Extracts structural markers from each output and checks how many pairs
    have identical structure.
    """
    if len(outputs) < 2:
        return 1.0

    structures = [extract_structure(out) for out in outputs]
    total_pairs = 0
    identical_pairs = 0

    for i in range(len(structures)):
        for j in range(i + 1, len(structures)):
            total_pairs += 1
            if structures[i] == structures[j]:
                identical_pairs += 1

    return identical_pairs / total_pairs if total_pairs > 0 else 1.0


def compute_consistency(outputs: list[str]) -> ConsistencyMetrics:
    """
    Compute all consistency metrics for a list of outputs.

    Parameters
    ----------
    outputs : list[str]
        Raw text outputs from multiple runs of the same benchmark case.

    Returns
    -------
    ConsistencyMetrics
        Aggregated similarity and structural consistency scores.
    """
    if len(outputs) < 2:
        return ConsistencyMetrics(
            mean_similarity=1.0,
            similarity_std=0.0,
            identical_structure_rate=1.0,
        )

    similarities = compute_pairwise_similarity(outputs)
    structural_rate = compute_structural_consistency(outputs)

    if not similarities:
        return ConsistencyMetrics(
            mean_similarity=0.0,
            similarity_std=0.0,
            identical_structure_rate=structural_rate,
        )

    arr = np.array(similarities)
    return ConsistencyMetrics(
        mean_similarity=round(float(np.mean(arr)), 4),
        similarity_std=round(float(np.std(arr)), 4),
        identical_structure_rate=round(structural_rate, 4),
    )
