"""Dependency-free JSON Schema validation for tool arguments.

Only the subset of JSON Schema draft-07 that tool schemas realistically need is
supported; anything unknown is ignored instead of rejected, so richer schemas
degrade gracefully rather than blocking a call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}

_MAX_ERRORS = 8


def type_name(value: Any) -> str:
    """Return the JSON type name of ``value``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def matches_type(value: Any, expected: str) -> bool:
    """Type check that does not treat ``bool`` as ``int``."""
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected in _JSON_TYPES:
        return isinstance(value, _JSON_TYPES[expected])
    return True  # Unknown types are not enforced.


def _describe(expected: str | Sequence[str]) -> str:
    return "/".join(expected) if isinstance(expected, (list, tuple)) else str(expected)


def validate_instance(
    value: Any,
    schema: Mapping[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate one value against one schema node, returning human messages."""
    if not schema:
        return []

    errors: list[str] = []

    expected = schema.get("type")
    if expected is not None:
        options = list(expected) if isinstance(expected, (list, tuple)) else [expected]
        if not any(matches_type(value, option) for option in options):
            errors.append(
                f"{path}: expected type {_describe(options)}, got {type_name(value)}"
            )
            return errors  # Later keywords cannot apply to the wrong type.

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                if re.search(pattern, value) is None:
                    errors.append(f"{path}: does not match pattern {pattern!r}")
            except re.error as exc:
                errors.append(f"{path}: invalid pattern {pattern!r} ({exc})")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")

    if isinstance(value, Mapping):
        properties: Mapping[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: is required")
        for key, item in value.items():
            if key in properties:
                errors.extend(validate_instance(item, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unexpected property")

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(validate_instance(item, item_schema, f"{path}[{index}]"))
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")

    return errors[:_MAX_ERRORS]


def validate_arguments(
    arguments: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> list[str]:
    """Validate a whole tool argument mapping against the tool's input schema."""
    if not isinstance(arguments, Mapping):
        return ["$: arguments must be an object"]
    return validate_instance(dict(arguments), schema)
