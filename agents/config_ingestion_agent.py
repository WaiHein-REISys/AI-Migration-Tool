"""
Config Ingestion Agent
======================
Loads and validates skillset-config.json and rules-config.json against their
JSON Schemas. Returns a unified config dict used by all downstream agents.
"""

import json
import logging
from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import ValidationError
except ImportError:
    raise ImportError("jsonschema is required. Run: pip install jsonschema")

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when a config file fails schema validation."""


class ConfigIngestionAgent:
    """
    Validates and loads the two config files that drive the entire pipeline:
      - skillset-config.json  : source/target stack definitions and component mappings
      - rules-config.json     : guardrails, ambiguity thresholds, approval settings
    """

    SKILLSET_SCHEMA_PATH = Path(__file__).parent.parent / "config" / "schemas" / "skillset-schema.json"
    RULES_SCHEMA_PATH    = Path(__file__).parent.parent / "config" / "schemas" / "rules-schema.json"

    def __init__(
        self,
        skillset_path: str | Path,
        rules_path: str | Path,
    ) -> None:
        self.skillset_path = Path(skillset_path)
        self.rules_path    = Path(rules_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_and_validate(self) -> dict[str, Any]:
        """
        Load both config files, validate against schemas, and return merged config.

        Returns:
            {
              "skillset": <skillset dict>,
              "rules":    <rules dict>,
              "mappings_index": {<id>: <mapping>},   # MAP-001 → mapping dict
              "rules_index":    {<id>: <rule>},       # RULE-001 → rule dict
            }

        Raises:
            FileNotFoundError      – if a config or schema file is missing
            ConfigValidationError  – if a config file fails schema validation
        """
        logger.info("Loading skillset config from: %s", self.skillset_path)
        skillset = self._load_json(self.skillset_path)

        logger.info("Loading rules config from: %s", self.rules_path)
        rules = self._load_json(self.rules_path)

        logger.info("Validating skillset config against schema...")
        self._validate(skillset, self.SKILLSET_SCHEMA_PATH)

        logger.info("Validating rules config against schema...")
        self._validate(rules, self.RULES_SCHEMA_PATH)

        # Build fast-lookup indexes
        mappings_index = {m["id"]: m for m in skillset.get("component_mappings", [])}
        rules_index    = {r["id"]: r for r in rules.get("guardrails", [])}

        config = {
            "skillset": skillset,
            "rules":    rules,
            "mappings_index": mappings_index,
            "rules_index":    rules_index,
        }

        logger.info(
            "Config loaded successfully. %d component mappings, %d guardrail rules.",
            len(mappings_index),
            len(rules_index),
        )
        return config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _validate(self, config: dict, schema_path: Path) -> None:
        schema = self._load_json(schema_path)
        try:
            jsonschema.validate(instance=config, schema=schema)
        except ValidationError as exc:
            raise ConfigValidationError(
                f"Config validation failed at '{exc.json_path}': {exc.message}"
            ) from exc

    # ------------------------------------------------------------------
    # Convenience helpers (used by downstream agents)
    # ------------------------------------------------------------------

    @staticmethod
    def get_mapping_for_pattern(config: dict, source_pattern_keyword: str) -> dict | None:
        """Return the first mapping whose source_pattern contains the keyword."""
        for mapping in config["skillset"].get("component_mappings", []):
            if source_pattern_keyword.lower() in mapping["source_pattern"].lower():
                return mapping
        return None

    @staticmethod
    def get_blocking_rules(config: dict, applies_to: str) -> list[dict]:
        """Return all blocking rules that apply to the given layer."""
        return [
            r for r in config["rules"].get("guardrails", [])
            if r["enforcement"] == "blocking"
            and (applies_to in r["applies_to"] or "all" in r["applies_to"])
        ]

    @staticmethod
    def get_flagged_libraries(config: dict) -> list[str]:
        """Return all libraries flagged by RULE-008 (external library halt)."""
        rules_index = config.get("rules_index", {})
        rule_008 = rules_index.get("RULE-008", {})
        return rule_008.get("flagged_libraries", [])

    @staticmethod
    def get_confidence_floor(config: dict) -> float:
        return config["rules"]["ambiguity_thresholds"]["confidence_floor"]
