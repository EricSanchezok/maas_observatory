---
name: MaaS Observatory
description: Clear response measurements for academy-hosted models.
colors:
  background: "#0a0d10"
  surface: "#0f1317"
  surface-raised: "#12171b"
  border: "#283038"
  text: "#edf2f4"
  text-secondary: "#bbc4cb"
  muted: "#77828c"
  signal: "#9be7d8"
  signal-muted: "#5f9e93"
  warning: "#e7b978"
  critical: "#f07872"
typography:
  display:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "72px"
    fontWeight: 500
    lineHeight: 0.96
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "48px"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.03em"
  body:
    fontFamily: "Geist, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  metadata:
    fontFamily: "DM Mono, ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  control: "8px"
  panel: "10px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "20px"
  lg: "32px"
  section: "80px"
components:
  status-current:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.signal}"
    typography: "{typography.metadata}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
  metric-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "20px"
---

# Design System: MaaS Observatory

## Overview

**Creative North Star: "The Clear Signal"**

MaaS Observatory is a dark, restrained measurement product. Its interface gives response data room to breathe, uses one mint signal color sparingly, and keeps methodology available without making every card read like documentation.

The system rejects debug-console density, decorative technology motifs, generic AI gradients, explanatory slogans, and numbered landing-page scaffolding.

**Key Characteristics:**

- One aligned content column with deliberate vertical rhythm
- Flat, bordered data surfaces with minimal decoration
- Familiar controls and direct, user-facing labels
- Technical metadata disclosed progressively

## Colors

Near-black surfaces create a quiet field; pale mint identifies current, measured information.

- **Deep Field** (`#0a0d10`): page background.
- **Instrument Surface** (`#0f1317`): primary panels.
- **Raised Surface** (`#12171b`): controls and selected states.
- **Clear Signal** (`#9be7d8`): active state, focus, and primary chart line.
- **Measured Amber** (`#e7b978`): delayed or rapid-collection state.
- **Critical Coral** (`#f07872`): unavailable state and request failure.
- **Primary Ink** (`#edf2f4`): headings and values.
- **Secondary Ink** (`#bbc4cb`): supporting labels.

**The Signal Rule.** Mint identifies current data or interaction state; it is not decorative.

## Typography

**Display Font:** Geist (system sans fallback)

**Body Font:** Geist (system sans fallback)

**Label/Mono Font:** DM Mono

Geist keeps the product clear and contemporary. DM Mono is reserved for timestamps, units, fixture identifiers, and compact measurement metadata.

- **Display** (500, 72px, 0.96): masthead only.
- **Headline** (500, 48px, 1): major page sections.
- **Title** (550, 20–28px, 1.2): model and panel headings.
- **Body** (400, 15px, 1.5): descriptions, capped at 70ch.
- **Label** (400, 11px, 1.4): timestamps and units; uppercase only where space is constrained.

**The Plain-Language Rule.** Primary labels describe what users experience; protocol abbreviations belong in measurement details.

## Elevation

The interface is flat by default. Depth comes from tonal surface changes and dividers, not ambient shadows or glass effects. Popovers may use one compact shadow only when separation from underlying data is necessary.

**The Flat-by-Default Rule.** Static panels do not float.

## Components

### Buttons

- **Shape:** compact rounded rectangle or circular icon control (8px or full circle).
- **Primary:** mint surface with deep text, used only for a real action.
- **Hover / Focus:** tonal change plus a 2px visible mint focus outline.
- **Ghost:** transparent with a clear border and text contrast.

### Chips

- **Style:** compact bordered status label with text plus icon or explicit word.
- **State:** color supports the written state and never replaces it.

### Cards / Containers

- **Corner Style:** 8–10px.
- **Background:** Deep Field or Instrument Surface.
- **Shadow Strategy:** none at rest.
- **Border:** one quiet divider color.
- **Internal Padding:** 20–24px.

### Inputs / Fields

- **Style:** raised surface, 8px radius, visible label.
- **Focus:** 2px mint outline with offset.
- **Error / Disabled:** explicit text state, not opacity alone.

### Navigation

Use a conventional sticky top bar. Links are concise, sentence case, and use clear hover and focus states. Mobile navigation wraps or collapses without horizontal scrolling.

### Response Metric

Pair a plain-language label, tabular number, unit, recent sample time, and an optional trend. Live cards show only the latest completed request; window summaries show the arithmetic mean across the fixed six-fixture suite. Method definitions live in a shared disclosure rather than repeated card prose.

## Do's and Don'ts

### Do:

- **Do** align headings, controls, cards, and charts to the same content boundary.
- **Do** use `#9be7d8` for current measurements, selection, and focus.
- **Do** preserve null values and state why a measurement is unavailable.
- **Do** use tabular numbers and DM Mono for units and timestamps.
- **Do** support keyboard use, reduced motion, and WCAG 2.1 AA contrast.

### Don't:

- **Don't** build a debug panel or dense infrastructure console.
- **Don't** use neon science-fiction styling, generic AI gradients, or glassmorphism.
- **Don't** use oversized numbered sections or explanatory slogans.
- **Don't** expose protocol terminology such as TTFT or E2E as the primary label.
- **Don't** invent model-wide server metrics from an unknown source boundary.
- **Don't** add nested scrolling, decorative scrollbars, or overlapping chart labels.
