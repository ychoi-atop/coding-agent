import json
from pathlib import Path
from typing import Any, Dict
import sys

from fastapi.testclient import TestClient
from jsonschema import validate as js_validate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.main import app


def _load_contract() -> Dict[str, Any]:
    contract_path = Path("contracts/api_contract.json")
    assert contract_path.exists(), "Missing contracts/api_contract.json"
    return json.loads(contract_path.read_text(encoding="utf-8"))


def _resolve_openapi_ref(spec: Dict[str, Any], ref: str) -> Dict[str, Any]:
    # Example: "#/components/schemas/HealthResponse"
    if not ref.startswith("#/"):
        raise AssertionError(f"Unsupported $ref: {ref}")
    parts = ref[2:].split("/")
    node: Any = spec
    for p in parts:
        assert p in node, f"$ref path not found in OpenAPI spec: {ref} (missing {p})"
        node = node[p]
    assert isinstance(node, dict)
    return node


def _resolve_openapi_schema(spec: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    # Resolve $ref and a simple allOf merge
    if "$ref" in schema:
        return _resolve_openapi_schema(spec, _resolve_openapi_ref(spec, schema["$ref"]))
    if "allOf" in schema:
        merged: Dict[str, Any] = {}
        # naive merge for common keys
        for sub in schema["allOf"]:
            sub_r = _resolve_openapi_schema(spec, sub)
            merged = _deep_merge(merged, sub_r)
        # preserve other keys
        for k, v in schema.items():
            if k != "allOf":
                merged.setdefault(k, v)
        return merged
    return schema


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_contract_schema(contract: Dict[str, Any], node: Dict[str, Any]) -> Dict[str, Any]:
    if "schema" in node:
        assert isinstance(node["schema"], dict)
        return node["schema"]
    if "schema_ref" in node:
        ref = node["schema_ref"]
        schemas = contract.get("schemas", {})
        assert ref in schemas, f"Unknown schema_ref in contract: {ref}"
        assert isinstance(schemas[ref], dict)
        return schemas[ref]
    raise AssertionError("Response/request must include schema or schema_ref")


def _assert_schema_compatible(
    contract_schema: Dict[str, Any], openapi_schema: Dict[str, Any], spec: Dict[str, Any], where: str
) -> None:
    openapi_schema = _resolve_openapi_schema(spec, openapi_schema)

    # Type
    c_type = contract_schema.get("type")
    o_type = openapi_schema.get("type")
    if c_type is not None:
        assert o_type == c_type, f"Type mismatch at {where}: contract={c_type}, openapi={o_type}"

    # Basic constraints to enforce if present in contract
    for k in ["minimum", "maximum", "minLength", "maxLength", "pattern", "format"]:
        if k in contract_schema:
            assert openapi_schema.get(k) == contract_schema.get(k), f"Constraint {k} mismatch at {where}"

    if "enum" in contract_schema:
        assert openapi_schema.get("enum") == contract_schema.get("enum"), f"Enum mismatch at {where}"

    if c_type == "object":
        c_props = contract_schema.get("properties", {})
        o_props = openapi_schema.get("properties", {})
        assert isinstance(o_props, dict), f"OpenAPI schema missing properties at {where}"
        # required
        c_req = set(contract_schema.get("required", []) or [])
        o_req = set(openapi_schema.get("required", []) or [])
        missing_req = c_req - o_req
        assert not missing_req, f"Missing required fields at {where}: {sorted(missing_req)}"

        for pname, pschema in c_props.items():
            assert pname in o_props, f"Missing property at {where}: {pname}"
            assert isinstance(pschema, dict)
            _assert_schema_compatible(pschema, o_props[pname], spec, where + "." + pname)

    if c_type == "array":
        c_items = contract_schema.get("items")
        o_items = openapi_schema.get("items")
        assert isinstance(c_items, dict), f"Contract array missing items at {where}"
        assert isinstance(o_items, dict), f"OpenAPI array missing items at {where}"
        _assert_schema_compatible(c_items, o_items, spec, where + "[]")


def test_api_contract_matches_openapi_and_runtime():
    contract = _load_contract()

    c = TestClient(app)
    spec = c.get("/openapi.json").json()

    paths = spec.get("paths", {})
    assert isinstance(paths, dict)

    for ep in contract.get("endpoints", []):
        method = ep["method"].lower()
        path = ep["path"]

        assert path in paths, f"Contract path missing in OpenAPI: {path}"
        assert method in paths[path], f"Contract method missing in OpenAPI: {method} {path}"
        op = paths[path][method]

        # Request validation (OpenAPI)
        if ep.get("request") is not None:
            req = ep["request"]
            ctype = req.get("content_type", "application/json")
            assert "requestBody" in op, f"Missing requestBody in OpenAPI for {method} {path}"
            content = (op.get("requestBody") or {}).get("content") or {}
            assert ctype in content, f"Missing request content-type in OpenAPI for {method} {path}: {ctype}"
            req_schema = content[ctype].get("schema")
            assert isinstance(req_schema, dict), f"Missing request schema in OpenAPI for {method} {path}"

            contract_req_schema = _resolve_contract_schema(contract, req)
            _assert_schema_compatible(contract_req_schema, req_schema, spec, f"request {method} {path}")

        # Response validation (OpenAPI)
        responses = op.get("responses") or {}
        for code, rdesc in (ep.get("responses") or {}).items():
            assert code in responses, f"Missing response code in OpenAPI for {method} {path}: {code}"
            ctype = rdesc.get("content_type", "application/json")
            content = responses[code].get("content") or {}
            assert ctype in content, (
                f"Missing response content-type in OpenAPI for {method} {path} {code}: {ctype}"
            )
            resp_schema = content[ctype].get("schema")
            assert isinstance(resp_schema, dict), (
                f"Missing response schema in OpenAPI for {method} {path} {code}"
            )

            contract_resp_schema = _resolve_contract_schema(contract, rdesc)
            _assert_schema_compatible(contract_resp_schema, resp_schema, spec, f"response {method} {path} {code}")

        # Runtime validation (optional but recommended):
        # If a 200 response has an example, call the endpoint and validate response JSON.
        r200 = (ep.get("responses") or {}).get("200")
        if r200 and "example" in r200:
            if ep.get("request") is None:
                rr = c.request(method.upper(), path)
            else:
                req = ep["request"]
                example_req = req.get("example")
                # If request exists but no example, skip runtime test (planner/implementer should add examples for stronger checks)
                if example_req is None:
                    continue
                rr = c.request(method.upper(), path, json=example_req)

            assert rr.status_code == 200, f"Runtime status mismatch for {method} {path}: {rr.status_code}"
            instance = rr.json()
            schema = _resolve_contract_schema(contract, r200)
            js_validate(instance=instance, schema=schema)
