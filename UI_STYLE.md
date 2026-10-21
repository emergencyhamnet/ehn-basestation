# Preferred EHN Web UI Style

Approved 2026-10-02. Use this standard for future EHN web interfaces and the eventual
MeshCore Pi-port admin UI update. The Operator Workbench and Main Directory Console
are the current reference implementations. The Pi-port UI has not yet been restyled.

## Shared Foundation

Use [ui/static/emergencyham-ui-style.css](ui/static/emergencyham-ui-style.css) and
the approved local [logo](ui/static/ehn-logo.png). These approved refinements supersede
the original style pack where they differ:

- Body/control font: `"Trebuchet MS", "Gill Sans", "Segoe UI", sans-serif`.
- White operational panels, dark navy headings, restrained violet/magenta primary actions.
- Green for healthy, orange for pending/degraded, red for failed/destructive, and blue-violet
  for neutral information. Always include a text label rather than relying on color alone.
- Full-width white header with a bottom border, no rounded outer panel, and centered inner
  content up to 1280 px wide.
- Two desktop header columns: aspect-correct logo up to 335 x 90 px on the left, title and
  concise platform-neutral description on the right. Stack on mobile with a 280 px logo
  constrained to available width. Avoid Windows-specific header copy.
- Workbench navigation sits below branding. Directory actions sit below its title.

## Approved Background

Apply the same viewport-fixed background to all consoles so document length does not
change the tint. Keep headers and working panels white for readability.

```css
body {
  font-family: "Trebuchet MS", "Gill Sans", "Segoe UI", sans-serif;
  color: var(--ehn-text);
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(221, 56, 212, 0.32), transparent 55%),
    radial-gradient(circle at top right, rgba(157, 56, 234, 0.3), transparent 60%),
    linear-gradient(180deg, #faf2ff 0%, #eee8f7 100%);
  background-attachment: fixed;
  background-repeat: no-repeat;
}
```

## Layout And Interaction

- Preserve each application's existing workflows and control IDs when applying the theme.
- Directory: full-width editor/endpoints and Authority Summary panels; Members and Audit in
  two desktop columns; Delivery Queue full-width. Stack all panels on mobile.
- Workbench: status rail and main workspace on desktop, one column on narrow screens.
- Use shrinkable grid tracks (`minmax(0, 1fr)`) to prevent intrinsic-width overflow.
- Wide tables and long log lines scroll inside their own containers, never the whole page.
  Log tails have a bounded height and strip ANSI terminal color codes.
- Recent Meshtastic activity defaults to unique text messages: five rows visible, up to 20
  scrollable entries. The first line shows the EHN identity label and identity type; the next
  line shows the display name and node address. Outbound rows identify the local sender and
  show the destination separately. Nodes without a directory match are labeled unresolved;
  radio-reported names are display names, not verified EHN identities. Directory peers and
  observed nodes share Identity Label, Identity Type, Display Name, and Address columns.
  Recipient choices use identity label, display name, then address. Low-level traffic stays behind
  the explicit All Activity filter; polling preserves the scroll position.
- Background refreshes must preserve drafts, focus, selection, and scroll position.

## Future Pi-Port Admin UI

Reuse this theme, logo, header, font, and background when its source checkout is available.
Preserve MeshCore-native settings, contacts, adverts, channels, map, and hardware controls.
Do not import basestation directory/routing workflows into the native UI or alter its
single-owner broker/session boundary. Avoid extra radio queries or full snapshots caused
by rendering. Keep local acceptance, RF ACK/NAK, and recipient reply distinct.

Verify logo loading/aspect ratio, keyboard focus, long text, table/log scrolling, and absence
of horizontal page overflow at desktop and 390 px mobile widths. Validate styling without
changing live records, radio settings, or transmitting RF test traffic.

The 2026-10-03 current-base snapshot supersedes the original pre-styling October 1 restore
point and includes the approved UI and subsequent naming updates. See [ALPHA_TESTING.md](ALPHA_TESTING.md).