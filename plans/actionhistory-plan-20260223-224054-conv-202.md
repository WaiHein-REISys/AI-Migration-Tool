# Plan Document -- ActionHistory

**Generated:** 2026-02-23T22:40:54.597482+00:00
**Status:** PENDING APPROVAL
**Run ID:** conv-20260223-2240-1d65bd
**Feature Root:** `Y:\Solution\HRSA\HAB-GPRSSubmission\src\GPRSSubmission.Web\wwwroot\gprs_app\ActionHistory`

---

## 1. Current Architecture Breakdown

| Component | Type | Pattern | File |
|---|---|---|---|
| ActionHistoryComponent | Frontend | Angular 2 Component | `ActionHistory.component.ts` |
| ActionHistoryModule | Frontend | Angular 2 NgModule | `ActionHistory.module.ts` |
| ActionHistoryPageActionModel | Frontend | Angular 2 PageActionModel | `ActionHistory.PageActionModel.ts` |

---

## 2. Proposed Target Architecture

| Source Component | Target Component | Mapping ID | Rules Applied | Template |
|---|---|---|---|---|
| `ActionHistory.component.ts` | `ActionHistoryComponent.tsx` (React functional component) | MAP-001 | RULE-003 | `templates/ng-component-to-react.jinja2` |
| `ActionHistory.module.ts` | `ActionHistoryModule.tsx` (React functional component) | MAP-006 | RULE-003 | `templates/ng-module-to-nextjs-feature.jinja2` |
| `ActionHistory.PageActionModel.ts` | `ActionHistoryPageActionModel.tsx` (React functional component) | MAP-001 | RULE-003 | `templates/ng-component-to-react.jinja2` |

---

## 3. Step-by-Step Conversion Sequence

### Step C1: ActionHistory.component.ts -> `ActionHistoryComponent.tsx` (React functional component)
- **Mapping:** MAP-001 (Angular 2 NgModule @Component with RxJS Subject lifecycle)
- **Rules:** RULE-003 (no business logic reinterpretation), RULE-002 (preserve CSS class names)
- **Template:** `templates/ng-component-to-react.jinja2`
- **Notes:** Replace OnInit/OnDestroy with useEffect, Subject.takeUntil with AbortController or cleanup function, @Inject() with props/hooks

### Step C2: ActionHistory.module.ts -> `ActionHistoryModule.tsx` (React functional component)
- **Mapping:** MAP-006 (Angular 2 NgModule (*.module.ts with declarations/imports/providers))
- **Rules:** RULE-003 (no business logic reinterpretation), RULE-002 (preserve CSS class names)
- **Template:** `templates/ng-module-to-nextjs-feature.jinja2`
- **Notes:** NgModule declarations become component files in a feature folder, providers become service modules, imports become type imports

### Step C3: ActionHistory.PageActionModel.ts -> `ActionHistoryPageActionModel.tsx` (React functional component)
- **Mapping:** MAP-001 (Angular 2 NgModule @Component with RxJS Subject lifecycle)
- **Rules:** RULE-003 (no business logic reinterpretation), RULE-002 (preserve CSS class names)
- **Template:** `templates/ng-component-to-react.jinja2`
- **Notes:** Replace OnInit/OnDestroy with useEffect, Subject.takeUntil with AbortController or cleanup function, @Inject() with props/hooks


---

## 4. Business Logic Inventory

| Logic Item | Location | Preservation Method | Status |
|---|---|---|---|
| (none identified) | | | |

---

## 5. Risk Areas & Ambiguities

### [WARNING] -- RISK-001: RULE-004
**Message:** Cross-feature coupling detected: 'ActionHistory.component.ts' imports './../core/constants' which is outside the declared feature boundary.

**Recommendation:** Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.

### [WARNING] -- RISK-002: RULE-004
**Message:** Cross-feature coupling detected: 'ActionHistory.component.ts' imports './../core/services/context.service' which is outside the declared feature boundary.

**Recommendation:** Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.

### [WARNING] -- RISK-003: RULE-004
**Message:** Cross-feature coupling detected: 'ActionHistory.component.ts' imports './../core/services/configuration.service' which is outside the declared feature boundary.

**Recommendation:** Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.

### [WARNING] -- RISK-004: RULE-004
**Message:** Cross-feature coupling detected: 'ActionHistory.module.ts' imports './../core/services/context.service' which is outside the declared feature boundary.

**Recommendation:** Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.

### [WARNING] -- RISK-005: RULE-004
**Message:** Cross-feature coupling detected: 'ActionHistory.module.ts' imports './../core/services/configuration.service' which is outside the declared feature boundary.

**Recommendation:** Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.

### [WARNING] -- RISK-006: RULE-004
**Message:** Cross-feature coupling detected: 'ActionHistory.PageActionModel.ts' imports '../Core/PageActions' which is outside the declared feature boundary.

**Recommendation:** Determine whether this dependency should be (a) included in scope, (b) stubbed, or (c) treated as an external API contract. Resolve before Step C1.

### [BLOCKING] -- RISK-007: RULE-008
**Message:** External platform library detected: 'pfm-layout' (imported in ActionHistory.component.ts). No direct equivalent exists in the target stack.

**Recommendation:** Review 'pfm-layout' usage and decide: (a) find equivalent Next.js/Python package, (b) stub with a local implementation, or (c) exclude from scope. Cannot proceed until resolved.

### [BLOCKING] -- RISK-008: RULE-008
**Message:** External platform library detected: 'pfm-ng' (imported in ActionHistory.component.ts). No direct equivalent exists in the target stack.

**Recommendation:** Review 'pfm-ng' usage and decide: (a) find equivalent Next.js/Python package, (b) stub with a local implementation, or (c) exclude from scope. Cannot proceed until resolved.

### [BLOCKING] -- RISK-009: RULE-008
**Message:** External platform library detected: 'pfm-re' (imported in ActionHistory.module.ts). No direct equivalent exists in the target stack.

**Recommendation:** Review 'pfm-re' usage and decide: (a) find equivalent Next.js/Python package, (b) stub with a local implementation, or (c) exclude from scope. Cannot proceed until resolved.


---

## 6. Acceptance Criteria

- [ ] All CSS class names in `ActionHistory.component.ts` are preserved in converted JSX
- [ ] All CSS class names in `ActionHistory.module.ts` are preserved in converted JSX
- [ ] All CSS class names in `ActionHistory.PageActionModel.ts` are preserved in converted JSX
- [ ] All business logic methods produce identical outputs for identical inputs
- [ ] No imports from pfm-* or Platform.* libraries in converted code

---

**APPROVAL REQUIRED TO PROCEED TO EXECUTION**

Sign-off by: ___________________ Date: ___________
