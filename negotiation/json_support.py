"""JSON ohne mehrdeutige Schluessel oder nicht endliche Zahlen."""

import json
import math


def _reject_constant(value: str):
    raise ValueError("NaN und Infinity sind keine gueltigen JSON-Zahlen.")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON-Zahl ist zu gross.")
    return parsed


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Doppelte JSON-Schluessel sind nicht erlaubt.")
        result[key] = value
    return result


def loads(value: str | bytes):
    return json.loads(value, parse_constant=_reject_constant, parse_float=_finite_float,
                      object_pairs_hook=_unique_object)
