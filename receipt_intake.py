"""
Receipt intake - step 2 of the workflow.

    image  ->  EXTRACT  ->  normalize()  ->  engine  ->  output

This module is only the EXTRACT box. It turns a receipt into a record plus a
note of which fields it could not read. It makes no decisions: it never returns
APPROVE or REVIEW, never applies a cap, never looks at the policy table. The
engine downstream is unchanged and still fully deterministic.

Two things a receipt cannot tell you, no matter how sharp the photo:

  - manager_approval  - lives in an approval workflow, not on the paper
  - attendees         - a table for four prints one bill

Guessing either would be inventing a value, so they are reported as unknown
and the engine's intake gate turns that into REVIEW when the category actually
depends on them. A client meal needs both; a solo coffee needs neither.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from policy_engine import relevant_for

# Below this, a value is treated as unreadable rather than trusted.
CONFIDENCE_THRESHOLD = 0.80


@dataclass
class Extraction:
    """What came off the receipt, and how sure we are of each piece."""
    values: dict = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)
    source: str = "unknown"

    def unreadable(self, threshold=CONFIDENCE_THRESHOLD):
        """Fields we either never got, or got but don't trust."""
        out = []
        for k, v in self.values.items():
            if v is None or self.confidence.get(k, 0.0) < threshold:
                out.append(k)
        return out


def to_record(extraction: Extraction, record_id: str,
              threshold=CONFIDENCE_THRESHOLD) -> dict:
    """Turn an Extraction into a record the normalizer can take.

    A low-confidence field is dropped to None rather than passed through at its
    guessed value. The names of the dropped fields ride along in _unclear so the
    engine can say WHICH field needs a human, instead of a bare 'check this'.
    """
    rec = {"id": record_id}

    for k, v in extraction.values.items():
        rec[k] = v if extraction.confidence.get(k, 0.0) >= threshold else None

    # We are holding the receipt, so this one is known by construction.
    rec["receipt_present"] = True

    category = rec.get("category")
    relevant = relevant_for(category)
    rec["_unclear"] = sorted(f for f in extraction.unreadable(threshold)
                             if f in relevant)
    rec["_extract_source"] = extraction.source
    return rec


# ---------------------------------------------------------------------------
# Extractors - swap one in without touching anything downstream
# ---------------------------------------------------------------------------
def manual_extractor(_image_path, transcribed: dict,
                     unreadable: tuple = ()) -> Extraction:
    """Human reads the receipt and types what they can see.

    No network, no API key, no model. Anything they couldn't make out goes in
    `unreadable` and is given zero confidence. Useful as the default, and as
    proof that the pipeline's shape doesn't depend on any AI being present.
    """
    values = dict(transcribed)
    conf = {k: (0.0 if k in unreadable else 1.0) for k in values}
    for k in unreadable:
        values.setdefault(k, None)
        conf[k] = 0.0
    return Extraction(values=values, confidence=conf, source="manual")


# There is deliberately no automated extractor here. The extraction step is a
# single box in a fixed pipeline, and its contract is the part that matters:
# take an image, return an Extraction - values plus a per-field confidence.
# Anything satisfying that contract drops in without a line changing
# downstream, because nothing downstream knows how the numbers were obtained.
