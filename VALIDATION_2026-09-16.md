# Validation record — 2026-09-16

This record separates current local checks from the real Android capture evidence. It does not claim real macOS or M4 acceptance.

## Earlier local baseline (before final closeout)

- `test_facebook_cancel_transport.py`: 7 tests passed. Coverage includes process stop proof before transport cleanup, concurrent cleanup coalescing, external proxy preservation, cleanup failure lease blocking, normal completion lease release, and non-Windows behavior.
- `test_ui_v2_and_safety.WebV2IntegrationTests test_ui_v2_and_safety.WebV2BatchSelectionTests test_ui_v2_and_safety.WindowsOneClickRuntimeContractTests`: 18 tests passed. The historical source-hash class is intentionally excluded because its baseline describes an older snapshot.
- Earlier stabilization regressions: 302 API/orchestration/navigation/health/client/file-sharing tests passed; 93 environment/platform/batch tests passed with 2 platform skips. These are historical snapshots within this day's work, not final-source acceptance counts.
- `evidence/real-tests-20260916/inspect_batch.py batch_b6fb03dc8bc142ce --require-complete` passed against the local API. It verified three terminal, target-matched jobs and 683 aggregate CSV rows (27 + 231 + 425), with one source post ID per job and no rate-limit/auth records.

## Real Android evidence retained locally

The earlier complete retry batch `batch_b6fb03dc8bc142ce` covers a group permalink, a `share/p` URL, and a normal `pfbid` post. A low-volume Reel also completed after standby recovery. These runs are retained as historical evidence; later code must pass fresh acceptance. The captured artifacts and manifests remain under `runs/` and are excluded from source delivery.

## Required before publication

Run `evidence/real-tests-20260916/lifecycle_acceptance.py` only while the API health endpoint reports an idle worker, zero queued/cancelling jobs, and an available capture lease. Acceptance requires `cancelling -> cancelled`, stopped process identity, `transport_cleanup_state=restored`, the configured normal proxy read back on Android, a released lease, and a subsequent complete low-volume job. The first accepted run is recorded below; changes to cancellation or target verification require renewed acceptance.

After any final source change, rerun the affected local suites, parser checks, and `git diff --check` before creating a release archive. Publish only the explicit `release-files.txt` set; never include local credentials, APKs, certificates, cookies, runs, or emulator disks.

## Stabilization closeout observations — 2026-09-16

- A fresh API restart used the project interpreter and preserved the existing emulator PID. `test_facebook_cancel_transport` plus `test_facebook_capture_api` passed 143 tests after the cancellation changes.
- `evidence/real-tests-20260916/lifecycle-20260916-130029.json` records a real `running -> cancelling -> cancelled` transition. The owned PowerShell/mitmdump/python process tree stopped, port 8081 was released, the configured Android proxy was restored, the emulator identity was preserved, and a same-worker follow-up completed with one attempt and no failover (2 CSV rows, terminal scope).
- `evidence/real-tests-20260916/failure-recovery-20260916-133410.json` records a strict URL/post-id mismatch rejected with `Target post could not be confirmed after 3 strict attempts; capture aborted.`, followed by a successful same-worker Reel task (49.88 s, 2 CSV rows, one attempt, no failover). The worker and lease returned to idle/available.
- The final five URL batch `batch_b27bda2739cc43ff` completed four cases: ordinary numeric post (234 rows), share/p (234 rows), group permalink (27 rows), and Reel (2 rows). Each had a matched target, terminal page, complete integrity, one source post ID, and one attempt on `worker-01`. The pfbid URL failed with `reaction capture is incomplete: query-not-triggered`; this is retained as a truthful partial result. The same pfbid URL has earlier terminal successful evidence in `batch_b6fb03dc8bc142ce` and `batch_34f91feb15b646e1`, so pfbid support is historically verified but not a current five-for-five claim.
- The batch inspector now checks per-job CSV user uniqueness and preserves `(job_id, user_id)` pairs across posts. Its `--require-complete` gate correctly rejected the final batch because of the pfbid failure.

## Candidate source archive snapshot

- `evidence/real-tests-20260916/release-verification-20260916-140751.json` accepted the 119-file candidate archive `facebook-source-20260916-140145.zip`. A fresh extraction ran 524 tests successfully (2 skips), both JavaScript syntax checks, all manifest PowerShell parsers and `pip check`. Archive members and SHA-256 entries matched that source snapshot.
- This candidate predates later document and review fixes. It is not the final release artifact; export and verify a new archive after source and documentation are final.
- A source whitelist audit found no excluded runtime paths or Git-ignored entries. The private-key signature in `test_facebook_mac_device.py` is a deliberately incomplete marker used to test rejection, not key material.

## Final source gate — 2026-09-16 20:06

- `evidence/real-tests-20260916/release-verification-20260916-200637.json` accepted `dist/facebook-source-20260916-200606.zip`. The archive contains exactly 119 whitelist files; extraction verified exact source bytes, manifest hashes and ZIP integrity.
- In the fresh extraction, the workflow-equivalent CI list ran 548 tests with 2 intentional skips and `OK`; both Node syntax checks, all release PowerShell parsers and `pip check` returned exit code 0. The workspace run independently produced the same 548-test total, `pip check`, `git diff --check`, and document/link audit all passed.
- `evidence/real-tests-20260916/source-manifest-audit-20260916-200910.json` accepted the exact whitelist with no forbidden paths, ignored entries or real secret signatures. The only private-key marker is the existing nonsecret rejection fixture in `test_facebook_mac_device.py`.
- This gate validates the current source archive and local Windows contracts. It does not provide real Apple Silicon hardware evidence, and it does not convert the partial five-URL live batch into a complete five-for-five acceptance.

## Later closeout evidence

- `evidence/real-tests-20260916/final-batch_b30573fa4f764e33-acceptance.json` records the original pfbid URL completing on `worker-01`: 425 unique CSV users, 18 successful pages, terminal scope, a matched target, 144.97 seconds, one attempt and no failover. Facebook reported 426 interactions, so the result remains `complete-terminal-visible-subset`. This run entered the detail view directly (`inline_feed_promoted=false`); the localized Feed timestamp branch is covered by executable fixtures, not claimed as exercised by this real run.
- `evidence/real-tests-20260916/release-verification-20260916-155522.json` accepted a later 119-file candidate archive. Its fresh extraction ran 543 tests (`OK`, 2 skips), both JavaScript syntax checks, manifest PowerShell parsers and `pip check`; archive members, source bytes and hashes matched. Subsequent review findings require additional repairs and a new final archive.
- `evidence/real-tests-20260916/lifecycle-20260916-155649.json` is a retained failed acceptance (`accepted=false`). Cancellation itself completed in 12.49 seconds with the owned process tree stopped, port released, proxy restored, emulator preserved and worker/lease idle/available. The immediate same-worker Reel follow-up collected two terminal users but failed final target verification (`not-matched`). This does not close the cancellation-and-reuse requirement.

## Fresh live evidence — 2026-09-16 evening

- `evidence/real-tests-20260916/failure-recovery-20260916-193055.json` confirms the deliberately mismatched URL/post-id job was rejected after three strict target attempts. The process was stopped, the normal proxy read back as `127.0.0.1:7897`, the capture lease became available, and the same worker remained reusable. The follow-up Reel was then rejected because all three attempts had `posts=0` and no matching Reel token; this is an external target-visibility failure, not a cleanup failure. An earlier run at 19:13 also exposed a stale proxy baseline in the acceptance observer; the second run passed the recovery assertions and reached the target-visibility assertion.
- `evidence/real-tests-20260916/final-batch_d68121ac9c464f00-acceptance.json` is the fresh sequential five-URL batch. The ordinary numeric post and `share/p` both completed on `worker-01` in one attempt, terminal scope, target matched, 238 unique CSV users and 11 terminal pages each. The pfbid URL preserved 32 users and 2 pages before truthful `query-not-triggered` failure; the group permalink matched its target but produced no fresh source post ID; the Reel had no matching target token after three strict attempts. All three failed jobs retained partial evidence, stopped processes and `transport_cleanup_state=restored`.
- The same batch proves the aggregation boundary: 476 HTTP CSV rows, 476 unique `(job_id, user_id)` pairs and 238 users preserved across the two successful jobs. No cross-job user collapse occurred. The batch inspector correctly rejected the batch as incomplete rather than converting partial jobs to success. The successful Reel artifacts in `recheck-reel-current` and `batch_68394d255f5c4924` remain valid historical evidence, but they do not turn this fresh five-URL run into a five-for-five claim.

## Open closeout items

- Localized Feed-card timestamp recognition is repaired and the original pfbid has historical terminal evidence. The fresh sequential five-type batch was executed, but only 2/5 URLs completed in the current Facebook session; pfbid, group permalink and Reel remain current external-visibility/entry failures with partial evidence retained.
- Process-stop error handling, malformed/conflicting cleanup results, incomplete macOS cleanup evidence and ambiguous target-ID/author proof have regression fixtures and focused repairs. Fresh failure recovery proves strict rejection and transport cleanup, but its same-worker follow-up could not complete because the Reel was not visible. Cancellation-and-reuse therefore remains open for the final source snapshot.
- Re-run final-source checks, update this record with new live results, refresh the source archive and publish the reviewed stabilization branch. No main-branch merge or real-Mac acceptance is claimed here.
