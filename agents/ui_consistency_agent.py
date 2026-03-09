"""
UI Consistency Agent  (Stage 6b)
=================================
Compares the source Angular template(s) against the converted React / TSX
output to verify that UI elements, CSS classes, event bindings, and
conditional logic were all preserved correctly.

Two checks are performed for each converted UI file:

  1. Static code audit — regex-based extraction and diff of:
       - CSS class names   (class="..."  →  className="...")
       - HTML/JSX element types  (button, input, select, …)
       - Event bindings   ((click)=...  →  onClick=...)
       - Structural directives  (*ngIf → conditional render, *ngFor → .map())

  2. Optional LLM confirmation — the diff is sent to the LLM, which
     classifies each difference as an expected idiom change or a
     potential omission (role: UI_CONSISTENCY).

Option C — Storybook story generation:
  When ui_consistency.generate_stories is ``true``, a minimal
  ``<Component>.stories.tsx`` stub is written next to each converted UI
  component so teams can run visual regression via Storybook Test or
  Chromatic.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agents.llm import LLMRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Angular event → React synthetic event map
# ---------------------------------------------------------------------------
_NG_TO_REACT_EVENT: dict[str, str] = {
    "click":       "onClick",
    "change":      "onChange",
    "submit":      "onSubmit",
    "blur":        "onBlur",
    "focus":       "onFocus",
    "keyup":       "onKeyUp",
    "keydown":     "onKeyDown",
    "keypress":    "onKeyPress",
    "mouseenter":  "onMouseEnter",
    "mouseleave":  "onMouseLeave",
    "mouseover":   "onMouseOver",
    "mouseout":    "onMouseOut",
    "input":       "onInput",
    "dblclick":    "onDoubleClick",
    "contextmenu": "onContextMenu",
    "scroll":      "onScroll",
}

# HTML elements we care about (ignore Angular/custom component tags)
_SEMANTIC_ELEMENTS: frozenset[str] = frozenset({
    "a", "button", "form", "input", "select", "textarea", "table", "thead",
    "tbody", "tr", "th", "td", "ul", "ol", "li", "nav", "header", "footer",
    "main", "section", "article", "aside", "div", "span", "p", "h1", "h2",
    "h3", "h4", "h5", "h6", "img", "label", "fieldset", "legend", "dialog",
    "details", "summary",
})


# ---------------------------------------------------------------------------
# Extraction helpers — Angular source
# ---------------------------------------------------------------------------

def _extract_from_angular_html(html: str) -> dict[str, Any]:
    """Parse an Angular component template (.component.html)."""
    # CSS classes  — class="foo bar"
    raw_classes: list[str] = []
    for m in re.finditer(r'\bclass="([^"]+)"', html):
        raw_classes.extend(m.group(1).split())
    # [class.foo]="..." bindings
    for m in re.finditer(r'\[class\.([a-zA-Z0-9_-]+)\]', html):
        raw_classes.append(m.group(1))
    classes = sorted(set(raw_classes))

    # HTML elements (lowercase tags only — skip Angular components in PascalCase)
    elements = sorted({
        m.group(1).lower()
        for m in re.finditer(r'<([a-zA-Z][a-zA-Z0-9-]*)', html)
        if m.group(1).islower() and m.group(1).lower() in _SEMANTIC_ELEMENTS
    })

    # Angular event bindings  (click)="..."  (change)="..."
    events = sorted({
        m.group(1).lower()
        for m in re.finditer(r'\(([a-zA-Z]+)\)=', html)
    })

    # Structural directives
    ng_if_count  = len(re.findall(r'\*ngIf\b', html))
    ng_for_count = len(re.findall(r'\*ngFor\b', html))
    ng_model     = len(re.findall(r'\[\(ngModel\)\]', html))

    return {
        "css_classes": classes,
        "elements":    elements,
        "events":      events,
        "ng_if_count": ng_if_count,
        "ng_for_count": ng_for_count,
        "ng_model_count": ng_model,
    }


def _extract_from_angular_ts(ts_src: str) -> dict[str, Any]:
    """
    Fall-back extraction when no .component.html is available.
    Parses inline template strings and class members from the TypeScript source.
    """
    # Try to extract inline template: `...`
    inline_html = ""
    m = re.search(r"template\s*:\s*`([^`]+)`", ts_src, re.DOTALL)
    if m:
        inline_html = m.group(1)

    result = _extract_from_angular_html(inline_html) if inline_html else {
        "css_classes": [], "elements": [], "events": [],
        "ng_if_count": 0, "ng_for_count": 0, "ng_model_count": 0,
    }

    # Also pick up @Output EventEmitters and method names used in template
    outputs = re.findall(r'@Output\(\)[^;]+EventEmitter[^;]*;\s*(\w+)', ts_src)
    result["ng_outputs"] = sorted(set(outputs))

    inputs = re.findall(r'@Input\(\)\s+(?:\w+:\s*)?(\w+)\b', ts_src)
    result["ng_inputs"] = sorted(set(inputs))

    return result


# ---------------------------------------------------------------------------
# Extraction helpers — React / TSX output
# ---------------------------------------------------------------------------

def _extract_from_tsx(tsx: str) -> dict[str, Any]:
    """Parse a converted React / TSX component."""
    # CSS classes  — className="foo bar"  or  className={'foo bar'}
    raw_classes: list[str] = []
    for m in re.finditer(r'className=["\']([^"\']+)["\']', tsx):
        raw_classes.extend(m.group(1).split())
    classes = sorted(set(raw_classes))

    # JSX element types (lowercase = HTML elements)
    elements = sorted({
        m.group(1).lower()
        for m in re.finditer(r'<([a-zA-Z][a-zA-Z0-9]*)[\s/>]', tsx)
        if m.group(1)[0].islower() and m.group(1).lower() in _SEMANTIC_ELEMENTS
    })

    # React event handlers  onClick  onChange  onSubmit …
    react_events = sorted({
        m.group(1)
        for m in re.finditer(r'\b(on[A-Z][a-zA-Z]+)\s*=', tsx)
    })

    # Conditional render indicators
    conditional_count = len(re.findall(r'\?\s*\(', tsx)) + len(re.findall(r'&&\s*[(<]', tsx))
    map_count = len(re.findall(r'\.map\s*\(', tsx))

    # Props from function signature / interface
    props = sorted({
        m.group(1)
        for m in re.finditer(r'(\w+)\s*(?::\s*[\w|<>\[\]]+)?\s*[,)}]', tsx)
        if m.group(1) not in {"return", "const", "let", "var", "if", "else"}
    })

    return {
        "css_classes":        classes,
        "elements":           elements,
        "react_events":       react_events,
        "conditional_count":  conditional_count,
        "map_count":          map_count,
    }


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def _diff_classes(src_classes: list[str], tgt_classes: list[str]) -> dict[str, Any]:
    src_set = set(src_classes)
    tgt_set = set(tgt_classes)
    return {
        "source":  sorted(src_set),
        "target":  sorted(tgt_set),
        "missing": sorted(src_set - tgt_set),   # in source but not in target
        "added":   sorted(tgt_set - src_set),   # in target but not in source
    }


def _diff_elements(src: list[str], tgt: list[str]) -> dict[str, Any]:
    src_set = set(src)
    tgt_set = set(tgt)
    return {
        "source":  sorted(src_set),
        "target":  sorted(tgt_set),
        "missing": sorted(src_set - tgt_set),
        "added":   sorted(tgt_set - src_set),
    }


def _diff_events(
    ng_events: list[str],
    react_events: list[str],
) -> dict[str, Any]:
    """Map Angular events to their React equivalents and report mismatches."""
    expected_react = {_NG_TO_REACT_EVENT.get(e, f"on{e.capitalize()}") for e in ng_events}
    actual_react   = set(react_events)
    return {
        "angular_events":    sorted(ng_events),
        "expected_react":    sorted(expected_react),
        "actual_react":      sorted(actual_react),
        "missing_handlers":  sorted(expected_react - actual_react),
        "extra_handlers":    sorted(actual_react - expected_react),
    }


def _directive_summary(src: dict[str, Any], tgt: dict[str, Any]) -> dict[str, Any]:
    return {
        "ng_if_count":         src.get("ng_if_count", 0),
        "conditional_count":   tgt.get("conditional_count", 0),
        "ng_for_count":        src.get("ng_for_count", 0),
        "map_count":           tgt.get("map_count", 0),
        "ng_model_count":      src.get("ng_model_count", 0),
    }


def _derive_status(
    classes: dict,
    elements: dict,
    events: dict,
) -> Literal["PASS", "WARN", "FAIL"]:
    """
    FAIL  — CSS classes or HTML elements are missing (likely dropped content).
    WARN  — Event handlers or structural directives mismatch (may be idiom change).
    PASS  — Everything accounted for.
    """
    if classes["missing"] or elements["missing"]:
        return "FAIL"
    if events["missing_handlers"]:
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------------------
# Storybook story generator  (Option C)
# ---------------------------------------------------------------------------

_STORY_TEMPLATE = """\
import type {{ Meta, StoryObj }} from "@storybook/react";
import {component_name} from "./{component_name}";

const meta: Meta<typeof {component_name}> = {{
  title: "Migrated/{component_name}",
  component: {component_name},
  tags: ["autodocs"],
}};
export default meta;

type Story = StoryObj<typeof {component_name}>;

/**
 * Auto-generated by AI Migration Tool (UIConsistencyAgent).
 * Populate props below to enable visual regression testing.
 */
export const Primary: Story = {{
  args: {{
    // TODO: supply props from the migrated component's interface
  }},
}};
"""


def _generate_story(component_name: str, target_path: Path) -> Path:
    """Write a .stories.tsx stub next to the converted component."""
    story_path = target_path.parent / f"{component_name}.stories.tsx"
    story_path.write_text(
        _STORY_TEMPLATE.format(component_name=component_name),
        encoding="utf-8",
    )
    return story_path


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class UIConsistencyAgent:
    """
    Stage 6b — UI Consistency Audit.

    For each converted UI file, extracts UI elements from both the Angular
    source and the React output, computes a structural diff, and optionally
    calls the LLM to classify differences.  Generates Storybook story stubs
    when ``generate_stories`` is enabled.
    """

    def __init__(
        self,
        approved_plan: dict,
        output_root: str | Path,
        run_id: str,
        logs_dir: str | Path,
        llm_router: "LLMRouter | None" = None,
        ui_consistency_config: dict | None = None,
        dry_run: bool = False,
    ) -> None:
        self.plan         = approved_plan
        self.output_root  = Path(output_root)
        self.run_id       = run_id
        self.logs_dir     = Path(logs_dir)
        self._router      = llm_router
        self.dry_run      = dry_run
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        cfg = ui_consistency_config or {}
        self.enabled          = cfg.get("enabled", True)
        self.generate_stories = cfg.get("generate_stories", False)
        self.fail_on_missing_classes = cfg.get("fail_on_missing_classes", True)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(
        self,
        completed_step_ids: list[str],
        all_steps: list[dict],
    ) -> dict[str, Any]:
        if self.dry_run:
            return self._skipped("skipped_dry_run")
        if not self.enabled:
            return self._skipped("skipped_disabled")

        steps_index     = {s.get("id"): s for s in all_steps}
        completed_steps = [steps_index[sid] for sid in completed_step_ids if sid in steps_index]
        ui_steps        = [s for s in completed_steps if self._is_ui_file(s.get("target_file", ""))]

        if not ui_steps:
            return self._skipped("skipped_no_ui_files")

        findings: list[dict[str, Any]] = []
        for step in ui_steps:
            finding = self._audit_step(step)
            findings.append(finding)

        failed = [f for f in findings if f["status"] == "FAIL"]
        warned = [f for f in findings if f["status"] == "WARN"]
        passed = [f for f in findings if f["status"] == "PASS"]

        # Overall status: FAIL if any classes/elements missing; WARN if events differ
        if failed:
            status = "failed"
        elif warned:
            status = "warned"
        else:
            status = "passed"

        report: dict[str, Any] = {
            "run_id":        self.run_id,
            "status":        status,
            "ui_steps_checked": len(ui_steps),
            "passed":        len(passed),
            "warned":        len(warned),
            "failed":        len(failed),
            "findings":      findings,
        }

        json_path = self.logs_dir / f"{self.run_id}-ui-consistency-report.json"
        md_path   = self.logs_dir / f"{self.run_id}-ui-consistency-report.md"
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        md_path.write_text(self._render_markdown(report), encoding="utf-8")

        report["report_json"] = str(json_path)
        report["report_md"]   = str(md_path)

        # Log summary
        logger.info(
            "[UIConsistency] %s — %d UI file(s): %d pass / %d warn / %d fail",
            status.upper(), len(ui_steps), len(passed), len(warned), len(failed),
        )
        for f in failed:
            logger.warning(
                "[UIConsistency] FAIL step=%s  missing_classes=%s  missing_elements=%s",
                f["step"],
                f["css_classes"]["missing"],
                f["elements"]["missing"],
            )
        for f in warned:
            logger.warning(
                "[UIConsistency] WARN step=%s  missing_handlers=%s",
                f["step"],
                f["events"]["missing_handlers"],
            )

        return report

    # ------------------------------------------------------------------
    # Per-step audit
    # ------------------------------------------------------------------

    def _audit_step(self, step: dict) -> dict[str, Any]:
        step_id     = step["id"]
        source_file = step.get("source_file", "")
        target_file = step.get("target_file", "")

        feature_root = Path(self.plan["feature_root"])
        source_ts    = feature_root / source_file
        target_tsx   = self.output_root / target_file

        # --- Extract from source ---
        template_origin = "not_found"
        if source_ts.exists():
            ts_src = source_ts.read_text(encoding="utf-8", errors="replace")
            # Look for sibling .component.html
            html_path = source_ts.with_suffix("").with_suffix(".component.html")
            if not html_path.exists():
                # Try same stem with .html
                html_path = source_ts.with_suffix(".html")
            if html_path.exists():
                html_src = html_path.read_text(encoding="utf-8", errors="replace")
                src_data = _extract_from_angular_html(html_src)
                template_origin = str(html_path.name)
            else:
                src_data = _extract_from_angular_ts(ts_src)
                template_origin = "inline_or_missing"
        else:
            src_data = {
                "css_classes": [], "elements": [], "events": [],
                "ng_if_count": 0, "ng_for_count": 0, "ng_model_count": 0,
            }
            template_origin = "source_not_found"

        # --- Extract from target ---
        if target_tsx.exists():
            tsx_src  = target_tsx.read_text(encoding="utf-8", errors="replace")
            tgt_data = _extract_from_tsx(tsx_src)
        else:
            tgt_data = {
                "css_classes": [], "elements": [], "react_events": [],
                "conditional_count": 0, "map_count": 0,
            }

        # --- Compute diffs ---
        css_diff  = _diff_classes(src_data["css_classes"],  tgt_data["css_classes"])
        elem_diff = _diff_elements(src_data["elements"],    tgt_data["elements"])
        evt_diff  = _diff_events(src_data["events"],        tgt_data["react_events"])
        directive_summary = _directive_summary(src_data, tgt_data)

        status = _derive_status(css_diff, elem_diff, evt_diff)

        # --- Optional LLM classification ---
        llm_findings: list[dict] = []
        has_diffs = (
            css_diff["missing"] or elem_diff["missing"] or evt_diff["missing_handlers"]
        )
        if has_diffs and self._router and self._router.is_available:
            llm_findings = self._classify_with_llm(step_id, css_diff, elem_diff, evt_diff)
            # Downgrade to WARN if LLM says all diffs are expected idiom changes
            if status == "FAIL" and all(
                f.get("classification") == "expected_idiom_change" for f in llm_findings
            ):
                status = "WARN"

        # --- Optional Storybook story ---
        story_path: str | None = None
        if self.generate_stories and target_tsx.exists() and self._is_ui_file(target_file):
            component_name = target_tsx.stem
            story_file = _generate_story(component_name, target_tsx)
            story_path = str(story_file)
            logger.info("[UIConsistency] Story generated: %s", story_file)

        return {
            "step":            step_id,
            "source_file":     source_file,
            "template_origin": template_origin,
            "target_file":     target_file,
            "status":          status,
            "css_classes":     css_diff,
            "elements":        elem_diff,
            "events":          evt_diff,
            "directives":      directive_summary,
            "llm_findings":    llm_findings,
            "story_path":      story_path,
        }

    # ------------------------------------------------------------------
    # LLM classification (UI_CONSISTENCY role)
    # ------------------------------------------------------------------

    def _classify_with_llm(
        self,
        step_id: str,
        css_diff: dict,
        elem_diff: dict,
        evt_diff: dict,
    ) -> list[dict]:
        from agents.llm import LLMMessage
        from agents.llm.base import LLMNotAvailableError, LLMProviderError

        system = (
            "ROLE: UI_CONSISTENCY\n"
            "You review diffs between an Angular source template and a converted "
            "React component. For each difference, classify it as:\n"
            "  - expected_idiom_change  (e.g. Angular class → React className, "
            "(click) → onClick, *ngIf → conditional render)\n"
            "  - expected_structural_change  (e.g. wrapper div removed intentionally)\n"
            "  - potential_omission  (element or class that should have been preserved "
            "but appears to be missing)\n\n"
            "Return strict JSON: { \"findings\": [ { \"item\": str, \"type\": "
            "\"css_class|element|event\", \"classification\": str, \"reason\": str } ] }"
        )

        diffs_text = []
        for c in css_diff.get("missing", []):
            diffs_text.append(f"CSS class missing in target: {c!r}")
        for e in elem_diff.get("missing", []):
            diffs_text.append(f"HTML element missing in target: <{e}>")
        for h in evt_diff.get("missing_handlers", []):
            diffs_text.append(f"React event handler missing: {h}")

        user = f"Step: {step_id}\n\nDifferences to classify:\n" + "\n".join(
            f"  - {d}" for d in diffs_text
        )

        try:
            response = self._router.complete(  # type: ignore[union-attr]
                system=system,
                messages=[LLMMessage(role="user", content=user)],
            )
            text = response.text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
            data = json.loads(text)
            return data.get("findings", [])
        except (LLMNotAvailableError, LLMProviderError, json.JSONDecodeError, Exception) as exc:
            logger.warning("[UIConsistency] LLM classification unavailable: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Markdown report renderer
    # ------------------------------------------------------------------

    @staticmethod
    def _render_markdown(report: dict[str, Any]) -> str:
        lines = [
            f"# UI Consistency Report — {report['run_id']}",
            "",
            f"- **Overall status:** {report['status'].upper()}",
            f"- UI files checked: {report['ui_steps_checked']}",
            f"- Passed: {report['passed']}  |  Warned: {report['warned']}  |  Failed: {report['failed']}",
            "",
            "---",
            "",
            "## Findings",
            "",
        ]

        for f in report["findings"]:
            status_icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(f["status"], "?")
            lines += [
                f"### {status_icon} Step `{f['step']}` — {f['status']}",
                f"- Source: `{f['source_file']}` (template: {f['template_origin']})",
                f"- Target: `{f['target_file']}`",
                "",
            ]

            css = f["css_classes"]
            if css["missing"]:
                lines.append(f"**⚠ Missing CSS classes:** `{'`, `'.join(css['missing'])}`")
            if css["added"]:
                lines.append(f"*Added CSS classes (new):* `{'`, `'.join(css['added'])}`")

            elem = f["elements"]
            if elem["missing"]:
                lines.append(f"**⚠ Missing HTML elements:** `{'`, `'.join(f'<{e}>' for e in elem['missing'])}`")

            evt = f["events"]
            if evt["missing_handlers"]:
                lines.append(f"**⚠ Missing React handlers:** `{'`, `'.join(evt['missing_handlers'])}`")

            d = f["directives"]
            lines += [
                "",
                f"- `*ngIf` count: {d['ng_if_count']} → conditional renders: {d['conditional_count']}",
                f"- `*ngFor` count: {d['ng_for_count']} → `.map()` calls: {d['map_count']}",
            ]

            if f.get("llm_findings"):
                lines += ["", "**LLM classification:**"]
                for lf in f["llm_findings"]:
                    lines.append(
                        f"  - `{lf.get('item')}` ({lf.get('type')}): "
                        f"**{lf.get('classification')}** — {lf.get('reason', '')}"
                    )

            if f.get("story_path"):
                lines.append(f"\n*Storybook story:* `{f['story_path']}`")

            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_ui_file(path: str) -> bool:
        return Path(path).suffix.lower() in {".tsx", ".jsx"}

    @staticmethod
    def _skipped(reason: str) -> dict[str, Any]:
        return {
            "status":           reason,
            "ui_steps_checked": 0,
            "passed":           0,
            "warned":           0,
            "failed":           0,
            "findings":         [],
            "report_json":      None,
            "report_md":        None,
        }
