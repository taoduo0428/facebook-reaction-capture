# 当前验收总表（2026-10-03）

Windows 本机已有可运行交付，但整体目标尚未全部完成。下面区分代码实现、运行验证和未取得的证据；历史记录保留在后面，不应把早期统计当作最新结果。

| 范围 | 已有证据 | 边界与剩余工作 |
|---|---|---|
| 当前代码回归 | batchmeta-final-01：765 项运行，763 项通过、2 项跳过；退出码 0；原始输出保留 | Windows/Python 3.13.12，不等同远程 CI/Python 3.12 或 Mac 实机 |
| Reaction 真实样本 | 数字帖、share/p、pfbid 各950人；群组308人；Reel2人；均有目标绑定及终页证据 | 只覆盖这些样本；不是所有URL或所有账号可见范围证明 |
| Shares | fb_ad138ecdb11c4fd1：16个当前账号可见分享者，2页终止；报告46、差额30保留 | 不代表取得全部46；其余 Reaction-only任务没有请求 Shares |
| 取消恢复 | fb_4a1224c9bd984084：取消、已登记进程退出、代理恢复、lease释放；结果接口409 | 不宣称所有物理故障、未知子孙进程或断电耐久性 |
| 可靠性修复 | 持久化后再发布、阻塞原因保留、显式确认、启动失败回滚、脱敏503、scheduler存活诊断 | 独立执行者复核未完成；隔离故障证据与实机证据分别保留 |
| 文件交付 | job/batch下载与元数据共用物理路径约束；已有950行CSV经API/UI下载哈希一致 | 不宣称硬链接或恶意本机写入者TOCTOU防护 |
| 已测量优化 | 同主机6组交替对照，health方法475.905→325.145ms，ADB调用5→3，返回内容一致 | 约31.7%仅适用于该健康方法；不是Facebook采集提速 |
| 资源基线 | fb_80a78d0601db4a8a：112.593秒、40次采样；CPU/工作集/I/O已记录 | 单次采样、短命进程可能遗漏、共享页重复计数；不是失败率/全系统资源基准 |
| 平台与发布边界 | 94条平台线索已源码分类；白名单/本地可达历史签名候选已由主执行者分类 | 独立安全分类、原生Mac及hostedCI未完成；没有批准发布 |

最新批次元数据修复已在2026-10-03 22:16通过仅重载空闲API应用；后续读回原Reaction950、Share16、取消结果409及CSV哈希检查正常。该状态是当时快照，不是持续运行保证。日志/配置备份见 verification.md。

## 查收与后续边界

- 使用最新本地源码快照时先解压到新目录，不覆盖当前本机配置或运行数据。每个ZIP内含SHA256SUMS；旧包保留为旧快照。
- 主执行者回归与二次检查已做；T016/T057/T080所需独立复核仍未取得，不能改名冒充。
- T012/T017/T073/T081中需要原生Mac/远程CI的部分明确未验收。
- T013–T015宽范围性能要求仍有缺口。现有结果支持health局部改善及资源基线，不支持采集端到端提速。
- 不再为了未勾选数量而新增功能或重复测试；需新的具体缺陷或明确未覆盖的验收条件才继续变更。T019不得据此标为完成。

## Preserved historical audit (superseded where contradicted by current evidence)

# Completion audit — original system-wide objective remains active

Audit date: 2026-10-02. Scope is the user's full objective: use the engineering
framework to analyze, repair and safely optimize the existing Facebook system.
This document is not a replacement/narrowing of that objective or a success claim.

## Requirements and evidence

| Item | Authoritative evidence available | Status / missing proof |
|---|---|---|
| Understand current system (FR-001) | AGENTS/Constitution/workflow, current dirty HEAD/diffs, module traces, tests/CI, current listener/device queries | Local baseline established; whole-module coverage review incomplete |
| Preserve dirty work/state (FR-002/FR-008) | Timestamped per-slice backups/diffs/hashes, unchanged operator registry timestamps, no reset/commit/push/cleanup command | Preserved in performed slices; not authorization for future cleanup/deployment |
| API and URL compatibility (FR-003) | Dummy API/proxy, five-family validation and client fixtures | Local contracts tested; actual private tunnel and real five-URL runtime acceptance missing |
| Serial Worker, takeover, lease and cleanup (FR-003) | State-machine traces and multiple negative/positive temporary-manifest fixtures | Several defects repaired; operator recovery, all crash points and actual proxy/device restoration missing |
| Target/source/completeness/terminal/visibility (FR-003/SC-004) | Existing deterministic parser/result/recovery tests and selected current-source traces | No real fresh Facebook enumeration; independent whole-contract audit incomplete |
| Regression/evidence quality (FR-004/SC-003) | Per-slice red/green tests, local gate exact exits and summaries, explicit partial/skip semantics | Current optional external full-stream logging verified on Windows; historical tails not recoverable, skip/platform/whole-regression acceptance remains scoped |
| Security boundary review (FR-006) | Local HTTP/URL/DOM/data-path repairs, no production probes; narrowly independent export review | Limited release/local-reachable-history signature snapshot triaged by parent; independent classifications and broad security/leakage coverage incomplete |
| Platform pairing (FR-007) | Windows local tests, static paired code checks, heuristic scan, both CI command lists | 94 heuristic candidates not fully classified; hosted CI, native Mac and Android runtime missing |
| Performance (FR-005/SC-005) | Alternated six-run same-fixture Python benchmarks, exact export equality, allocation tradeoff | Local schema/aggregation benefit only; disk, queue, UI, device, network and E2E baseline missing |
| Safe simple operation (user objective) | Local workflow/gate with failure/partial results and operator docs | Storage-block recovery workflow and real one-click normal/failure acceptance missing |
| Whole completion (T019) | Current-task evidence explicitly partial | NOT MET; do not update goal to complete |

## Current external state

Latest read-only inspection: API 8765/UI 8766 are not listening. Existing ADB
127.0.0.1:5037 and proxy 7897 retain ownership; adb devices -l lists no device.
No Mac execution host is evidenced. Those are current missing acceptance resources,
not permission to reinstall/reset emulators, change accounts or start another tenant.
Code review/fixture work can still make progress, so the whole goal is not at an impasse.

## Task-record interpretation

Early checked T001–T008 and subsequent slice tasks document work performed in their
respective scope; they do not prove every clause of the original objective. In particular,
T003 requires full raw-output retention (not just successful process exit), T004 includes
leakage inspection (release path hygiene is not content/history scanning), and T016/T017
need independent/hosted/native evidence beyond the local fixture suite. Preserve their
history; append explicit outstanding tasks rather than erase gaps or silently redefine
acceptance. Current T009–T019 and independent recovery/platform/E2E tasks stay open.

## Next gates

1. Continue using verified optional external full-stream evidence; bounded summaries
   should label truncation and must not stand in for full failure investigation.
2. Complete independent Worker/persistence/lease review, operator-block recovery and
   restart durability with bounded temporary fixtures, preserving live state.
3. Classify remaining platform/secret/data-contract findings using exact current code.
4. Establish an authorized real-device/host acceptance environment without state resets;
   verify normal, failure/cancel/cleanup, five URL families and artifacts end-to-end.
5. Measure real same-condition performance including correctness and resource costs;
   retain local measurements as local rather than extrapolate percentages.
6. Re-run requirement-level completion audit before any whole-goal success claim.



## Continuation audit — 2026-10-02

The current continuation closed the reproduced sibling registered-handle lease-scan gap and recorded fresh local evidence. The new matrix and full local gate are authoritative for this narrow change only. The original system-wide objective remains **NOT MET**: API/UI are not running, no ADB device is attached, native macOS/Facebook acceptance is unavailable, performance E2E baselines are absent, and remaining durability/leakage/independent review tasks are open. The local gate's 743-test result must not be promoted to whole-system completion.

## Open durability boundary identified after continuation

The existing regression intentionally excludes restart assertions for the `all-postcapture-writes` case because the failure model assumes every post-capture write is unavailable. The current source proves in-memory fail-closed behavior, but it does not yet prove what a second Manager reconstructs from the last durable pre-capture manifest in that specific boundary. T071 is therefore open and must be resolved with an isolated temporary-manifest experiment before any durability or exactly-once claim.

## T071 result

The `all-postcapture-writes` restart boundary is now covered. When no post-capture diagnostic can be durably written, the second Manager reconstructs a failed job with `restart-process-identity-unverified-no-auto-resume`, keeps the capture lease uncertain, has no active worker, and rejects fresh assignment. The absence of a persistence-reason registry in this branch is expected because no diagnostic write succeeded; fail-closed lease state, not an invented durable acknowledgment, prevents recapture. Physical disk/power-loss semantics remain outside this fixture and are still unverified.

T071 is locally closed by the isolated second-Manager experiment and fresh 743-test gate. The result is a fail-closed reconstruction, not a durability guarantee for actual disk/power loss: no persistence-reason registry entry exists when every post-capture write fails, but the reconstructed failed state remains lease-uncertain and unassignable. Native filesystem crash consistency, real devices, macOS and Facebook E2E remain unverified.

## Current runtime reliability focus

The latest bounded runtime fixture suite passed 172 tests across cancellation transport, Worker bootstrap, capture orchestration and shutdown safety. This strengthens local T010/T011 evidence but does not close the real-platform portion: no API/UI service or ADB device is active, and native process, proxy, emulator and Facebook acceptance remain unavailable.

## macOS pairing evidence update

The latest Windows-hosted macOS pairing fixture run passed 16 tests. It improves static/fixture evidence for T012 but leaves native macOS execution and real device acceptance unavailable; no task status is promoted beyond the evidence actually collected.

## Setup/local evidence update

The latest setup/local/readiness/transport fixture run passed 56 tests with one explicit skip. This strengthens deterministic deployment-contract evidence only; it does not establish current configuration ownership, account login, device readiness or live transport health.

## Runtime ownership refresh

The latest read-only snapshot confirms no API/UI listener and no attached ADB device; the existing ADB daemon and verge-mihomo proxy remain owned by their observed processes. This preserves the boundary for local testing: no restart, cleanup or device reset was authorized or performed.

## Final local gate refresh

The current worktree's fresh full local gate passed with 743 tests and two explicit skips. This confirms no regression from the acceptance-matrix/audit-document additions; it does not close the external Windows runtime, Android/Facebook, macOS, hosted-CI, crash-consistency or performance gates.

## T073 real startup update

T073 has partially advanced: the authorized `START.cmd` path now has current evidence for real AVD boot, ADB readiness, proxy/reverse transport, API/UI health and active Worker lease availability. It is not complete: no target URL was submitted, no credentials were entered, and Facebook result/visibility/completeness acceptance remains open.

## Real Facebook session readiness update

The emulator now exposes a usable Facebook feed/session after the authorized startup. Target-specific source/visibility/completeness and terminal-pagination acceptance are still open because no user-approved target URL has been submitted.


## Real numeric-post acceptance — 2026-10-03

Historical target located in FACEBOOK_BROWSER_SAMPLES.md (2026-09-08 sample), reused under current user authorization to continue with previous work-log targets. One POST only, default terminal semantics, no cap/wait/version/config change. Job fb_3a266090484a4e00 targeted numeric post 1691321539667513. Start 2026-10-03T03:32:34.418439Z; finish 03:35:09.557538Z (155.139s). Live result: completed/terminal, target matched by graphql-response-post-id, 39 successful pages, 975 raw reaction rows, 950 unique identities, 25 duplicates, reported total 950, gap 0, terminal page true, open tail false, rate-limit records 0. All group terminal; a separate filtered-reaction group remains open, so no claim that every filtered group was independently exhausted. Shares: query-not-triggered/0 users, NOT accepted as empty or complete.

After completion: process stopped, transport runner-owned, health worker idle/queue0/lease available; Android proxy 127.0.0.1:7897, reverse tcp7897 and tcp8081 present. UIAutomator during capture showed Facebook package controls (no account labels retained). Screenshot visual inspection was unavailable in this model; no screenshot-based acceptance is claimed. Run artifacts: C:/Users/lenovo/Desktop/facebook/runs/api-fb_3a266090484a4e00/attempt-001. This establishes one numeric-post Reaction run, not five URL families, Shares, native Mac, cancellation or same-condition before/after performance.


## Final local handoff gate — 2026-10-03

Original final-handoff131300 process/session vanished; reportempty,stderrunfinisheddots, no completion verdict. Original evidence retained and NOTPASS. One read-only fixture/static retry with a NEWexternal directory completed:749tests128.195s,OK(skipped2),exit0,statuspassed; savedreport and original commandstreams readback+SHA256 verified, integritymanifest at C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-final-handoff-retry-01/stream-integrity-final.json. No repeatcapture/APIrestart/independentreview/nativeexecution claim. This includes the new concurrentack regression that predates this gate; previous748 result now historical.

External-statechange before gate: API8765/UI8766 no longer listening, emulator notrunning; oldUIpid44816 reused for cmd.exe created13:12:17, neverkill by stalepid. Adbdevices query automatically started daemon5037 (not a side-effect-free command), no attacheddevice; proxy7897 remains observed. No reason for service disappearance established; no automaticrepair/relaunch/cleanup performed. Earlier successful jobs/cancel prove those dated runs, NOTcurrentruntimehealth.

Residual original objective unmet: independentlyattributed T080 reviewer requires explicit delegationauthorization;T081 nativeMac/hostedCI unavailable (all94sourceleads triaged but not nativeaccepted). No new E2Espeedup claim: T082finitecomparison only correctness/negativeguards, live responsevaried. Broad earlier independent/history/platform requirements remain unclosed; goal NOTcomplete. Further unchanged-loop retests/document append are not meaningful progress. Preserve all dirtysource/rawartifacts/services/userconfig.


## Ledger distinction — 2026-10-03

Independentreview/nativeproof are acceptance obligations, NOT gates against implementing Windows code. Four stale checkboxesT018/T053/T060/T067 corrected from direct evidence; no invented new code requirement or speedupclaim. Stillopen tasks include broad aggregates and independent review/native/performance clauses; their count is NOT unimplemented feature count. Main-agent reread cannot replace an independentreview, but missing reviewer authorization alone is not reason to stop other authorized implementation. Current environment restoration already separately authorized and verified. No commit/push/deletion/nativeMac claim.


## Confirmed unstarted scheduler rollback repair — 2026-10-03

StartWorker helper registered thread/startedIDs before Thread.start; RuntimeError/OSError on demonstrablyunstarted thread left phantom _worker, making later start return an unstartedhandle. Two redsubcases reproduced exactpointerdefect. Minimal repair serializes publication+start underexistingRLock and rollsback only exactownedthread with identNone; restores priorstartedIDs/removesthatregistration, rethrows originalexception. If identnonNone, handle retained and latercalls do NOT startanotherthread. No arbitrary deadworker autorestart, queue/concurrency/timing/visibility/config/API contract change. Native testThread target entersManagerlock successfully (boundedjoin), confirming no start/Managerlock deadlock. No live resourcefault injection.

Source/test/task backups C:/Users/lenovo/AppData/Local/Temp/facebook-worker-start-20261003-135210/. Initial focus91tests hit independentWindows10053 in HTTPsourcefixture: MockManager noStartWorker call, proxylogs403 before unreadbody close; originalfailure retained. Only Host/Origin source-fixtures changed to header-onlyContentLength20 (guard mustrejectBEFOREbodyread), no productionHTTP weakening/auto retry; finalfocus91pass7.992s. Native addedtest included in finalfull752tests129.518s,OK(skipped2),exit0;reports/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-worker-start-final-01/. This supersedes749fullbaseline for currentcode. Mainagent evidence, not independentlyreviewed.

Appliedcode via ownedIDLE API reload afterverifyinghealthidle/queue0/cancelling0/leaseavailable and Python listenerabsoluteprojectcmdline+creationtime. Only ownedAPIpid35836 stopped throughprocesshandle; no stalePIDkill/globalSTOP/devicefault/reset. API/UIlogs+configs copied BEFORE existingSTART.cmd to C:/Users/lenovo/AppData/Local/Temp/facebook-api-update-logs-20261003-140231/. STARTexit0,reuseddevice and healthyUI,creatednewAPI;posthealthready/idle/available/queue0,Androiddeviceboot/proxy127.0.0.1:7897,UI200. Configbytehashunchanged. Existingnumeric950terminal/Shares16terminal readable;cancelledresultHTTP409. No repeatFacebookcapture. API rarefailure rollback confirmed in isolatedfixture, not realresourceexhaustion; actualstartup validated nonfailurepath. T084complete within declaredscope;Mac/independentreview/actualE2Espeedup remainunproven.


## Typed scheduler error HTTP completion — 2026-10-03

Three red HTTPoperator cases directly called actual_start_worker with mocked nativeRuntimeError;exceptionescapedreload/enable/reconcile routes. Narrow SchedulerStartError(RuntimeError) now wraps ONLY native Thread.start RuntimeError with original__cause__;OSError unchanged;genericManagerRuntimeError is NOT caught. Three Managerinvoke boundaries maptypederror tosingle sanitized503/inspect-state-before-retry. Responsewriting remainsoutsideManagercatch; even SchedulerStartError in reply propagates, oneManagercall/onewriteattempt,noautoretry. Existing200/404/409/requestguard/statusbody contracts retained.

ActualtemporaryManager direct+v2proxy fixture demonstrates ackmanifest alreadydurablethenThreadfail=>503while jobremainsfailed/reconciled,registrationrolledback,noactualcapture;secondmanualnegativeack rejects409/startonlyonce. Thus503NOTfullrollback;API_USAGE documents inspectjob/queue/health first,noautomaticPOSTretry. Causeidentity/unrelatedlogic/replyerrors assertions covered. Focus71pass15.137s;finalfull755tests136.770s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-scheduler-http-final-01/. Source/test/doc/taskbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-http-20261003-140854/,boundedproductiondiff48lines. Mainagentreview,notindependent.

Applied by ownIDLE APIreload afterhealthidle/queue0/cancelling0/available pluslistenerPython/projectcmdline/creationtime verified. OnlyAPIpid40600 stopped throughprocesshandle,logs/configsbeforebackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-http-reload-20261003-141911/. STARTexit0/reusedexistingemulator+UI/newAPIready. Posthealthidle/available/device/proxy127.0.0.1:7897/UI200;existingReaction950/Share16preserved;cancelledresult409. ConfigbyteSHA256unchanged. No realthreadresourcefault/storagefault/duplicatecapture/accountreset/versionchanges. T085complete scope;nativeMac/independentreview/E2Espeedup remainnotclaimed.


## Current Windows handoff distinction — 2026-10-03

Implementation applied to current API after owned-idle reload: candidatepersist/lease guards,explicit Reelmapping,passive phase timing,unstarted scheduler rollback,typed startup HTTP503 with cause/inspect-before-retry. Currentfullgate755run/753pass/2skip;successful five-family Reactions,visible Share16/46,real cancel/recovery and preserved results remain scoped evidence. T010/T011 isolation gates closed; remainingcheckboxes mix independentreview/native/platform/performance aggregates, not missingfeaturecount. No independentlyconfirmedall-systempass,Macnative support or E2Espeedup claimed.


## Additive scheduler health snapshot — 2026-10-03

Source health previously reportedready/idle basedonlyHTTP/jobactivity, evenabsent/dead scheduler. Added ONLY scheduler={state,alive} underManagerlock, statesrunning/true,not-started/false,stopped/false,unknown/null onRuntimeError/OSErrorprobe. No nativeIDs/argv/exceptiondetail; preserveoldservice/worker/mode/lease fields. Observerneverdropshandle,clearsjobs/lease,startsworkers or retriesPOST. Sampling is not guaranteed continuousliveness/jobcompletion/cleanup proof. ABI-independent stdthread observation, nativeMac notexecuted. API_USAGE/spec/plan recordadditivecontract.

Two redtests initially6missingfieldsubcases; fixedmissing/unstarted/running/stopped and twoprobeexceptions; ownership/uncertainlease/oldfields preserved. Focus73tests14.588s pass;full757tests130.313s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-scheduler-health-final-01/. Source/test/spec/plan/task/docbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-health-20261003-181137/. Current code now755pass/2skip, not old755runtotal. No claimed speedup or independentreview.

APIconnection failure was followedbyconfigport8765/listener+process ownershipchecks;confirmednoAPI/UI/captureprocess/marker/capture8081listener. Existingproxy7897/ADB5037 retained. Restoredclosed environment viauser's explicitreopenauthorization, BEFOREstartup4logs+configsbackup C:/Users/lenovo/AppData/Local/Temp/facebook-scheduler-health-restore-20261003-182013/. STARTexit0,newAVDboot/newAPI/UIready;oldstaleAVDlocks movedbyexistinghelper to avd-lock-backup-20261003-182019,no wipe/userdata reset. PostactualGET /health schedulerunning/alivetrue,service ready/workeridle/queue0/cancelling0/leaseavailable,Androiddevice/proxy127.0.0.1:7897,UI200;retainedReaction950/Share16/cancelresult409. ConfigSHA256unchanged. No liveprobeexception/resourcefault injection,repeatcapture/agent/globalwatchdog/versionchange. T086complete currentdeclaredscope.


## Artifact physical path robustness completion — 2026-10-03

Temporary ownfixture junction to sibling syntheticoutside directory reproduced oldartifact() returningallowedCSV beyonddeclaredrunroot (red). Minimal shared_checked_artifact_path resolvestrustedroot/candidate, rejects childsymboliclinks and escapingjunctions before file stat/read, preserveslogicalpath/name andtrustedrootrelocation. Job/batchdownloads and metadata use same check; blockedfiles0bytes/missingnotready. Existing availabilitylabels retained. Helper handlesresolutionOSError/RuntimeError withoutrawpath text. This is accidental/stale-link robustness,NOTconfirmedremote/lower-trustexploit,tenantpolicy or malicioushostwriter sandbox. Hardlinks/TOCTOU/hostilelocalmanifest authority expresslyunproven. API_USAGE/spec/plan detail intentionalchild-link incompatibility andcopyregularartifact guidance;validtrustedrootmove remains allowed.

NativeWindowsjunctionfixture onlyinside explicitlycheckedTEMPparent (target/linkparentcanonicalinside scratch); no actualjoblinks/userfilesaltered. Three tests cover job/batch/metaescape,regularfiles+rootrelocation,missing/empty/resolutionerrors. Initialred case exposedAPIreturn; fixture metadata assertion corrected to existingstate/size schema, no addedavailableflag. Focus9tests4.103s pass;full760tests126.323s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-artifact-confinement-final-01/. Code/test/spec/plan/task/docbackup C:/Users/lenovo/AppData/Local/Temp/facebook-artifact-confinement-20261003-184322/.

Applied afteridentity+idle/queue0/cancelling0/available checks:onlyownedAPIpid8148 stoppedthroughprocesshandle,logs/configsbeforebackup C:/Users/lenovo/AppData/Local/Temp/facebook-artifact-confinement-reload-20261003-185547/. ExistingSTARTexit0/reusedemulator+UI/newAPI;actualAPI8765/v2proxy8766 numericCSVboth200/178253byte/hash=ondisk. Posthealthready/schedulerrunning/workeridle/queue0/leaseavailable/configbytehashunchanged. No repeatcapture or livepathfaultinjection. T087declaredscopecomplete;T009source/localreviewclosed using logicalalias8case proof,physicalfixture,manifestrecoveryT060+T010/T011 andparent release/localhistoryT053 snapshot;NOTindependentT057/securityapproval/allOSfilesystem guarantee. Mainagentreview only, nativeMac/hostedCI/independentreview remainseparate.


## Request-local health ADB batching and measured benefit — 2026-10-03

Initial realHTTP6samples median445.709ms suggested health subprocess overhead; source3workers eachstatequery plus activeboot/proxy=>5ADbcalls. Added lazy ContextVar holder onlywithin health() worker-snapshot list, tokenresetfinally includingexceptions; realadb_snapshot usesonevalidatedunique device-list, boot/proxy queries/timeouts/errorsemantics unchanged. Directsnapshot outsidehealth independent. Injected/mocked snapshotprovider bypasseslazyinventory withoutrealdeviceprobes. Malformed/duplicate/unknownstate/header/exit/OSError/TimeoutExpiredinventory=>oldindividualstates, oneboundedextraquery cost onfallback (no inventedhealthystate). No TTL/persistentcache/paralleltenants/SDKversion/waitchange. Concurrentrequests ownscope, snapshots not continuousfreshness guarantee.

Tests initially3redbatchexpectations(noinventory);5newmethods cover fewercalls/freshnextrequest/directoutsideprobe,invalidlist+proxytimeout preservingdevice,failedinventory3cases,2concurrentrequests/barrierseparateinventories,exceptioncontextcleanup/noinjectedproviderprobe. Focus74tests3.852s plus5finalbatchtests pass;full765tests131.889s,OK(skipped2),exit0/originalstreams C:/Users/lenovo/security-audit-skill/facebook/checks/run-20261003-health-adb-batch-final-01/. Source/test/spec/plan/task/APIUsagebackup C:/Users/lenovo/AppData/Local/Temp/facebook-health-adb-batch-20261003-191920/. No independentlyattributedreview.

Samehost/config/SDKadb/binary/device,6alternatingold/current Managerhealth pairs readonly actualADB, fullreports equal EVERYpair: oldmedian475.905ms/5commands vsnew325.145ms/3commands (~31.7percent wall reduction on this measured method). Originaltoken-freeprivatebenchmarkrecords/hashes C:/Users/lenovo/AppData/Local/Temp/facebook-health-batch-paired-20261003-192350.json. Runtime/jobs empty in controlledManager so no capture/queue interaction; no CPU/RSS/totalprocessmemory metric or FacebookE2Espeedup claim.

Applied viaownidleAPIreload onlyPID46768 verifiedPython/projectpath/creationtime+queue0/cancelling0/available;logs/config backup C:/Users/lenovo/AppData/Local/Temp/facebook-health-adb-batch-reload-20261003-193739/. STARTexit0/reusedemulator/UI;actualHTTPpostapply6samples median312.0365ms, source C:/Users/lenovo/AppData/Local/Temp/facebook-health-batch-live-20261003-193830.json (observational notalternatingHTTPcontrol). Posthealthready/schedulerrunning/idle/queue0/leaseavailable;existingCSVproxyhashunchanged/cancelresult409/configbytehashunchanged. No duplicatecapture/livefault injection/accountreset/globalwatchdog. T088 complete endpointscope;T013-T015 broadercapture/resourcebeforeafter/native/independentacceptance remainseparate.


## Windows 单次真实资源基线（2026-10-03）

任务 `fb_80a78d0601db4a8a`：历史 Reel 样本，单次提交，无抽样上限或等待改动。采集耗时 112.593 秒；采样窗口约 113.84 秒、40 个快照。结果 2 人/报告总数 2、目标匹配、terminal、差额 0，进程 stopped、传输 runner-owned；观察脚本退出码 0。

| 观察范围 | 观察到的 CPU 秒 | 采样工作集峰值之和 MiB | I/O 读取传输 MiB | I/O 写入传输 MiB |
|---|---:|---:|---:|---:|
| api | 7.000 | 58.91 | 57.69 | 40.15 |
| capture-tree | 25.641 | 499.91 | 74.35 | 40.00 |
| emulator | 191.000 | 1817.88 | 204.73 | 40.38 |

限制：采样后等待 2 秒，加上查询时间，实际间隔并非严格 2 秒。短命进程和两次采样之间的峰值可能漏采；观察到 27 个捕获树进程，不是所有曾启动进程的全量证明。API/模拟器用窗口计数差值；新捕获树进程用最后观察到的累计值。工作集相加含共享页，不是系统物理内存总量；I/O transfer 包含文件、管道、网络，不能叫物理磁盘吞吐。模拟器指标含 Android 后台活动；API指标含观察脚本轮询和用户页面请求。多核 CPU 时间可超过墙钟时间。

这是一份单次资源基线，不是优化前后对照或失败率统计，也不足以完整关闭 T013–T015。不能据此减少模拟器 CPU/RAM、缩短稳定等待或宣称采集提速。生产代码和既有配置未改；未安装 psutil。未重复提交、中断/杀死采集、重启环境或做压力测试。

证据目录：`C:/Users/lenovo/AppData/Local/Temp/facebook-live-resource-20261003-212126/`，包含 sample.ps1、ownership.json、samples.jsonl、summary.json、observer.log；报告不保存用户姓名、账号凭证或命令行。当前 API ready、scheduler running、Worker idle、队列 0、lease available。
