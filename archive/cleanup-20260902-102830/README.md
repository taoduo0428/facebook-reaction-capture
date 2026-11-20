# Cleanup archive 20260902-102830

This directory contains recoverable project artifacts moved out of the active
Facebook crawler workspace on 2026-09-02. No account state, emulator data,
certificates, patched libraries, core source code, tests, or active logs were
removed.

## Active runs retained

- `runs/reaction-raw-network-probe-retry-20260901`
  - Latest successful end-to-end probe.
  - Contains TLS-level PCAP, decrypted mitmproxy archive, and parsed reactor CSV.
- `runs/reaction-ab-raw-on-20260901`
  - Raw HTTP archive A/B baseline.

## Archived run directories

The following directories were moved from `runs/` to `runs/` under this
archive:

- `reaction-raw-network-probe-20260901`
- `reaction-ab-raw-off-20260901`
- `reaction-force-search-small-20260901-recovered`
- `reaction-force-search-observe-20260901`
- `reaction-force-search-small-20260901-retry1`
- `reaction-force-search-small-20260901`
- `reaction-pfbid-list-20260901`
- `reaction-pfbid-target-20260901`
- `reaction-stage-list-20260901`
- `reaction-stage-target-20260901`
- `reaction-fallback-small-20260901`
- `reaction-current-fixed-20260901`
- `reaction-live-success-20260901`
- `reaction-current-small-20260901`
- `reaction-live-fixed-20260901`
- `reaction-live-small-20260901-1220`
- `raw-live-small-20260901-1206`
- `live-now-20260901-1055`
- `20260901-110906`
- `20260901-105641`
- `20260901-105401`
- `20260901-105321`
- `20260901-103512`
- `20260901-102508`
- `20260901-101823`
- `20260901-101717`
- `20260901-101411`
- `20260901-100742`
- `raw-e2e-20260901-092345`
- `raw-e2e-20260901-092313`
- `raw-e2e-20260901-092222`
- `20260901-014159`
- `20260901-013239`
- `20260901-012115`
- `20260901-011642`
- `candidate-nasa-1615133783315325`

## Other archived artifacts

- `run-root-files/`: loose screenshots and loopback PCAP self-test from `runs/`.
- `workspace-debug/`: one-off screenshots, UI XML dumps, debug logs, and
  temporary probe scripts from the project root.
- `analysis-current/`: superseded top-level analysis output.

## Restore

Move an archived run directory back into the project `runs/` directory. Move a
debug artifact back to the project root only if a historical investigation
specifically needs it. Names and contents were preserved.
