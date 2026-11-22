# September 16 continuation: repair and real-test evidence

This record extends the earlier audit without replacing its historical claims.
The API was restarted for the first two batches; the emulator and logged-in
Facebook process were preserved. Neither batch is full acceptance: both requests
explicitly set `reaction_scroll_limit: 1`, which limits gestures, not response pages.

## Pre-repair real observations

| Batch | Case | Result |
| --- | --- | --- |
| `batch_27fb398cb56f4aae` | share/p | 65.09 s, 50 identities, 3 pages, matched target, partial open tail |
| same | Jaimanitas group permalink | 47.10 s, failed source discovery; normalized header count 210 found but not activated |
| same | Reel 1621449149592338 | 69.84 s, 20 identities, 1 page, partial open tail; reported 14K so excluded from full low-volume acceptance |
| `batch_e256dccf3d1040f3` | krqenews pfbid | 50 identities, 3 pages, bounded sample |
| same | group permalink 1420101493637310 | failed source discovery; primary normalized header count 26; fallback attempt on worker-02 also failed |

HTTP CSV downloads were parsed for the first batch's successful jobs: 50 and 20
rows respectively, with exactly one source per file. These numbers establish
download consistency, not full pagination.

## Confirmed defects and scope

- The reaction activation call ran before comments normalization. A candidate
  discovered afterward never entered the activation/readiness branch.
- Tap helpers discarded the ADB command outcome and reported activation success.
- A partial count gap received a terminal-list/platform-difference explanation.
- Batch current-stage selection omitted Android data and chose the first active
  stage, leaving it at environment even after reaction data appeared.
- Optional proxy lookup failure in `adb_snapshot` could erase already confirmed
  device/boot health. The earlier real failover has no saved health snapshots, so
  it cannot be attributed conclusively to this defect.

Repairs preserve source matching, first-fresh-query discovery, pagination terminal
checks, rate-limit cooldown, account state, and prior run evidence.

`full-request.json` is the prepared full test request; omission of the scroll limit
is intentional. `inspect_batch.py BATCH_ID` reads the local HTTP API and downloaded
CSV into memory and prints only acceptance metrics, without reactor identities.
Preparation of this request does not mean it has been submitted or passed.

GitHub publication remains gated on corrected code and real full-flow acceptance.

## Corrected-code checks and full test dispatch

- Combined API/orchestrator/navigation/transport-health/shutdown/client suite:
  295 tests passed. An intermediate new composer fixture failed because its
  PowerShell harness produced no JSON; the harness was repaired before this pass.
- PowerShell parser checks passed for explorer, transport, and shutdown scripts;
  both frontend JavaScript syntax checks passed.
- All three implementation agents passed the persisted Luna Max guard.
- Verified idle API listener PID 29184 was stopped alone and the existing API
  launcher restarted hidden. Health returned worker-01 idle, no queued/cancelling
  jobs, available lease, both configured devices booted. No emulator was stopped.
- Full request submitted as `batch_68394d255f5c4924`, four posts, without an explicit
  scroll limit. Acceptance is still pending; actual outcome must be appended.

## Full batch 68394 outcome

This batch ended `completed_with_errors`, not full system acceptance.

| Post | Job | Seconds | Outcome |
| --- | --- | --- | --- |
| group 1420101493637310 | fb_410da87f8b1c4f54 | 70.06 | failed; simplified Chinese comment regex typo caused geometry fallback to choose Share, then backed out without submitting |
| Jaimanitas group | fb_05801ad070ce4559 | 112.73 | complete, matched, 211 users / 10 pages / terminal, CSV 211 rows, source 28624279553875599 |
| share/p target | fb_4d40d751b343448c | 46.49 | failed before explorer; File.ReadLines shared-read mode conflicted with concurrent JSONL append writer |
| Reel 1054985397317048 | fb_82bff85c58de487b | 73.04 | complete, matched, 2 users / 1 terminal page, CSV 2 rows, source 122122050735339946 |

Both complete cases had zero rate-limit and feature-block records. All four jobs
used worker-01, one attempt each, zero failovers, with Facebook PID 1839 reused.
After the batch, health was idle with no queued/cancelling jobs and available lease.
The two new defects are being fixed before retry; failed jobs remain failed.

## Retry batch b6fb outcome

After correcting the Chinese selector and the Windows shared-file reader, combined
related regression coverage passed 302 tests. The broader environment, platform,
pagination and batch suites ran 93 tests with two platform skips and no failures.

`batch_b6fb03dc8bc142ce` completed all three full jobs:

| Job | Seconds | Users / pages | Complete result |
| --- | --- | --- | --- |
| fb_b359d26e0e154e52 | 171.66 | 27 / 2 | group matched, terminal, HTTP CSV 27 |
| fb_516f30e76d7f4b87 | 136.48 | 231 / 11 | share target matched, terminal visible subset of reported 232, HTTP CSV 231 |
| fb_b1a3028638334d8e | 153.96 | 425 / 18 | pfbid target matched, terminal visible subset of reported 426, HTTP CSV 425 |

All three had zero rate limits/feature blocks and zero worker failovers. The group
required three strict target-open attempts, then used existing exact-source deferred
validation; this explains its longer duration. No source guard was relaxed.
Facebook PID 1839 was reused for the first two jobs; the third found an already
running PID 30122 and attached hooks. The cause of that intervening PID change is
not established, so this batch does not prove identical PID across all three jobs.

## Controlled cancellation check

`fb_c383b38553634faa` was submitted as a small Reel job and cancelled while running.
Observed `cancelling` with stopped process, then `cancelled`, idle queue and released
lease. However, Android still used capture proxy `127.0.0.1:8081`: hard termination
skipped the PowerShell finally/normal proxy restore. This is an outstanding defect,
not accepted cancellation recovery. Follow-up job `fb_3ab2b51f0f5c4322` tests the next
job path. Publication remains blocked until cancellation transport cleanup is fixed.

Follow-up `fb_3ab2b51f0f5c4322` ultimately completed on worker-02, 2 visible users,
terminal, matched source, zero rate limits. Attempt 1 encountered Android connectivity
service/ADB failure; the existing recovery restarted ADB and switched to standby.
Both emulator host processes remained present and both proxies later read normal.
This proves observed standby recovery but is not a clean cancellation acceptance.

The full retry batch HTTP aggregate CSV contained 683 rows, equal to 27+231+425;
all three job IDs remained separate post boundaries.
