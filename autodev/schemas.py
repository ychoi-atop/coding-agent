from __future__ import annotations

from .validators import DEFAULT_VALIDATOR_NAMES

VALIDATORS = list(DEFAULT_VALIDATOR_NAMES)
QUALITY_LEVELS = ["minimal", "balanced", "strict"]

NONEMPTY_STRING = {"type": "string", "minLength": 1}
QUALITY_PATTERN = {"type": "string", "enum": QUALITY_LEVELS}
QUALITY_EXPECTATION_SCHEMA = {
    "type": "object",
    "required": ["requires_tests", "requires_error_contract"],
    "properties": {
        "requires_tests": {"type": "boolean"},
        "requires_error_contract": {"type": "boolean"},
        "touches_contract": {"type": "boolean"},
    },
    "additionalProperties": False,
}
SEMVER = r"^\d+\.\d+(\.\d+)?$"

TASK_VALIDATION_FOCUS = {"type": "array", "items": {"type": "string", "enum": VALIDATORS}}

PRD_SCHEMA = {
  "type": "object",
  "required": ["title", "goals", "non_goals", "features", "acceptance_criteria", "nfr", "constraints"],
  "properties": {
    "title": {"type":"string"},
    "goals": {"type":"array","items":{"type":"string"}},
    "non_goals": {"type":"array","items":{"type":"string"}},
    "personas": {"type":"array","items":{"type":"string"}},
    "features": {"type":"array","items":{
      "type":"object",
      "required":["name","description","requirements"],
      "properties":{
        "name":{"type":"string"},
        "description":{"type":"string"},
        "requirements":{"type":"array","items":{"type":"string"}},
        "api_surface":{"type":"array","items":{"type":"string"}},
      },
      "additionalProperties": False
    }},
    "acceptance_criteria": {"type":"array","items":{"type":"string"}},
    "nfr": {"type":"object"},
    "constraints": {"type":"array","items":{"type":"string"}},
    "performance_targets": {"type": "object"},
    "expected_load": {"type": "object"},
    "latency_sensitive_paths": {"type": "array", "items": {"type": "string"}},
    "cost_priority": {"type": "string"},
  },
  "additionalProperties": False
}

PLAN_SCHEMA = {
  "type":"object",
  "required":["project","tasks","ci","docker","security","observability"],
  "properties":{
    "project":{
      "type":"object",
      "required":["type","name"],
      "properties":{
        "type":{"type":"string","minLength": 1},
        "name":{"type":"string", "minLength": 1},
        "python_version":{"type":"string", "pattern": SEMVER},
        "version":{"type":"string"},
        "quality_level":{"type":"string","enum": QUALITY_LEVELS},
        "default_artifacts":{"type":"array","items":{"type":"string","minLength":1}},
        "quality_gate_profile":{"type":"string","enum": QUALITY_LEVELS},
      },
      "additionalProperties": False
    },
    "runtime_dependencies":{"type":"array","items":{"type":"string"}},
    "dev_dependencies":{"type":"array","items":{"type":"string"}},
    "tasks":{
      "type":"array",
      "items":{
        "type":"object",
            "required":["id","title","goal","acceptance","files","depends_on","quality_expectations"],
        "properties":{
          "id":{"type":"string","minLength": 1},
          "title":{"type":"string","minLength": 5},
          "goal":{"type":"string","minLength": 8},
          "acceptance":{"type":"array","items":{"type":"string","minLength": 5},"minItems": 1,"uniqueItems": True},
          "files":{"type":"array","items":{"type":"string","minLength": 1},"minItems": 1,"uniqueItems": True},
          "depends_on":{"type":"array","items":{"type":"string","minLength": 1}},
            "quality_expectations": QUALITY_EXPECTATION_SCHEMA,
          "validator_focus":{"type":"array","items":{"type":"string", "enum": VALIDATORS}},
        },
        "anyOf": [
          {
            "if": {
              "properties": {
                "quality_expectations": {
                  "properties": {"requires_error_contract": {"const": True}}
                }
              }
            },
            "then": {
              "properties": {
                "acceptance": {
                  "contains": {"type": "string", "pattern": "(?i)(error|validation|exception|fallback)"}
                }
              }
            }
          },
          {
            "if": {
              "properties": {
                "quality_expectations": {
                  "properties": {"requires_tests": {"const": True}}
                }
              }
            },
            "then": {
              "properties": {
                "acceptance": {
                  "contains": {"type": "string", "pattern": "(?i)(test|coverage|assert)"}
                }
              }
            }
          },
        ],
        "additionalProperties": False
      }
    },
    "ci":{
      "type":"object",
      "required":["enabled","provider"],
      "properties":{
        "enabled":{"type":"boolean"},
        "provider":{"type":"string","enum":["github_actions"]}
      },
      "additionalProperties": False
    },
    "docker":{
      "type":"object",
      "required":["enabled"],
      "properties":{"enabled":{"type":"boolean"}},
      "additionalProperties": False
    },
    "security":{
      "type":"object",
      "required":["enabled","tools"],
      "properties":{
        "enabled":{"type":"boolean"},
        "tools":{"type":"array","items":{"type":"string","enum":["pip_audit","bandit","semgrep"]}}
      },
      "additionalProperties": False
    },
    "observability":{
      "type":"object",
      "required":["enabled"],
      "properties":{"enabled":{"type":"boolean"}},
      "additionalProperties": False
    },
    "performance_targets": {"type": "object"},
    "expected_load": {"type": "object"},
    "latency_sensitive_paths": {"type": "array", "items": {"type": "string"}},
    "cost_priority": {"type": "string"},
    
  },
  "additionalProperties": False
}

ARCHITECTURE_SCHEMA = {
    "type": "object",
    "required": ["components", "data_models", "api_contracts", "technology_decisions", "constraints"],
    "properties": {
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "responsibility", "interfaces"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "responsibility": {"type": "string", "minLength": 5},
                    "interfaces": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "data_models": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "fields"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "type": {"type": "string", "minLength": 1},
                                "required": {"type": "boolean"},
                                "description": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "description": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "api_contracts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["method", "path", "description"],
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "minLength": 1},
                    "description": {"type": "string"},
                    "request_body": {"type": "object"},
                    "response_body": {"type": "object"},
                    "status_codes": {"type": "array", "items": {"type": "integer"}},
                },
                "additionalProperties": False,
            },
        },
        "technology_decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["area", "choice", "rationale"],
                "properties": {
                    "area": {"type": "string", "minLength": 1},
                    "choice": {"type": "string", "minLength": 1},
                    "rationale": {"type": "string", "minLength": 5},
                    "alternatives_considered": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "constraints": {"type": "array", "items": {"type": "string"}},
        "database": {
            "type": "object",
            "properties": {
                "tables": {"type": "array", "items": {"type": "object"}},
                "relationships": {"type": "array", "items": {"type": "object"}},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["findings", "overall_verdict"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file", "severity", "description", "suggestion"],
                "properties": {
                    "file": {"type": "string", "minLength": 1},
                    "severity": {"type": "string", "enum": ["critical", "major", "minor", "info"]},
                    "description": {"type": "string", "minLength": 5},
                    "suggestion": {"type": "string", "minLength": 1},
                    "line_hint": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "overall_verdict": {"type": "string", "enum": ["approve", "request_changes"]},
        "blocking_issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "summary": {"type": "string"},
    },
    "additionalProperties": False,
}

CHANGESET_SCHEMA = {
  "type":"object",
  "required":["role","summary","changes","notes","handoff"],
  "properties":{
    "role":{"type":"string"},
    "summary":{"type":"string"},
    "quality_notes":{"type":"array","items":{"type":"string"}},
    "validation_links":{
      "type":"object",
      "required":["acceptance","tasks"],
      "properties":{
        "acceptance":{"type":"array","minItems": 1,"uniqueItems": True,"items":{"type":"string","minLength": 1}},
        "tasks":{"type":"array","minItems": 1,"uniqueItems": True,"items":{"type":"string","minLength": 1}},
        "validators":{"type":"array","items":{"type":"string","enum": VALIDATORS}}
      },
      "additionalProperties": False
    },
    "handoff":{
      "type":"object",
      "required":["Summary","Changed Files","Commands","Evidence","Risks","Next Input"],
      "properties":{
        "Summary":{"type":"string","minLength":1},
        "Changed Files":{"type":"array","items":{"type":"string","minLength":1}},
        "Commands":{"type":"array","items":{"type":"string","minLength":1}},
        "Evidence":{"type":"array","items":{"type":"string","minLength":1}},
        "Risks":{"type":"array","items":{"type":"string","minLength":1}},
        "Next Input":{"type":"string","minLength":1}
      },
      "additionalProperties": False
    },
    "changes":{
      "type":"array",
      "items":{
        "type":"object",
        "required":["op","path"],
        "properties":{
          "op":{"type":"string","enum":["write","delete","patch"]},
          "path":{"type":"string"},
          "content":{"type":"string"}
        },
        "allOf": [
          {
            "if": {"properties": {"op": {"const": "write"}}},
            "then": {"required": ["content"]}
          },
          {
            "if": {"properties": {"op": {"const": "patch"}}},
            "then": {"required": ["content"]}
          }
        ],
        "additionalProperties": False
      }
    },
    "notes":{"type":"array","items":{"type":"string"}}
  },
  "additionalProperties": False
}
