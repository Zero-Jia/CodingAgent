"""Bounded, fail-closed JSON Schema subset for MCP execution.

Discovery preserves arbitrary schemas; execution rejects unsupported constraints.
No references are resolved and no schema can initiate network IO.
"""

from __future__ import annotations

import json

_ANNOTATIONS = {"title", "description", "default", "examples", "$comment"}
_KEYWORDS = _ANNOTATIONS | {
    "type", "properties", "required", "additionalProperties", "items", "enum", "const",
    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "anyOf",
}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def validate_arguments(schema: dict[str, object], arguments: dict[str, object]) -> None:
    """Validate schema first, including constraints on absent optional properties."""
    encoded = json.dumps([schema, arguments], allow_nan=False)
    if len(encoded) > 128_000:
        raise ValueError("schema or arguments exceed budget")
    _check_schema(schema, 0)
    if not _matches(schema, arguments):
        raise ValueError("arguments do not match supported input schema")


def _check_schema(schema: object, depth: int) -> None:
    if depth > 24 or not isinstance(schema, dict) or set(schema) - _KEYWORDS:
        raise ValueError("unsupported input schema")
    kind = schema.get("type")
    if "type" in schema and (not isinstance(kind, str) or kind not in _TYPES):
        raise ValueError("unsupported schema type")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError("invalid schema bound")
    for key in ("minimum", "maximum"):
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError("invalid numeric bound")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("invalid enum")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
        raise ValueError("invalid required")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("invalid properties")
    for child in properties.values():
        _check_schema(child, depth + 1)
    if "items" in schema:
        _check_schema(schema["items"], depth + 1)
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        _check_schema(additional, depth + 1)
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not branches:
            raise ValueError("invalid anyOf")
        for child in branches:
            _check_schema(child, depth + 1)


def _equal(left: object, right: object) -> bool:
    # JSON booleans must not compare equal to numbers (Python True == 1).
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(v, right[k]) for k, v in left.items())
    return bool(left == right)


def _matches(schema: dict[str, object], value: object) -> bool:
    kinds = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "null": value is None, "number": type(value) in (int, float),
        "integer": type(value) is int or (type(value) is float and value.is_integer()),
    }
    kind = schema.get("type")
    if isinstance(kind, str) and not kinds[kind]:
        return False
    if "const" in schema and not _equal(value, schema["const"]):
        return False
    choices = schema.get("enum")
    if isinstance(choices, list) and not any(_equal(value, choice) for choice in choices):
        return False
    branches = schema.get("anyOf")
    if isinstance(branches, list) and not any(_matches(child, value) for child in branches):
        return False
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list) and any(key not in value for key in required):
            return False
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for key, item in value.items():
            child = properties.get(key, schema.get("additionalProperties", True))
            if child is False or (isinstance(child, dict) and not _matches(child, item)):
                return False
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict) and not all(_matches(items, item) for item in value):
            return False
    if isinstance(value, str | list):
        low, high = (
            ("minLength", "maxLength") if isinstance(value, str) else ("minItems", "maxItems")
        )
        minimum, maximum = schema.get(low), schema.get(high)
        if isinstance(minimum, int) and len(value) < minimum:
            return False
        if isinstance(maximum, int) and len(value) > maximum:
            return False
    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            return False
        if isinstance(maximum, int | float) and value > maximum:
            return False
    return True
