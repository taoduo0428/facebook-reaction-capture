# Reply exactly OK
_Exported on 08/17/2026 at 10:15:56 GMT+8 from OpenAI Codex via WayLog_


**OpenAI Codex**

## Memory

You have access to a memory folder with guidance from prior runs. It can save
time and help you stay consistent. Use it whenever it is likely to help.

Decision boundary: should you use memory for a new user query?

- Skip memory ONLY when the request is clearly self-contained and does not need
  workspace history, conventions, or prior decisions.
- Hard skip examples: current time/date, simple translation, simple sentence
  rewrite, one-line shell command, trivial formatting.
- Use memory by default when ANY of these are true:
  - the query mentions workspace/repo/module/path/files in MEMORY_SUMMARY below,
  - the user asks for prior context / consistency / previous decisions,
  - the task is ambiguous and could depend on earlier project choices,
  - the ask is a non-trivial and related to MEMORY_SUMMARY below.
- If unsure, do a quick memory pass.

Memory layout (general -> specific):

- C:\Users\lenovo\.codex\memories/memory_summary.md (already provided below; do NOT open again)
- C:\Users\lenovo\.codex\memories/MEMORY.md (searchable registry; primary file to query)
- C:\Users\lenovo\.codex\memories/skills/<skill-name>/ (skill folder)
  - SKILL.md (entrypoint instructions)
  - scripts/ (optional helper scripts)
  - examples/ (optional example outputs)
  - templates/ (optional templates)
- C:\Users\lenovo\.codex\memories/rollout_summaries/ (per-rollout recaps + evidence snippets)
  - The paths of these entries can be found in C:\Users\lenovo\.codex\memories/MEMORY.md or C:\Users\lenovo\.codex\memories/rollout_summaries/ as `rollout_path`
  - These files are append-only `jsonl`: `session_meta.payload.id` identifies the session, `turn_context` marks turn boundaries, `event_msg` is the lightweight status stream, and `response_item` contains actual messages, tool calls, and tool outputs.
  - For efficient lookup, prefer matching the filename suffix or `session_meta.payload.id`; avoid broad full-content scans unless needed.

Quick memory pass (when applicable):

1. Skim the MEMORY_SUMMARY below and extract task-relevant keywords.
2. Search C:\Users\lenovo\.codex\memories/MEMORY.md using those keywords.
3. Only if MEMORY.md directly points to rollout summaries/skills, open the 1-2
   most relevant files under C:\Users\lenovo\.codex\memories/rollout_summaries/ or
   C:\Users\lenovo\.codex\memories/skills/.
4. If above are not clear and you need exact commands, error text, or precise evidence, search over `rollout_path` for more evidence.
5. If there are no relevant hits, stop memory lookup and continue normally.

Quick-pass budget:

- Keep memory lookup lightweight: ideally <= 4-6 search steps before main work.
- Avoid broad scans of all rollout summaries.

During execution: if you hit repeated errors, confusing behavior, or suspect
relevant prior context, redo the quick memory pass.

How to decide whether to verify memory:

- Consider both risk of drift and verification effort.
- If a fact is likely to drift and is cheap to verify, verify it before
  answering.
- If a fact is likely to drift but verification is expensive, slow, or
  disruptive, it is acceptable to answer from memory in an interactive turn,
  but you should say that it is memory-derived, note that it may be stale, and
  consider offering to refresh it live.
- If a fact is lower-drift and expensive to verify, it is usually fine to
  answer from memory directly.

When answering from memory without current verification:

- If you rely on memory for a fact that you did not verify in the current turn,
  say so briefly in the final answer.
- If that fact is plausibly drift-prone or comes from an older note, older
  snapshot, or prior run summary, say that it may be stale or outdated.
- If live verification was skipped and a refresh would be useful in the
  interactive context, consider offering to verify or refresh it live.
- Do not present unverified memory-derived facts as confirmed-current.
- Prefer a short refresh offer for interactive questions, especially about prior
  results, commands, timing, or older snapshots.

Memory citation requirements:

- If ANY relevant memory files were used: append exactly one
`<oai-mem-citation>` block as the VERY LAST content of the final reply.
  Normal responses should include the answer first, then append the
`<oai-mem-citation>` block at the end.
- Use this exact structure for programmatic parsing:
```
<oai-mem-citation>
<citation_entries>
MEMORY.md:234-236|note=[responsesapi citation extraction code pointer]
rollout_summaries/2026-02-17T21-23-02-LN3m-example.md:10-12|note=[weekly report format]
</citation_entries>
<rollout_ids>
019c6e27-e55b-73d1-87d8-4e01f1f75043
019c7714-3b77-74d1-9866-e1f484aae2ab
</rollout_ids>
</oai-mem-citation>
```
- `citation_entries` is for rendering:
  - one citation entry per line
  - format: `<file>:<line_start>-<line_end>|note=[<how memory was used>]`
  - use file paths relative to the memory base path (for example, `MEMORY.md`,
    `rollout_summaries/...`, `skills/...`)
  - only cite files actually used under the memory base path (do not cite
    workspace files as memory citations)
  - if you used `MEMORY.md` and then a rollout summary/skill file, cite both
  - list entries in order of importance (most important first)
  - `note` should be short, single-line, and use simple characters only (avoid
    unusual symbols, no newlines)
- `rollout_ids` is for us to track what previous rollouts you find useful:
  - include one rollout id per line
  - rollout ids should look like UUIDs (for example,
    `019c6e27-e55b-73d1-87d8-4e01f1f75043`)
  - include unique ids only; do not repeat ids
  - an empty `<rollout_ids>` section is allowed if no rollout ids are available
  - you can find rollout ids in rollout summary files and MEMORY.md
  - do not include file paths or notes in this section
  - For every `citation_entries`, try to find and cite the corresponding rollout id if possible
- Never include memory citations inside pull-request messages.
- Never cite blank lines; double-check ranges.

Updating memories:

You can update the memories **only** when explicitly asked by the user. This must always come from a direct request from the user.
- Write your update in C:\Users\lenovo\.codex\memories/extensions/ad_hoc/notes/
- Each update must be one small file containing what you want to add/delete/update from the memories.
- The name of this file must be `<timestamp>-<short slug>.md`
- Do not try to edit the memory files yourself, only add one update note in C:\Users\lenovo\.codex\memories/extensions/ad_hoc/notes/

========= MEMORY_SUMMARY BEGINS =========
v1

## User Profile

Windows-based user, mainly working in Chinese, who maintains local Codex/MCP tooling, Leap repositories, Android emulator capture, and automation projects. They expect concise operational reporting grounded in the controlling config, process, command, or API evidence. They prefer focused, reversible changes; clear scope boundaries; and verification of the user-visible result. For handoffs, they want directly executable documentation with evidence and explicit limits rather than a narrative recap.

## User preferences

- Execute an authorized configuration, maintenance, or repair change through concrete verification; do not stop at a plan when recovery is feasible.
- Keep changes minimal, reversible, and inside the stated scope; verify the actual target checkout/remote before editing.
- Preserve read-only boundaries and requested tool order. Report unavailable UI-only state as unavailable rather than guessing.
- For a focused runtime upgrade, preserve stated compatibility requirements such as portable Python, auth, workspace restrictions, tunnel, and custom tooling; explain upstream versus project-patch provenance.
- For a verified capture workflow, write the successful method into relevant local project memory with exact UI entry labels/bounds and evidence paths; distinguish observations from composer/account actions.
- For external submission workflows, draft and dry-run first when asked not to submit; submit only after explicit confirmation and report returned IDs.

## General Tips

- On Windows, use bounded `rg` roots, validate UTF-8/TOML explicitly, and avoid broad profile searches that time out.
- Treat transport/health success separately from functional readiness: verify authentication, target trigger/query, persisted result, and user-visible output.
- Preserve generated logs/files before branch switches. When a fetched branch has no tracking ref, inspect refs and create the local branch at the fetched SHA.
- For Android capture, an open port or UI count is not proof: require a fresh target GraphQL response, source ID, parsed rows, and restored normal proxy.
- For vendored runtime upgrades, find the source-to-packaged-runtime copy step and synchronize both before live verification.

## What's in Memory

### C:\Users\lenovo\Desktop\facebook

#### 2026-08-15

- Facebook Android proxy capture and research handoff: emulator-5554, Clash TUN, strict-route, 10.0.2.2:7897, mitmproxy, ResharesOfContentFeedQuery, 8次分享
  - desc: Networking recovery, read-only capture boundary, API/UI evidence, and executable handoff for cwd=C:\Users\lenovo\Desktop\facebook.
  - learnings: Burp reachability was not a stable Facebook capture path; use Clash TUN plus the temporary mitmproxy topology, require fresh HTTP 200 query evidence, and restore `10.0.2.2:7897`.

### C:\Users\lenovo\Desktop\破限\gpt-webcodex

#### 2026-08-15

- Embedded Coding Tools MCP v0.3.0 upgrade: coding-tools-mcp, 5d6e131a, prepare-native-runtime.ps1, command_id, kill_command, 0.3.0+gptwebcodex.1
  - desc: Upstream stable migration and live Windows web-assistant verification; search for source/runtime synchronization and compatibility retention.
  - learnings: Confirm the real remote first; sync `resources/coding-tools-mcp` into portable Python, preserve the custom tool/auth/tunnel contract, and distinguish source tests from live installation proof.

### C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct / C:\Users\lenovo\Desktop\破限\test-mcp

#### 2026-08-10

- Feishu Wiki login routing: Feishu Wiki, HTTP/1.1 302 Found, accounts.feishu.cn, redirect_uri, OAuth, SSO
  - desc: Official-login/OAuth/SSO route for the observed protected Wiki URL; cwd=C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct.
  - learnings: Recheck the live redirect before diagnosing another resource; do not treat it as a public document.
- Project inspection and Coding Tools MCP OAuth: React 18, Unknown client_id, Authorize Coding Tools MCP, run-mcp-service-hidden.cmd
  - desc: Read-only React/Vite orientation, runtime audit, and web-plugin OAuth repair; cwd=C:\Users\lenovo\Desktop\破限\test-mcp.
  - learnings: Lead connector recaps with field-by-field settings and verify the authorization endpoint separately from completed user reconnection.

### C:\Users\lenovo\Documents\Codex\2026-08-09\new-chat

#### 2026-08-09

- LeapAgent DRV v2 assessment and ModeShadow validation: assessment.go, work_items, pending_review, BPS, REAL-001, real-source.db
  - desc: Minimal assessment hardening and isolated real SQLite replay, plus a later untested diff inspection.
  - learnings: Only explicit `work_items: []` is valid zero delivery; retain uncertainty when fresh test evidence is absent.

### Older Memory Topics

#### C:\Users\lenovo\.codex / C:\Users\lenovo\Desktop\铁陨\test

- Coding Tools MCP, provider fallback, skills, RTK, and DailyWork: config.toml, coding-tools-mcp, newline-delimited JSON-RPC, tomllib, rtk init -g --codex, lark-cli.cmd
  - desc: Global Codex configuration, capability verification, FastCtx governance, and evidence-gated Feishu report automation; recheck live configuration and session state.
- Codex Desktop and VS Code startup performance: logs_2.sqlite, mcpServerStatus, codex mcp list --json, stale-cwd retries
  - desc: Separate renderer/MCP/plugin/hook/database costs and use supported SQLite maintenance; cwd=C:\Users\lenovo\.codex.

#### C:\Users\lenovo\Desktop\leap

- LeapAgent/LeapLite DRV, report templates, and enterprise test startup: ModeShadow, feat/drv-summary-button, electron:rebuild-native, POST /api/control/drv/debug/today, 功能变更说明.md
  - desc: DRV validation, exact change-report format, branch recovery, and real report interpretation across Leap worktrees.

#### Windows profile and Desktop projects

- IELTS P3 semantic canvases: build_p3_topic_category_canvases.py, 10 semantic categories, 56 themes, visual contrast
  - desc: Semantic canvas rebuild and local contrast refinement under C:\Users\lenovo\Desktop\图谱.
- BOSS crawler cleanup and migration: BOSS crawler, boss_jd_backfill.py, ip_pool.py, rollback-artifacts, SHA-256
  - desc: Archive/migrate crawler code while preserving live state and outputs under C:\Users\lenovo\Desktop\外包+岗位爬虫.
- Disk triage, Codex warnings, hidden MCP launcher, and GrokFarmBox: SoftwareDistribution, .crawler-state, hidden-stdio-launcher-v3.exe, GrokFarmBox
  - desc: Safe cleanup classification, path-preserving migration, Codex repair, and bounded startup diagnosis; confirm current state before actions.
========= MEMORY_SUMMARY ENDS =========

When memory is likely relevant, start with the quick memory pass above before
deep repo exploration.
<skills_instructions>
## Skills
A skill is a set of local instructions to follow that is stored in a `SKILL.md` file. Below is the list of skills that can be used. Each entry includes a name, description, and a short path that can be expanded into an absolute path using the skill roots table.
### Skill roots
- `r0` = `C:/Users/lenovo/.codex/skills`
- `r1` = `C:/Users/lenovo/.agents/skills`
- `r2` = `C:/Users/lenovo/.codex/skills/.system`
- `r3` = `C:/Users/lenovo/.codex/plugins/cache/openai-bundled`
- `r4` = `C:/Users/lenovo/.codex/plugins/cache/openai-primary-runtime`
- `r5` = `C:/Users/lenovo/.codex/plugins/cache/openai-primary-runtime/spreadsheets/26.813.12317/skills`
### Available skills
- imagegen: Generate or edit raster images when the task benefits from AI-created bitm (file: r2/imagegen/SKILL.md)
- openai-docs: Use for Codex models/pricing, scheduled tasks, skills, settings, setup,  (file: r2/openai-docs/SKILL.md)
- plugin-creator: Create and scaffold plugin directories for Codex with a required `.codex-p (file: r2/plugin-creator/SKILL.md)
- skill-creator: Guide for creating effective skills. This skill should be used when user (file: r2/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into $CODEX_HOME/skills from a curated list or a Gi (file: r2/skill-installer/SKILL.md)
- ai-multimodal: Process and generate multimedia content using Google Gemini API. Capabil (file: r1/ai-multimodal/SKILL.md)
- api-security: Use for authorized REST, GraphQL, WebSocket, SOAP, authentication, author (file: r0/reverse-skill/skills/api-security/SKILL.md)
- apk-reverse: 在 CLI 环境下做 Android APK 逆向时使用。适用于 APK 解包、Java 反编译、smali 修改、重打包、Frida 动态  (file: r0/reverse-skill/skills/apk-reverse/SKILL.md)
- beamer-skill: Create, compile, review, and polish academic Beamer LaTeX presentations. F (file: r1/beamer-skill/SKILL.md)
- bgpt-paper-search: Search scientific papers and retrieve structured experimental data extra (file: r1/bgpt-paper-search/SKILL.md)
- binary-diff: 跨版本符号迁移与二进制差分。当你有旧版本的符号/逆向结果，需要快速迁移到新版本时使用。 适用场景：内核缺 PDB 用旧版符号推导、程序更新后批 (file: r0/reverse-skill/skills/binary-diff/SKILL.md)
- browser:control-in-app-browser: Control the in-app Browser for opening, navigating, inspecting visible or  (file: r3/browser/26.810.52044/skills/control-in-app-browser/SKILL.md)
- ccp-party-building-materials: 中国共产党党建、党务、党委、党支部、组织生活会、民主生活会、主题教育、党课、述职、整改、工作汇报、工作总结、先进事迹、领导讲话等材料写作与修改 (file: r1/ccp-party-building-materials/SKILL.md)
- chrome:control-chrome: Control the user's Chrome browser for tasks that depend on existing Chro (file: r3/chrome/26.810.52044/skills/control-chrome/SKILL.md)
- citation-management: Comprehensive citation management for academic research. Search Google S (file: r1/citation-management/SKILL.md)
- computer-use:computer-use: Control Windows apps from ChatGPT (file: r3/computer-use/26.810.52044/skills/computer-use/SKILL.md)
- ctf-sandbox-orchestrator: Default entrypoint and master ctf-sandbox-orchestrator workflow for CTF (file: r0/reverse-skill/CTF-Sandbox-Orchestrator/ctf-sandbox-orchestrator/SKILL.md)
- dailywork: Generate and submit DailyWork personal or department daily reports from  (file: r0/dailywork/SKILL.md)
- dask: Distributed computing for larger-than-RAM pandas/NumPy workflows. Use when (file: r1/dask/SKILL.md)
- database-lookup: Search 78 public scientific, biomedical, materials science, and economic (file: r1/database-lookup/SKILL.md)
- diagram-generator: generate, refine, validate, and render diagrams from natural language,  (file: r0/reverse-skill/skills/diagram-generator/SKILL.md)
- documents:documents: Create, edit, redline, and comment on `.docx`, Word, and Google Docs-tar (file: r4/documents/26.813.12317/skills/documents/SKILL.md)
- edr-bypass-re: 逆向防御方实现 → 红队针对性绕过。把 EDR / Defender / AV 的 hook 表、ETW provider、AMSI 实现先逆 (file: r0/reverse-skill/skills/edr-bypass-re/SKILL.md)
- exploratory-data-analysis: Perform comprehensive exploratory data analysis on scientific data files (file: r1/exploratory-data-analysis/SKILL.md)
- find-skills: Helps users discover and install agent skills when they ask questions li (file: r1/find-skills/SKILL.md)
- firmware-pentest: 固件 / IoT 渗透链。从拿到一坨 .bin / .img 开始，闭环走完逆向 → 提取 → 模拟 → 利用。 方法论遵循 OWASP FS (file: r0/reverse-skill/skills/firmware-pentest/SKILL.md)
- frontend-slides: Create stunning, animation-rich HTML presentations from scratch or by co (file: r1/frontend-slides/SKILL.md)
- frontend-slides:frontend-slides-plugin-bridge: Compatibility entry that points nested plugin discovery to the canonica (file: r1/frontend-slides/plugins/frontend-slides/skills/frontend-slides/SKILL.md)
- geopandas: Python library for working with geospatial vector data including shapefi (file: r1/geopandas/SKILL.md)
- gmgn-market: Get crypto and meme token price charts (K-line, candlestick, OHLCV), tre (file: r1/gmgn-market/SKILL.md)
- gmgn-portfolio: Analyze any crypto wallet by address — holdings, realized/unrealized P&L (file: r1/gmgn-portfolio/SKILL.md)
- gmgn-swap: [FINANCIAL EXECUTION] Buy and sell meme coins and crypto tokens on Solan (file: r1/gmgn-swap/SKILL.md)
- gmgn-token: Research any crypto or meme token by address — real-time price, market c (file: r1/gmgn-token/SKILL.md)
- gmgn-track: Get real-time crypto buy/sell activity from Smart Money wallets, KOL influ (file: r1/gmgn-track/SKILL.md)
- gsd-ai-integration-phase: Generate AI design contract (AI-SPEC.md) for phases that involve building  (file: r0/gsd-ai-integration-phase/SKILL.md)
- gsd-code-review: Review source files changed during a phase for bugs, security issues, an (file: r0/gsd-code-review/SKILL.md)
- gsd-debug: Systematic debugging with persistent state across context resets (file: r0/gsd-debug/SKILL.md)
- gsd-execute-phase: Execute all plans in a phase with wave-based parallelization (file: r0/gsd-execute-phase/SKILL.md)
- gsd-help: Show available GSD commands and usage guide (file: r0/gsd-help/SKILL.md)
- gsd-manager: Unified router and command center for every GSD workflow. Use for milest (file: r0/gsd-manager/SKILL.md)
- gsd-map-codebase: Analyze codebase with parallel mapper agents to produce .planning/code (file: r0/gsd-map-codebase/SKILL.md)
- gsd-new-project: Initialize a new project with deep context gathering and PROJECT.md (file: r0/gsd-new-project/SKILL.md)
- gsd-plan-phase: Create detailed phase plan (PLAN.md) with verification loop (file: r0/gsd-plan-phase/SKILL.md)
- gsd-progress: Check project progress, show context, and route to next action (execut (file: r0/gsd-progress/SKILL.md)
- gsd-quick: Execute a quick task with GSD guarantees (atomic commits, state tracking (file: r0/gsd-quick/SKILL.md)
- gsd-secure-phase: Retroactively verify threat mitigations for a completed phase (file: r0/gsd-secure-phase/SKILL.md)
- gsd-ui-phase: Generate UI design contract (UI-SPEC.md) for frontend phases (file: r0/gsd-ui-phase/SKILL.md)
- gsd-verify-work: Validate built features through conversational UAT (file: r0/gsd-verify-work/SKILL.md)
- guizang-ppt-skill: 生成横向翻页网页 PPT（单 HTML 文件），含 WebGL 背景、章节幕封、数据大字报、图片网格等模板。提供两种风格：① "电子杂志 ×  (file: r1/guizang-ppt-skill/SKILL.md)
- hello_js_reverse_skill: Node.js / Python 接口自动化与签名还原工程技能：对自有平台或已授权平台的 Web API 进行签名分析与接口对接， 通过 Cam (file: r0/hello_js_reverse_skill/SKILL.md)
- hugging-science: Use when the user is doing AI/ML work in a scientific domain — biology (file: r1/hugging-science/SKILL.md)
- hypothesis-generation: Structured hypothesis formulation from observations. Use when you have e (file: r1/hypothesis-generation/SKILL.md)
- ida-reverse: IDA Pro 逆向分析辅助技能。当用户提到逆向、反编译、分析二进制/PE/ELF/APK/DLL/SO、破解、找密码、漏洞分析、病毒分析、f (file: r0/reverse-skill/skills/ida-reverse/SKILL.md)
- infographics: Create professional infographics using Nano Banana Pro AI with smart i (file: r1/infographics/SKILL.md)
- internal-comms: A set of resources to help me write all kinds of internal communicatio (file: r0/internal-comms/SKILL.md)
- keynot: Create polished, self-contained HTML slide presentations — no PowerPoint (file: r1/keynot/SKILL.md)
- lark-cli-router: Route Feishu and Lark requests through the bundled lark-cli and its versi (file: r0/dailywork/lark-connect/embedded_skills/lark-cli-router/SKILL.md)
- lark-cli-router: Route Feishu and Lark requests through the bundled lark-cli and its vers (file: r0/lark-cli-router/SKILL.md)
- lark-connect: Complete internal LeapCore Feishu authorization from an attached confide (file: r0/dailywork/lark-connect/SKILL.md)
- latex-posters: Create professional research posters in LaTeX using beamerposter, tikzpo (file: r1/latex-posters/SKILL.md)
- literature-review: Conduct comprehensive, systematic literature reviews using multiple acad (file: r1/literature-review/SKILL.md)
- literature-scout: 文献猎手 (Literature Scout) — 负责多源文献检索、筛选和分类，构建文献矩阵。 当被研究主管指派收集文献时激活。使用 Exa (file: r1/literature-scout/SKILL.md)
- llm-security: Use for defensive and authorized LLM, agent, prompt-injection, and AI app (file: r0/reverse-skill/skills/llm-security/SKILL.md)
- markdown-mermaid-writing: Comprehensive markdown and Mermaid diagram writing skill. Use when cre (file: r1/markdown-mermaid-writing/SKILL.md)
- market-research-reports: Generate comprehensive market research reports (50+ pages) in the style  (file: r1/market-research-reports/SKILL.md)
- markitdown: Convert files and office documents to Markdown. Supports PDF, DOCX, PP (file: r1/markitdown/SKILL.md)
- matlab: MATLAB and GNU Octave numerical computing for matrix operations, data  (file: r1/matlab/SKILL.md)
- matplotlib: Low-level plotting library for full customization. Use when you need f (file: r1/matplotlib/SKILL.md)
- medical-imaging-review: Write peer-review-quality comprehensive reviews for medical imaging AI (file: r1/medical-imaging-review/SKILL.md)
- nature-citation: Add strict Nature/CNS citations to manuscript text by splitting long pas (file: r0/nature-citation/SKILL.md)
- nature-data: Prepare, audit, or revise Nature-ready Data Availability statements, dat (file: r0/nature-data/SKILL.md)
- nature-figure: Submission-grade Nature/high-impact journal figure workflow for Python o (file: r0/nature-figure/SKILL.md)
- nature-paper2ppt: Build a complete but efficient Nature-style Chinese PPTX presentation  (file: r0/nature-paper2ppt/SKILL.md)
- nature-polishing: Polish, restructure, or translate academic prose into Nature-leaning E (file: r0/nature-polishing/SKILL.md)
- nature-response: Draft, audit, or revise point-by-point reviewer response letters for Nat (file: r0/nature-response/SKILL.md)
- networkx: Comprehensive toolkit for creating, analyzing, and visualizing complex (file: r1/networkx/SKILL.md)
- official-document-writing: 专业的党政机关公文写作指南。当用户需要撰写、修改或审核党政机关公文（如请示、通知、函、总结、纪要等）时使用。提供GB/T 9704-2012 (file: r1/ccp-party-building-materials/references/official-document-writing-skill/SKILL.md)
- open-notebook: Self-hosted, open-source alternative to Google NotebookLM for AI-powered (file: r1/open-notebook/SKILL.md)
- optimize-for-gpu: GPU-accelerate Python code using CuPy, Numba CUDA, Warp, cuDF, cuML, c (file: r1/optimize-for-gpu/SKILL.md)
- paper-analyst: 论文分析师 (Paper Analyst) — 负责精读论文、提取方法细节、构建对比表。 当被研究主管或文献侦查员指派分析论文时激活。对 T (file: r1/paper-analyst/SKILL.md)
- paper-lookup: Search 10 academic paper databases via REST APIs for research papers,  (file: r1/paper-lookup/SKILL.md)
- paper-slide-deck: Generate professional slide deck images from academic papers and conte (file: r1/paper-slide-deck/SKILL.md)
- parallel-web: All-in-one web toolkit powered by parallel-cli, with a strong emphasis (file: r1/parallel-web/SKILL.md)
- patch-diff-exploit: N-day 补丁差分到利用。从厂商发布的补丁里反推漏洞点、写 PoC、做成可用的攻击模块。 适用场景：已知 CVE 编号但只有补丁没有 PoC (file: r0/reverse-skill/skills/patch-diff-exploit/SKILL.md)
- pdf:pdf: Read, create, inspect, render, and verify PDF files where visual layout  (file: r4/pdf/26.813.12317/skills/pdf/SKILL.md)
- peer-review: Structured manuscript/grant review with checklist-based evaluation. Use  (file: r1/peer-review/SKILL.md)
- pentest-tools: 主动渗透测试工具链。覆盖信息收集、端口扫描、漏洞扫描、Web 渗透、SQL 注入、目录爆破、密码破解等场景。 通过 MCP server（pent (file: r0/reverse-skill/skills/pentest-tools/SKILL.md)
- playwright: Use when the task requires automating a real browser from the terminal (file: r0/playwright/SKILL.md)
- polars: Fast in-memory DataFrame library for datasets that fit in RAM. Use whe (file: r1/polars/SKILL.md)
- presentations:Presentations: Read, create or edit PowerPoint or Google Slides decks. Use for presenta (file: r4/presentations/26.813.12317/skills/presentations/SKILL.md)
- prompt-security-research: Use for defensive AI security research involving prompt injection, jai (file: r0/prompt-security-research/SKILL.md)
- pwn-chain: 从逆向走到可用利用 (Working Exploit) 的全链路工程化方法。 适用场景：拿到了二进制 + 漏洞点 + 目标环境，需要写出一个 (file: r0/reverse-skill/skills/pwn-chain/SKILL.md)
- pymc: Bayesian modeling with PyMC. Build hierarchical models, MCMC (NUTS), v (file: r1/pymc/SKILL.md)
- pymoo: Multi-objective optimization framework. NSGA-II, NSGA-III, MOEA/D, Paret (file: r1/pymoo/SKILL.md)
- python-crawler: Build, audit, debug, or extend resilient Python web crawlers and scrap (file: r0/python-crawler/SKILL.md)
- pytorch-lightning: Deep learning framework (PyTorch Lightning). Organize PyTorch code into  (file: r1/pytorch-lightning/SKILL.md)
- pyzotero: Interact with Zotero reference management libraries using the pyzotero (file: r1/pyzotero/SKILL.md)
- quality-editor: 质量编辑 (Quality Editor) — 负责综述论文的术语一致性、引用完整性和 整体质量审校。当被研究主管或论文撰写员指派审校时激活 (file: r1/quality-editor/SKILL.md)
- radare2: Use this skill whenever the user wants to analyze binaries with radare2 (file: r0/reverse-skill/skills/radare2/SKILL.md)
- research-grants: Write competitive research proposals for NSF, NIH, DOE, DARPA, and Taiwa (file: r1/research-grants/SKILL.md)
- research-proposal: Generate academic research proposals for PhD applications. Use when user (file: r1/research-proposal/SKILL.md)
- reverse-engineering: Provides reverse engineering techniques. Use when the main job is to un (file: r0/reverse-skill/skills/reverse-engineering/SKILL.md)
- reverse-skill: Route authorized reverse-engineering, security-analysis, CTF, APK, binar (file: r0/reverse-skill/SKILL.md)
- scholar-evaluation: Systematically evaluate scholarly work using the ScholarEval framework (file: r1/scholar-evaluation/SKILL.md)
- scientific-brainstorming: Creative research ideation and exploration. Use for open-ended brainst (file: r1/scientific-brainstorming/SKILL.md)
- scientific-critical-thinking: Evaluate scientific claims and evidence quality. Use for assessing exp (file: r1/scientific-critical-thinking/SKILL.md)
- scientific-schematics: Create publication-quality scientific diagrams using Nano Banana 2 AI wi (file: r1/scientific-schematics/SKILL.md)
- scientific-slides: Build slide decks and presentations for research talks. Use this for mak (file: r1/scientific-slides/SKILL.md)
- scientific-visualization: Meta-skill for publication-ready figures. Use when creating journal su (file: r1/scientific-visualization/SKILL.md)
- scientific-writing: Core skill for the deep research and writing tool. Write scientific ma (file: r1/scientific-writing/SKILL.md)
- scikit-learn: Machine learning in Python with scikit-learn. Use when working with su (file: r1/scikit-learn/SKILL.md)
- seaborn: Statistical visualization with pandas integration. Use for quick explora (file: r1/seaborn/SKILL.md)
- shap: Model interpretability and explainability using SHAP (SHapley Additive (file: r1/shap/SKILL.md)
- simpy: Process-based discrete-event simulation framework in Python. Use this sk (file: r1/simpy/SKILL.md)
- sora: Use when the user asks to generate, edit, extend, poll, list, download (file: r0/sora/SKILL.md)
- spreadsheets:Spreadsheets: Create, edit, analyze, and verify standalone spreadsheet files or Google  (file: r5/spreadsheets/SKILL.md)
- spreadsheets:excel-live-control: Control an open or active Microsoft Excel workbook through the ChatGPT ad (file: r5/excel-live-control/SKILL.md)
- src-hunter: 实战 SRC / 众测 / Bug bounty 漏洞挖掘工作流 skill。包含：5 阶段方法论（intake → recon → enum  (file: r0/reverse-skill/skills/pentest-tools/src-hunter/SKILL.md)
- stable-baselines3: Production-ready reinforcement learning algorithms (PPO, SAC, DQN, TD3,  (file: r1/stable-baselines3/SKILL.md)
- statistical-analysis: Guided statistical analysis with test selection and reporting. Use whe (file: r1/statistical-analysis/SKILL.md)
- statsmodels: Statistical models library for Python. Use when you need specific model  (file: r1/statsmodels/SKILL.md)
- superpowers:brainstorming: You MUST use this before any creative work - creating features, building (file: r1/superpowers/brainstorming/SKILL.md)
- superpowers:dispatching-parallel-agents: Use when facing 2+ independent tasks that can be worked on without share (file: r1/superpowers/dispatching-parallel-agents/SKILL.md)
- superpowers:executing-plans: Use when you have a written implementation plan to execute in a separate (file: r1/superpowers/executing-plans/SKILL.md)
- superpowers:finishing-a-development-branch: Use when implementation is complete, all tests pass, and you need to d (file: r1/superpowers/finishing-a-development-branch/SKILL.md)
- superpowers:receiving-code-review: Use when receiving code review feedback, before implementing suggestions (file: r1/superpowers/receiving-code-review/SKILL.md)
- superpowers:requesting-code-review: Use when completing tasks, implementing major features, or before merg (file: r1/superpowers/requesting-code-review/SKILL.md)
- superpowers:subagent-driven-development: Use when executing implementation plans with independent tasks in the cu (file: r1/superpowers/subagent-driven-development/SKILL.md)
- superpowers:systematic-debugging: Use when encountering any bug, test failure, or unexpected behavior, b (file: r1/superpowers/systematic-debugging/SKILL.md)
- superpowers:test-driven-development: Use when implementing any feature or bugfix, before writing implementati (file: r1/superpowers/test-driven-development/SKILL.md)
- superpowers:using-git-worktrees: Use when starting feature work that needs isolation from current workspa (file: r1/superpowers/using-git-worktrees/SKILL.md)
- superpowers:using-superpowers: Use when starting any conversation - establishes how to find and use ski (file: r1/superpowers/using-superpowers/SKILL.md)
- superpowers:verification-before-completion: Use when about to claim work is complete, fixed, or passing, before co (file: r1/superpowers/verification-before-completion/SKILL.md)
- superpowers:writing-plans: Use when you have a spec or requirements for a multi-step task, before t (file: r1/superpowers/writing-plans/SKILL.md)
- superpowers:writing-skills: Use when creating new skills, editing existing skills, or verifying sk (file: r1/superpowers/writing-skills/SKILL.md)
- survey-director: 综述总监 (Survey Director) — 负责AI/ML前沿综述论文的选题规划、大纲设计、 任务分配与终审。当用户提出综述写作需求时 (file: r1/survey-director/SKILL.md)
- survey-writer: 综述写手 (Survey Writer) — 负责按模板撰写综述论文各章节。 当被研究主管或论文分析师指派写作时激活。基于论文分析卡片和对比 (file: r1/survey-writer/SKILL.md)
- sympy: Use this skill when working with symbolic mathematics in Python. This sk (file: r1/sympy/SKILL.md)
- template-creator:template-creator: Create or update a reusable personal Codex artifact-template skill. Use  (file: r4/template-creator/26.813.12317/skills/template-creator/SKILL.md)
- timesfm-forecasting: Zero-shot time series forecasting with Google's TimesFM foundation model (file: r1/timesfm-forecasting/SKILL.md)
- torch-geometric: Guide for building Graph Neural Networks with PyTorch Geometric (PyG). U (file: r1/torch-geometric/SKILL.md)
- transformers: This skill should be used when working with pre-trained transformer mo (file: r1/transformers/SKILL.md)
- umap-learn: UMAP dimensionality reduction. Fast nonlinear manifold learning for 2D (file: r1/umap-learn/SKILL.md)
- vaex: Use this skill for processing and analyzing large tabular datasets (bi (file: r1/vaex/SKILL.md)
- venue-templates: Access comprehensive LaTeX templates, formatting requirements, and submi (file: r1/venue-templates/SKILL.md)
- vercel-deploy: Deploy applications and websites to Vercel. Use when the user requests d (file: r0/vercel-deploy/SKILL.md)
- vision-multimodal: Vision and multimodal capabilities for Claude including image analysis,  (file: r1/vision-multimodal/SKILL.md)
- visualize:visualize: Create visualizations and interactive tools directly in conversation.  (file: r3/visualize/1.0.21/skills/visualize/SKILL.md)
- zarr-python: Chunked N-D arrays for cloud storage. Compressed arrays, parallel I/O, S (file: r1/zarr-python/SKILL.md)
</skills_instructions><permissions instructions>
Filesystem sandboxing defines which files can be read or written. `sandbox_mode` is `read-only`: The sandbox only permits reading files. Network access is restricted.
Approval policy is currently never. Do not provide the `sandbox_permissions` for any reason, commands will be rejected.
</permissions instructions><plugins_instructions>
## Plugins
A plugin is a local bundle of skills, MCP servers, and apps.
### How to use plugins
- Skill naming: If a plugin contributes skills, those skill entries are prefixed with `plugin_name:` in the Skills list.
- MCP naming: Plugin-provided MCP tools keep standard MCP identifiers such as `mcp__server__tool`; use tool provenance to tell which plugin they come from.
- Trigger rules: If the user explicitly names a plugin, prefer capabilities associated with that plugin for that turn.
- Relationship to capabilities: Plugins are not invoked directly. Use their underlying skills, MCP tools, and app tools to help solve the task.
- Relevance: Determine what a plugin can help with from explicit user mention or from the plugin-associated skills, MCP tools, and apps exposed elsewhere in this turn.
- Missing/blocked: If the user requests a plugin that does not have relevant callable capabilities for the task, say so briefly and continue with the best fallback.
</plugins_instructions>

---


**OpenAI Codex**

You are `/root`, the primary agent in a team of agents collaborating to fulfill the user's goals.

At the start of your turn, you are the active agent.
You can spawn sub-agents to handle subtasks, and those sub-agents can spawn their own sub-agents.
All agents in the team, including the agents that you can assign tasks to, are equally intelligent and capable, and have access to the same set of tools.

You can use `spawn_agent` to create a new agent, `followup_task` to give an existing agent a new task and trigger a turn, and `send_message` to pass a message to a running agent without triggering a turn.
Child agents can also spawn their own sub-agents.
You can decide how much context you want to propagate to your sub-agents with the `fork_turns` parameter.

You will receive messages in the analysis channel in the form:
```
Message Type: MESSAGE | FINAL_ANSWER
Task name: <recipient>
Sender: <author>
Payload:
<payload text>
```
They may be addressed as to=/root

Note that collaboration tools cannot be called from inside `functions.exec`. Call `spawn_agent`, `send_message`, `followup_task`, `wait_agent`, `interrupt_agent`, and `list_agents` only as direct tool calls using the recipient shown in their tool definitions, such as `to=functions.collaboration.spawn_agent`, since they are intentionally absent from the `functions.exec` `tools.*` namespace. Available tools in `functions.exec` are explicitly described with a `tools` namespace in the developer message.

All agents share the same directory. In detail:
- All agents have access to the same container and filesystem as you.
- All agents use the same current working directory.
- As a result, edits made by one agent are immediately visible to all other agents.

When calling `wait_agent`, prefer longer waits (minutes) to avoid busy polling.

There are 4 available concurrency slots, meaning that up to 4 agents can be active at once, including you.

Full-history forks (`fork_turns` omitted or `"all"`) inherit the parent model and reasoning effort and do not accept overrides. Only set `model` or `reasoning_effort` when explicitly requested by the user, applicable `AGENTS.md` instructions, or skill instructions; when doing so, set `fork_turns` to `"none"` or a positive integer string.

---


**OpenAI Codex**

<multi_agent_mode>Any earlier instruction enabling proactive multi-agent delegation no longer applies. Do not spawn sub-agents unless the user or applicable AGENTS.md/skill instructions explicitly ask for sub-agents, delegation, or parallel agent work.</multi_agent_mode>

---


**User**

Reply exactly OK

---


**OpenAI Codex**

OK

---
