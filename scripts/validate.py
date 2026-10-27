"""Validate repository JSON, preset schemas, and the official preset index."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate():
    documents = {}
    for path in sorted(ROOT.rglob("*.json")):
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue
        with path.open(encoding="utf-8") as source:
            documents[path.relative_to(ROOT).as_posix()] = json.load(
                source, object_pairs_hook=unique_object, parse_constant=reject_constant
            )

    schema = documents["schema/preset.schema.json"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    preset_paths = {path for path in documents if path.startswith("presets/")}
    require(preset_paths, "No official presets found")
    for path in sorted(preset_paths | {"example.json"}):
        for error in validator.iter_errors(documents[path]):
            location = ".".join(str(part) for part in error.absolute_path)
            raise ValueError(f"{path} ({location}): {error.message}")
        expected_schema = "../schema/preset.schema.json" if path in preset_paths else "schema/preset.schema.json"
        require(documents[path]["$schema"] == expected_schema, f"{path}: invalid schema path")
        contributor_keys = {"contributor", "contributor_url"}
        metadata_keys = set(documents[path]["metadata"])
        if path in preset_paths:
            require(contributor_keys <= metadata_keys, f"{path}: formal presets must include contributor metadata")
        else:
            require(not (contributor_keys & metadata_keys), "example.json must omit automatically generated contributor metadata")

    index = documents["index.json"]
    require(isinstance(index, dict) and set(index) == {"schema_version", "presets"}, "Invalid index fields")
    require(index["schema_version"] == "1.0", "Unsupported index schema version")
    require(isinstance(index["presets"], list), "index.presets must be an array")
    indexed_paths, ids = set(), set()
    for entry in index["presets"]:
        require(isinstance(entry, dict) and set(entry) == {"id", "name", "description", "file"}, "Invalid index entry fields")
        require(all(isinstance(value, str) for value in entry.values()), "Index entry values must be strings")
        path = entry["file"]
        require(path in preset_paths, f"Index file is not an official preset: {path}")
        require(path not in indexed_paths and entry["id"] not in ids, f"Duplicate index entry: {path}")
        require(path == f"presets/{entry['id']}.json", f"File name must match preset ID: {path}")
        metadata = documents[path]["metadata"]
        for key in ("id", "name", "description"):
            require(entry[key] == metadata[key], f"{path}: index metadata mismatch for {key}")
        indexed_paths.add(path)
        ids.add(entry["id"])
    require(indexed_paths == preset_paths, "Every official preset must appear in index.json")
    print(f"OK: {len(documents)} JSON files parsed; example and {len(preset_paths)} official preset(s) pass Schema; index is consistent.")


if __name__ == "__main__":
    try:
        validate()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(f"Validation failed: {error}") from error
