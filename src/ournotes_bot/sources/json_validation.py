# L3
# Input: JSON bytes/text or decoded object pairs from independent source adapters.
# Output: JSON values, rejecting duplicate keys and nonfinite numbers.
# Pos: Data / Sources shared validation; see L2-2.md.
# Effects/Dependencies: Standard library only; no I/O or source-specific business rules.
"""Strict JSON decoding shared by independent data sources."""
import json
import math

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result

def decode_json(raw):
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite JSON")
        return result
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_float=finite_float,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
