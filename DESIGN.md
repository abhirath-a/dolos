---
name: "Dolos — Evidence Desk"
description: "A graph-led evidence workspace with readable provenance and inspectable model context."
colors:
  paper: "#f4f3ef"
  surface: "#fcfbf8"
  ink: "#292d35"
  muted: "#626772"
  line: "#dedfdc"
  blue: "#3658db"
  blue-soft: "#e8edff"
  vermilion: "#b84531"
  canvas: "#18232c"
  context: "#88adff"
  review-on-canvas: "#f07860"
  focus: "#809aff"
  button-hover: "#2646c0"
  secondary-button: "#e9e9e4"
  secondary-ink: "#3e4654"
  white: "white"
  review-background: "#f6e4d9"
  review-ink: "#944a30"
  pinned-surface: "#f0f1ee"
  canvas-control: "#22303b"
  canvas-control-line: "#3c4957"
typography:
  headline:
    fontFamily: "Manrope Variable, sans-serif"
    fontSize: "27px"
    fontWeight: 650
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Manrope Variable, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.7
  evidence:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "13px"
    lineHeight: 1.8
  label:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "12px"
    fontWeight: 550
    lineHeight: 1.3
  tag:
    fontFamily: "Inter Variable, sans-serif"
    fontSize: "10px"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  tag: "4px"
  icon: "5px"
  navigation: "6px"
  control: "7px"
  panel: "8px"
  upload: "12px"
spacing:
  compact: "8px"
  row: "12px"
  control: "16px"
  section: "24px"
  columns: "40px"
components:
  button-primary:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.white}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "11px 16px"
  button-primary-hover:
    backgroundColor: "{colors.button-hover}"
  button-secondary:
    backgroundColor: "{colors.secondary-button}"
    textColor: "{colors.secondary-ink}"
    rounded: "{rounded.control}"
    padding: "11px 16px"
  button-secondary-hover:
    backgroundColor: "{colors.line}"
  review-tag:
    backgroundColor: "{colors.review-background}"
    textColor: "{colors.review-ink}"
    typography: "{typography.tag}"
    rounded: "{rounded.tag}"
    padding: "5px 7px"
  pinned-prediction:
    backgroundColor: "{colors.pinned-surface}"
    rounded: "{rounded.panel}"
    padding: "17px"
  graph-search:
    backgroundColor: "{colors.canvas-control}"
    rounded: "{rounded.navigation}"
    padding: "9px"
  scope-input:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.tag}"
    padding: "8px"
  investigation-navigation:
    textColor: "#3d4f6b"
    height: "40px"
---

# Design System: Dolos — Evidence Desk

## Overview

**Creative North Star: "Evidence Desk"**

Evidence Desk places the actual transaction network on charcoal and surrounds it with warm paper navigation, controls, ledger, and explanations. The interface is compact enough to inspect connected evidence while keeping the graph and a readable explanation visible together.

Manrope gives headings a clear hierarchy; Inter carries dense evidence and data labels. Blue identifies model and selection context, while vermilion marks relationships requiring review. Findings remain review candidates, and sampled neighbors remain context rather than causal attribution.

**Key Characteristics:**

- An actual graph as the principal work surface.
- Warm paper evidence surfaces and restrained borders.
- Blue context and vermilion review signals with text and shape cues.
- A pinned prediction while neighboring records are inspected.

This document records the implemented source in `frontend/src/style.css` and the App, Network, Inspector, Ledger, and UploadPanel components. It follows the approved Evidence Desk brief. Browser hosting was unavailable during this pass: these are source-extracted implementation facts, not rendered visual approval. Functional checks do not establish visual quality. No raster imagery is used; the graph, CSS mark, and inline icons carry the visual content.

## Colors

The palette combines warm paper with a charcoal graph canvas and two distinct analytical accents. The frontmatter owns exact reusable values; one-off source colors remain in component CSS. Sidecar tonal ramps are synthesized OKLCH preview strips, not implemented tokens or approved additions to the palette.

### Primary

- **Model Blue** (`blue`): primary actions, links, selected tabs, and model controls.
- **Soft Model Blue** (`blue-soft`): the reusable light context palette token; implemented context actions also have their own local blue tints.
- **Historical Context Blue** (`context`): dashed sampled-context edges against charcoal.

### Secondary

- **Review Vermilion** (`vermilion`): paper-side review palette token. Review tags use their extracted background and ink pair.
- **Canvas Review Coral** (`review-on-canvas`): review edges and node markers on the dark canvas, preserving visibility there.

### Neutral

- **Warm Paper** (`paper`): workspace background.
- **Evidence Surface** (`surface`): toolbar and inspector background.
- **Charcoal Canvas** (`canvas`): actual graph surface, not a decorative illustration.
- **Ink**, **Muted Ink**, and **Divider** (`ink`, `muted`, `line`): primary copy, secondary palette, and structural boundaries. Later component overrides deliberately strengthen some secondary text.
- **Pinned Surface** (`pinned-surface`): the originating prediction inset within the inspector.
- **Canvas Control** and **Canvas Control Line**: dark search and view-switch surfaces.

**The Context and Review Rule.** Keep blue model context distinct from vermilion review signals; reinforce both with labels and geometry.

## Typography

**Heading Font:** Manrope Variable, sans-serif.
**Body and Data Font:** Inter Variable, sans-serif.

The installed variable fonts are imported locally through Fontsource. Inter is intentional for dense evidence and labels; generic overused-font detector warnings do not override that implementation choice. No separate mono face is established.

### Hierarchy

- **Headline:** workspace title, with the extracted headline role. It adjusts to (32px) above the wide breakpoint and (25px) on mobile; narrower tablet rules use (23px).
- **Title:** inspector heading, using the title role. The initial analysis prompt uses (25px), weight (600), line-height (1.3).
- **Body:** root text uses the body role; paragraph line-height is (1.7). Evidence explanations commonly use the evidence role.
- **Label:** buttons use the label role. Toolbars and table data also use local (10–12px) sizes; these are density choices, not a universal minimum.
- **Tag:** compact review and processing states use the tag role.
- **Numeric readout:** raw margin uses Manrope, weight (650), size (31px), tracking (-0.035em), and tabular numerals; mobile size is (34px). Ledger amounts also use tabular numerals.

**The Evidence Type Rule.** Use Manrope for the heading hierarchy and Inter for dense evidence and data labels.

## Layout

A (72px) desktop header holds the brand, one grouped native investigation selector, contextual help/back navigation, and one secondary upload entry. The graph workspace uses the full available width: there is no persistent case sidebar. A heading and analysis toolbar precede a (590px) desk with a flexible graph and (350px) inspector. The paginated ledger remains below.

The inspector can expand to `min(520px, 50%)`, or (550px) on wide screens. At (1650px), the default inspector is (400px), with a desk minimum of (650px). At (1250px), (1050px), and narrow-tablet widths, the inspector contracts to (320px), (300px), and (280px).

At maximum width (760px), the header becomes two rows with a (112px) minimum height, growing to fit its controls. The investigation selector occupies the second row; it remains available on mobile. The graph is (450px) high and the inspector stacks below it at full width. The ledger scrolls horizontally and keeps pagination. Native case/scope inputs use (16px) mobile text.

Uploads use one centered task column, at most (900px) wide. File selection, progress, and actions come first; provider handling and schema help follow as native disclosures. Selected files survive navigation away and back. Details and caveats remain available without a parallel tutorial column.

## Elevation & Depth

Depth comes primarily from the contrast of charcoal, warm paper, inset tones, and one-pixel dividers. The graph and inspector meet directly; they are not floating cards. Graph controls explicitly remove the library shadow. Positioned graph controls and search results use z-index layering.

### Shadow Vocabulary

- **Filter popover:** `0 12px 30px #18232c1a`, used on the scope-filter form over the workspace.

**The Flat Desk Rule.** Separate resting work surfaces with tone and borders; reserve the observed shadow for the filter popover.

## Shapes

Controls are gently rounded rather than pill-shaped: tags and fields use the tag radius, navigation and graph controls use the navigation radius, buttons use the control radius, and inset prediction panels use the panel radius. The upload target is more open with the upload radius and a dashed border. Entity discs and small state markers are circular; graph entities pair a (52px) disc with a wrapping name. The CSS brand mark has its own asymmetric curved silhouette. Structural work surfaces retain straight adjoining edges.

## Components

### Buttons

Compact, direct actions. Primary buttons use Model Blue and white; secondary buttons use a neutral fill. Default padding and radius are in frontmatter; default minimum height is (40px). Small buttons use (36px) with (9px 13px) padding. Primary and secondary hover fills are extracted variants. Text buttons underline on hover; icon buttons gain a quiet fill. Disabled controls reduce opacity to (0.48) and use the not-allowed cursor. Enabled buttons move down (1px) on press. Focus-visible controls receive a (3px) blue outline with a (3px) offset.

### Chips / State Tags

Small rectangular state labels pair text with color. Review tags use the review palette and an alert icon where shown in the ledger. Neutral and processing tags have distinct local tones. Synthetic data is explicitly labeled. Tags themselves are not interactive filters.

### Cards / Containers

Warm evidence surfaces are structural panels with dividers. The pinned prediction is an inset panel, with its source route, raw margin, and interpretation retained while a sampled neighbor is inspected. The filter form is a floating exception with its observed shadow. Do not extrapolate a general card grid from these insets.

### Inputs / Fields

Graph search is a dark bordered field with a transparent inner input, light text, and a search icon. Its outer radius and padding are recorded in frontmatter. The scope field is white with a (1px) `#cbd5e4` border and the tag radius. Global focus-visible styles cover inputs and selects; graph search locally removes the inner input outline. Native method selection is visually integrated into the paper toolbar. No additional validation styling is inferred beyond the implemented alert banners.

### Navigation

The grouped native investigation selector contains the model demonstration, historical cases, and saved investigations. It replaces the former persistent rail while preserving keyboard access to every case. One upload entry preserves selected files when reopened; starting another investigation is explicit inside the upload flow.

The inspector keeps Analysis, Evidence, and Entities tabs, with ARIA tab semantics and arrow/Home/End keyboard handling. Model-context isolation is offered beside the explanation, with a clear full-network exit on the canvas. Avoid adding another persistent navigation system for the same choices.

### Graph and Pinned Context

Entity discs distinguish person, account, and organization with inline icons. Directional edges carry arrowheads; a selected or highlighted edge reveals a transfer label. Review edges use coral; sampled-context edges use dashed blue. Focus adds a blue outline to the entity; unrelated entities can mute to (0.25) opacity. The legend explains line treatments. Keep the originating prediction pinned when a sampled neighbor is selected, and retain the ledger as a keyboard-accessible route to records.

### Ledger

Ten relationships per page, with route buttons, amount or relationship, date, review status, and an explicit inspect button. Hover and selected rows share a quiet blue-gray fill. Empty results show a clear-filters action. Disabled previous/next controls preserve page boundaries. Preview counts disclose when the graph is bounded; “not analyzed,” “context · not scored,” and “not flagged by this method” remain distinct.

### Upload and Feedback

The dashed upload target changes background and border on hover or drag and dims while disabled. Overall transfer progress is a native progress element; document rows retain individual processing state and rejected-row/error actions. Controls offer cancel remaining uploads, resume, and open investigation where applicable. The upload page keeps detailed data-handling and file-format guidance in native disclosures. Accepted work remains visible. Loading, completion, processing, error, cancellation, model-unavailable, and incompatible-input copy follows actual application state, using status/alert semantics where implemented.

Motion confirms state changes: analysis results enter with opacity and a 6px translation over 220ms using the strong ease-out token; edge and entity opacity changes take 160ms; press feedback takes 120ms. Financial geometry stays still while read. Reduced motion removes the entrance and movement. Viewport focus retains its existing 200ms transition. Do not add theatrical analysis timing.

## Do's and Don'ts

### Do:

- Do keep directional transfers and source records inspectable.
- Do preserve the pinned prediction when exploring sampled neighbors.
- Do pair review color with labels, icons, or line treatment.
- Do use Inter for dense evidence, table rows, and control labels.
- Do expose bounded preview, analysis, and export scope.
- Do reflect actual request and processing state.

### Don't:

- Don’t present context membership as causal attribution or connected entities as proof of wrongdoing.
- Don’t label raw model margins as confidence percentages.
- Don’t substitute decorative networks, simulated terminals, or theatrical delays for actual behavior.
- Don’t turn the warm evidence panels into floating shadow-heavy cards.
- Don’t treat source extraction or functional tests as rendered visual approval.
