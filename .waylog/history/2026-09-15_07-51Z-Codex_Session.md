# Codex Session
_Exported on 09/16/2026 at 21:18:58 GMT+8 from OpenAI Codex via WayLog_


**OpenAI Codex**

<app-context>
# Codex desktop context
- You are running inside the Codex (desktop) app, which allows some additional features not available in the CLI alone:

### Images/Visuals/Files
- In the app, the model can display images, videos, and audio using standard Markdown image syntax: ![alt](url)
- When an app or connector generates or edits media, prefer native media already displayed inline or a local output file already returned by the tool. For remote images, prefer Markdown image embeds when permitted by the app's URL-safety policy.
- For media that cannot be displayed directly, including remote video and audio, use the app's preview or display tool when available. Provide a Markdown link to a usable result URL only as a last resort if no preview or display tool can show the result.
- Do not download remote media to work around display restrictions.
- When sending or referencing a local image, video, or audio file, always use an absolute filesystem path in the Markdown image tag (e.g., ![alt](/absolute/path.png)); relative paths and plain text will not render the media.
- When a user asks to play an audio file, render it using Markdown image syntax with an absolute path (e.g., ![audio](/absolute/path.mp3)).
- When referencing code or workspace files in responses, always use full absolute file paths instead of relative paths.
- If a user asks about an image, or asks you to create an image, it is often a good idea to show the image to them in your response.
- Return web URLs as Markdown links (e.g., [label](https://example.com)).

### Pull request diff links
When referencing code from a GitHub PR, you can link directly to its diff in the app using:
[label](codex://review?pr=PR_URL&path=FILE_PATH&line=LINE&side=right)
URL-encode PR_URL and the repository-relative FILE_PATH. Use a verified one-based LINE from the current PR diff. Use side=left for the original code or side=right for the updated code. Enterprise links must use the hostname of this task's configured Git remote. Use ordinary file links for workspace code.

### Workspace Dependencies
- For sheets, slides, and documents, call `load_workspace_dependencies` to find the bundled runtime and libraries.

### Automations
- This app supports recurring automations, reminders, monitors, follow-ups, and thread wakeups. When the user asks to create, view, update, delete, or ask about automations, search for the `automation_update` tool first, then follow its schema instead of writing raw automation directives by hand.
- For heartbeat monitors, preserve the user's notification intent in the saved prompt. Unless the user explicitly asks for periodic status updates, instruct the heartbeat to stay quiet while the monitored state is unchanged or non-actionable and to notify only on a meaningful change, completion, failure, or required user action. Do not add instructions such as "leave a brief status update" on every run.
- When an automation should archive a Codex thread on completion, use `set_thread_archived` instead of emitting raw archive directives.

### Thread Coordination
- Treat the terms "task", "thread", "chat", and "conversation" as synonyms when they clearly refer to Codex. Tool names use the term "thread" and Codex uses "task" in the UI. When providing user-facing responses, use "task".
- When the user asks to create, fork, inspect, continue, hand off, pin, archive, unarchive, rename, or otherwise manage Codex threads, search for the relevant thread tool first: `create_thread`, `fork_thread`, `list_threads`, `list_archived_threads`, `read_thread`, `wait_threads`, `send_message_to_thread`, `handoff_thread`, `set_thread_pinned`, `set_thread_archived`, or `set_thread_title`.
- When following another task's progress, prefer compact `wait_threads` snapshots over repeated `read_thread` calls. Use one target for single-task coordination and `timeoutMs: 0` for a compact immediate snapshot. `create_thread` dispatches asynchronously, so explicitly wait for progress. Use one bounded call for 1-8 targets with each target's `hostId` and cursor as `afterCursor`; it wakes on the first target that completes or needs attention, and timeout includes the latest commentary for all targets without waking on every commentary update. An up-to-date cursor suppresses already-delivered final text. Separate waits from one task may run serially. Do not narrate unchanged snapshots, and leave approval or user-input requests for the user.
- Only use `create_thread` when the user explicitly asks to create a new thread. Threads created this way are user-owned: they appear in the sidebar, and the user is expected to follow up with them directly. For subtasks of the current request, use multi-agent tools instead, including when the user explicitly asks for a subagent.
- After a successful `create_thread` call, emit `::created-thread{threadId="..."}` for a created thread or `::created-thread{clientThreadId="..."}` for queued worktree setup on its own line in your final response.

### Inline Code Comments
- Use the ::code-comment{...} directive when you need to attach feedback directly to specific code lines.
- Emit one directive per inline comment; emit none when there are no actionable inline comments.
- Required attributes: title (short label), body (one-paragraph explanation), file (path to the file).
- Optional attributes: start, end (1-based line numbers), priority (0-3).
- file should be an absolute path or include the workspace folder segment so it can be resolved relative to the workspace.
- Keep line ranges tight; end defaults to start.
- Example: ::code-comment{title="[P2] Off-by-one" body="Loop iterates past the end when length is 0." file="/path/to/foo.ts" start=10 end=11 priority=2}

### Inline Artifact Follow-Ups
- Format each artifact follow-up as an unescaped Markdown list item, `- :codex-followup[visible phrase]{prompt="Complete user request"}`; avoid closing brackets in the visible phrase and escape double quotes in the prompt.

### Git
- Branch prefix: `codex/`. Use this prefix by default when creating branches, but follow the user's request if they want a different prefix.
</app-context>## Memory

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

Windows-based developer who uses Codex for Facebook crawler/emulator tooling, Leap repositories, MCP and browser/network diagnostics, Gitea deployments, and evidence-led documentation. Usually writes requests in Chinese. Values exact evidence, preservation of active services, account/login state, credentials, and historical facts, plus a concrete verified outcome rather than generic troubleshooting. Uses checkout-specific projects, so cwd is a lead rather than proof of the target. Expects isolated cross-platform layers and explicit separation of hosted CI/Windows simulation from real-machine evidence.

## User preferences

- Make authorized repairs directly, minimally, and reversibly; verify the user-visible or persisted result.
- Before editing, deleting, syncing, or restarting, inspect authoritative paths and live state; preserve unrelated work, credentials, active batches/services, login/crawler state, and protected data.
- For an "整体项目" audit or cleanup, map structure, entrypoints, config, tests, outputs, dependencies, and risks first; act only on high-confidence targets.
- Report partial, blocked, and unverified states precisely; separate code/tests, dated run artifacts, and current live availability.
- Preserve historical facts when correcting documentation; verify claimed edits from actual diffs and files, not plans.
- For Facebook, distinguish `initial-ready`, `partial/open-tail`, and `terminal-complete`; first-screen users or HTTP success are not complete pagination.
- `"返回empty list就行"`: known visibility-limited but verified/clean Facebook targets return `reaction_users: []`; retain real transport, target, parser, rate-limit, and incomplete-sample failures.
- Facebook dedupe is within a post only: preserve users across posts and use `job_id`, not `source_post_id`, as the post boundary.
- `"先不用改，先看看"` / `"只看"` means a read-only review until concrete edits are approved.
- When a configured model conflicts with Desktop UI, audit defaults, explicit spawn arguments, and final child settings; request Luna/max explicitly and reject/respawn downgraded children.

## General Tips

- Use bounded `rg` searches; verify repository identity, actual paths, and live process/config state before edits. `git diff --check` and ahead/behind do not prove runtime tests.
- On Windows, prefer UTF-8-safe tooling, Python `tomllib`, and shallow PowerShell expressions; disk config, loaded UI/session state, and runtime output are distinct evidence.
- Keep secrets out of reports and memory. Browser authorization, passwords, OTP, CAPTCHA, and final account approval remain user actions.
- Test the consuming browser/profile or exact API route, not only a listener or provider transport.
- For Facebook jobs, inspect manifests, logs, diagnostics, terminal state, strict target match, and artifact integrity; preserve rate-limited CSV/evidence as partial output.
- During CI/runbook audits, compare released test files with the workflow command. A green job does not cover an omitted safety suite; inspect `prepare_device.py` JSON/status even when examples use `|| true`.

## What's in Memory

### C:\Users\lenovo\Desktop\facebook

#### 2026-09-14

- macOS deployment documentation and CI review: `CODEX_DEPLOYMENT_RUNBOOK.md`, `34772974833`, `test_ui_v2_and_safety.py`, `web/index.html`, `89fe24d7`, `74a0a765`, `prepare_device.py`, `primary-standby`
  - desc: Read-only audit of macOS deployment claims, workflow coverage, and safety checks in `cwd=C:\Users\lenovo\Desktop\facebook`.
  - learnings: Hosted macOS/Windows CI passed 273 tests but omitted the safety suite, whose `web/index.html` baseline failed locally. Do not claim real-Mac acceptance or suite success.

#### 2026-09-13

- Facebook crawler runtime and post-scoped aggregation: `reaction_users: []`, `query-not-triggered`, `job_id`, `source_post_id`, `START.cmd`, `START_API_AND_EMULATOR.cmd`, `8765`, `8766`, `119 tests`
  - desc: Reaction-visibility result handling, batch aggregation, and non-disruptive Windows startup/runtime cleanup for `cwd=C:\Users\lenovo\Desktop\facebook`.
  - learnings: Guard empty results, aggregate `(job_id, user)` including checkpoints, and recheck listener ownership immediately before restarts or targeted cleanup.

### C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct

#### 2026-09-12

- Cloudflare CLI multi-account authentication: `cloudflared`, `multi-account`, `cert.pem`, `nameservers`, `codexpro.swglwt.kdns.fr`, `"挤掉这个账户"`
  - desc: Preserve an existing Cloudflare account/domain/Tunnel before a second login in `cwd=C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct`.
  - learnings: Guidance only: inspect CLI/config/account identity before selecting an isolation approach or advising DNS changes.

### Older Memory Topics

#### C:\Users\lenovo\Desktop\facebook

- documentation and runtime-evidence alignment: `CURRENT_STATUS_2026-09-09.md`, `initial-ready`, `terminal-complete`, `reaction-full-smoke-20260902-120331`, `5f7d946`
  - desc: Release/documentation status and historical-versus-live runtime evidence; preserve pagination terminology.
- macOS worker adapter: `macos/facebook_macos.py`, `MacWorker`, `executed: false`, `deep_capture_runner: not-implemented-for-macos`
  - desc: Isolated macOS bootstrap/transport layer; Windows simulation is not real-Mac deep-capture proof.
- generic capture, Worker-02, and presentation console: `GraphQL 1675004`, `worker-02`, `POST /v1/workers/worker-02/enable`, `batch-summary.json`
  - desc: Arbitrary URL capture, rate-limit partial-result labeling, second-emulator isolation, and local console evidence.
- capture handoff and restriction assessment: `ReactionProbeOnly`, `placeholder-or-unknown`, `ResharesOfContentFeedQuery`, `canonical permalink`
  - desc: Emulator/proxy recovery, strict target validation, and safe non-login capture boundary. [ad-hoc note]

#### C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct

- Luna Max effective-state audit: `gpt-5.6-luna`, `reasoning_effort="max"`, `fork_turns="none"`, `thread_settings_applied`, `luna_max_runtime_guard.py`
  - desc: Verify actual child-thread state when Desktop UI conflicts with configured Luna Max.
- CodexPro, OAuth, API, and browser/proxy diagnostics: `CODEXPRO_READY`, `Token exchange failed: 403`, `opencode auth list`, `models_base_url`, `ERR_SOCKS_CONNECTION_FAILED`, `AdsPower`, `Clash`
  - desc: Browser readiness, local OAuth repair, custom API/TUI state, and consumer-profile routing.
- IELTS, spreadsheet, and project/disk audits: `IELTS.org`, `OOXML`, `sheet7.xml.rels`, `WinError 1314`, `DiagOutputDir`
  - desc: Source-backed reading help, target-only OOXML edits, and conservative Windows triage.

#### C:\Users\lenovo\Desktop\leap and C:\Users\lenovo\Desktop\leap\DRV研发\LeapAgent

- repository synchronization and DRV validation: `git fetch origin --prune`, `VERIFY_FAILURES=0`, `origin/main`, `git merge --ff-only`, `ModeShadow`, `drv-v2-submit`
  - desc: Nested-checkout synchronization, remote reconciliation, and narrow DRV assessment/SQLite shadow validation.

#### C:\Users\lenovo\Desktop\外包+岗位爬虫

- crawler audits, BOSS normalization, DRV deployment, and EPWK enrichment: `drv_crawler`, `boss_cdp`, `PYTHONUTF8=1`, `taoduo/drv-market-crawler`, `git fsck --full`, `EpwkAdapter`
  - desc: Diagnosis-first audits, safe derived exports, Gitea clean-clone portability, and incremental detail-field quality.

#### C:\Users\lenovo\Desktop\webcodex and C:\Users\lenovo\Desktop\破限\test-mcp

- Soft Workspace V1 and Coding Tools MCP OAuth repair: `ProjectResolver`, `Access Scope`, `cargo check`, `Unknown client_id`, `run-mcp-service-hidden.cmd`, `Authorize Coding Tools MCP`
  - desc: Workspace context/authorization routing plus React/Vite inspection and OAuth client-registration recovery.

#### C:\Users\lenovo\.codex and Windows host

- Codex Desktop runtime safety and disk/migration triage: `codex_capability_switch.ps1`, `mcp_servers`, `C:\$Recycle.Bin`, `ext4.vhdx`, `D:\DesktopData`
  - desc: Reversible MCP capability changes, process/log safety, protected state-bearing data, and measured cleanup/migration rules.
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
- `r5` = `C:/Users/lenovo/.codex/plugins/cache/openai-primary-runtime/spreadsheets/26.909.12148/skills`
### Available skills
- imagegen: Generate or edit raster images when the task benefits from AI-created bitmap visuals such as photos, illustrations, textures, sprites, mockups, or transparent-background cutouts. Use when Codex should create a brand-new image, transform an existing image, or derive visual variants from references, and the output should be a bitmap asset rather than repo-native code or vector. Do not use when the task is better handled by editing existing SVG/vector/code-native assets, extending an established icon or logo system, or building the visual directly in HTML/CSS/canvas. (file: r2/imagegen/SKILL.md)
- openai-docs: Use for Codex models/pricing, scheduled tasks, skills, settings, setup, troubleshooting, customization, automations, and self-knowledge—including 'you,' 'your,' 'this app,' or 'this coding agent' when they refer to Codex—and for OpenAI APIs/products and ChatGPT Work. Also use for model choice/migration, prompting, SDKs, Responses, Realtime, agents, evals, and Chat/Work/Codex comparisons. Do not use for generic app/software tasks that merely mention Codex. (file: r2/openai-docs/SKILL.md)
- plugin-creator: Create and scaffold plugin directories for Codex with a required `.codex-plugin/plugin.json`, optional plugin folders/files, valid manifest defaults, and personal-marketplace entries by default. Use when Codex needs to create a new personal plugin, add optional plugin structure, generate or update marketplace entries for plugin ordering and availability metadata, or update an existing local plugin during development with the CLI-driven cachebuster and reinstall flow. (file: r2/plugin-creator/SKILL.md)
- skill-creator: Create or update a Codex skill with appropriately scoped instructions and any needed supporting resources. (file: r2/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into $CODEX_HOME/skills from a curated list or a GitHub repo path. Use when a user asks to list installable skills, install a curated skill, or install a skill from another repo (including private repos). (file: r2/skill-installer/SKILL.md)
- computer-use:computer-use: Control Windows apps from ChatGPT (file: r3/computer-use/26.908.70816/skills/computer-use/SKILL.md)
- dailywork: Generate and submit DailyWork personal or department daily reports from locally authorized Feishu activity, personal weekly reports from the caller's confirmed historical daily reports, and department weekly reports from confirmed personal weekly reports. Use when a user asks Codex to write, submit, or push a DailyWork daily/weekly report or its Feishu confirmation card. (file: r0/dailywork/SKILL.md)
- documents:documents: Create, edit, redline, and comment on `.docx`, Word, and Google Docs-targeted document artifacts inside the container, with a strict render-and-verify workflow. Use `render_docx.py` to generate page PNGs (and optional PDF) for visual QA, then iterate until layout is flawless before delivering the final document. (file: r4/documents/26.909.12148/skills/documents/SKILL.md)
- lark-cli-router: Route Feishu and Lark requests through the bundled lark-cli and its version-matched embedded Skills. Use for messages, documents, drive, Base, calendar, tasks, approvals, contacts, meetings, wiki, sheets, slides, mail, OKR, attendance, events, and Lark authorization or permission issues. (file: r0/lark-cli-router/SKILL.md)
- literature-review: Conduct comprehensive, systematic literature reviews using multiple academic databases (PubMed, arXiv, bioRxiv, Semantic Scholar, etc.). This skill should be used when conducting systematic literature reviews, meta-analyses, research synthesis, or comprehensive literature searches across biomedical, scientific, and technical domains. Creates professionally formatted markdown documents and PDFs with verified citations in multiple citation styles (APA, Nature, Vancouver, etc.). (file: r1/literature-review/SKILL.md)
- matplotlib: Low-level plotting library for full customization. Use when you need fine-grained control over every plot element, creating novel plot types, or integrating with specific scientific workflows. Export to PNG/PDF/SVG for publication. For quick statistical plots use seaborn; for interactive plots use plotly; for publication-ready multi-panel figures with journal styling, use scientific-visualization. (file: r1/matplotlib/SKILL.md)
- nature-paper2ppt: Build a complete but efficient Nature-style Chinese PPTX presentation from a scientific paper, preprint, PDF, article text, abstract, figure legends, or reading notes. Use this skill whenever the user asks to make slides/PPT/PPTX for journal club, group meeting, paper sharing, thesis seminar, lab meeting, department report, or academic presentation from a research paper, not only medical papers. It identifies the paper type and argument, selects only the figures needed for the story, writes Chinese slide content and speaker notes, creates the actual .pptx deck, and performs lightweight verification with cross-platform Python tooling by default. (file: r0/nature-paper2ppt/SKILL.md)
- networkx: Comprehensive toolkit for creating, analyzing, and visualizing complex networks and graphs in Python. Use when working with network/graph data structures, analyzing relationships between entities, computing graph algorithms (shortest paths, centrality, clustering), detecting communities, generating synthetic networks, or visualizing network topologies. Applicable to social networks, biological networks, transportation systems, citation networks, and any domain involving pairwise relationships. (file: r1/networkx/SKILL.md)
- paper-lookup: Search 10 academic paper databases via REST APIs for research papers, preprints, and scholarly articles. Covers PubMed, PMC (full text), bioRxiv, medRxiv, arXiv, OpenAlex, Crossref, Semantic Scholar, CORE, Unpaywall. Use when searching for papers, citations, DOI/PMID lookups, abstracts, full text, open access, preprints, citation graphs, author search, or any scholarly literature query. Triggers on mentions of any supported database or requests like "find papers on X" or "look up this DOI". (file: r1/paper-lookup/SKILL.md)
- pdf:pdf: Read, create, inspect, render, and verify PDF files where visual layout matters, including fillable AcroForms. Use Poppler rendering plus Python tools such as reportlab, pdfplumber, and pypdf for generation and extraction. (file: r4/pdf/26.909.12148/skills/pdf/SKILL.md)
- presentations:Presentations: Read, create or edit PowerPoint or Google Slides decks. Use for presentation, slide deck, PowerPoint, PPT, PPTX, or Google Slides requests. (file: r4/presentations/26.909.12148/skills/presentations/SKILL.md)
- python-crawler: Build, audit, debug, or extend resilient Python web crawlers and scraping systems. Use for crawler, spider, scraping, website or page collection, static requests, Playwright rendering, Cookie or storage-state reuse, pagination, API discovery, Scrapy scale-out, checkpointing, deduplication, validation, and crawler recovery work. (file: r0/python-crawler/SKILL.md)
- research-proposal: Generate academic research proposals for PhD applications. Use when user asks to "write a research proposal", "create PhD proposal", "generate research plan", "撰写研究计划", "写博士申请", "doctoral proposal", or mentions specific research topics for PhD application. Supports STEM, humanities, and social sciences with field-specific adaptations. Follows Nature Reviews-style academic writing conventions. Supports both English and Chinese output based on user preference. (file: r1/research-proposal/SKILL.md)
- reverse-skill: Route authorized reverse-engineering, security-analysis, CTF, APK, binary, JavaScript-signature, firmware, malware-analysis, API-security, and related tool-assisted tasks through the installed zhaoxuya520/reverse-skill package. Use when the user mentions reverse engineering, JS reverse/signature parameters, APK/IPA, IDA, radare2, Frida, CTF, pwn, packet capture, firmware, malware triage, API security, or security report generation. (file: r0/reverse-skill/SKILL.md)
- scientific-critical-thinking: Evaluate scientific claims and evidence quality. Use for assessing experimental design validity, identifying biases and confounders, applying evidence grading frameworks (GRADE, Cochrane Risk of Bias), or teaching critical analysis. Best for understanding evidence quality, identifying flaws. For formal peer review writing use peer-review. (file: r1/scientific-critical-thinking/SKILL.md)
- scientific-visualization: Meta-skill for publication-ready figures. Use when creating journal submission figures requiring multi-panel layouts, significance annotations, error bars, colorblind-safe palettes, and specific journal formatting (Nature, Science, Cell). Orchestrates matplotlib/seaborn/plotly with publication styles. For quick exploration use seaborn or plotly directly. (file: r1/scientific-visualization/SKILL.md)
- scientific-writing: Core skill for the deep research and writing tool. Write scientific manuscripts in full paragraphs (never bullet points). Use two-stage process with (1) section outlines with key points using research-lookup then (2) convert to flowing prose. IMRAD structure, citations (APA/AMA/Vancouver), figures/tables, reporting guidelines (CONSORT/STROBE/PRISMA), for research papers and journal submissions. (file: r1/scientific-writing/SKILL.md)
- scikit-learn: Machine learning in Python with scikit-learn. Use when working with supervised learning (classification, regression), unsupervised learning (clustering, dimensionality reduction), model evaluation, hyperparameter tuning, preprocessing, or building ML pipelines. Provides comprehensive reference documentation for algorithms, preprocessing techniques, pipelines, and best practices. (file: r1/scikit-learn/SKILL.md)
- shap: Model interpretability and explainability using SHAP (SHapley Additive exPlanations). Use this skill when explaining machine learning model predictions, computing feature importance, generating SHAP plots (waterfall, beeswarm, bar, scatter, force, heatmap), debugging models, analyzing model bias or fairness, comparing models, or implementing explainable AI. Works with tree-based models (XGBoost, LightGBM, Random Forest), deep learning (TensorFlow, PyTorch), linear models, and any black-box model. (file: r1/shap/SKILL.md)
- skill-library-router: Route requests to locally installed specialist skills that are intentionally hidden from the global startup catalog to reduce Codex context. Use when the user names a local skill that is not in Available skills, asks for a specialist workflow, or an active skill says to delegate to another installed skill. (file: r0/skill-library-router/SKILL.md)
- spreadsheets:Spreadsheets: Use skill when user requests to create, modify, analyze, visualize, or work with spreadsheet files (`.xlsx`, `.xls`, `.csv`, `.tsv`) or Google Sheets with formulas, formatting, charts, tables, and recalculation. Do not use for live controlling Microsoft Excel app or a live Excel session. (file: r5/spreadsheets/SKILL.md)
- spreadsheets:excel-live-control: Control an open or active Microsoft Excel workbook through the ChatGPT add-in or connected session. Use when the user tags the Microsoft Excel app in Codex or follows up on an established live Excel task. Do not use for standalone spreadsheet files or Google Sheets. (file: r5/excel-live-control/SKILL.md)
- statistical-analysis: Guided statistical analysis with test selection and reporting. Use when you need help choosing appropriate tests for your data, assumption checking, power analysis, and APA-formatted results. Best for academic research reporting, test selection guidance. For implementing specific models programmatically use statsmodels. (file: r1/statistical-analysis/SKILL.md)
- statsmodels: Statistical models library for Python. Use when you need specific model classes (OLS, GLM, mixed models, ARIMA) with detailed diagnostics, residuals, and inference. Best for econometrics, time series, rigorous inference with coefficient tables. For guided statistical test selection with APA reporting use statistical-analysis. (file: r1/statsmodels/SKILL.md)
- study-abroad-advisor: Use when planning or preparing study-abroad applications, including graduate or taught-master school selection, program-fit analysis, CV/resume, SOP/PS, recommendation strategy, application portfolio, interview prep, offer comparison, timelines, or cross-document consistency checks. (file: r0/study-abroad-advisor/SKILL.md)
- superpowers:systematic-debugging: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes (file: r1/superpowers/systematic-debugging/SKILL.md)
- superpowers:verification-before-completion: Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions always (file: r1/superpowers/verification-before-completion/SKILL.md)
- superpowers:writing-plans: Use when you have a spec or requirements for a multi-step task, before touching code (file: r1/superpowers/writing-plans/SKILL.md)
- template-creator:template-creator: Create or update a reusable personal Codex artifact-template skill. Use when the user invokes $template-creator or asks in natural language to create a reusable template from a reference document, presentation, spreadsheet, Google Docs, Slides, or Sheets link, ImageGen or Product Design image, email, Slack message, or Site project, or explicitly asks to edit or update a passed artifact-template skill. Do not use for one-off creation from an existing template. (file: r4/template-creator/26.909.12148/skills/template-creator/SKILL.md)
- visualize:visualize: Create visualizations and interactive tools directly in conversation. Proactively use to show how something works; explore 'what happens when', 'what changes', or 'help me understand'; compare or inspect; create simulations, maps, charts, graphs, and mockups. Use standard tools for static scientific figures. (file: r3/visualize/1.0.37/skills/visualize/SKILL.md)
</skills_instructions><permissions instructions>
Filesystem sandboxing defines which files can be read or written. `sandbox_mode` is `danger-full-access`: No filesystem sandboxing - all commands are permitted. Network access is enabled.
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

You are an agent in a team of agents collaborating to complete a task.

You can spawn sub-agents to handle subtasks, and those sub-agents can spawn their own sub-agents. All agents in the team, including the agents that you can assign tasks to, are equally intelligent and capable, and have access to the same set of tools.

You can use `spawn_agent` to create a new agent, `followup_task` to give an existing agent a new task and trigger a turn, and `send_message` to pass a message to a running agent.
Child agents can also spawn their own sub-agents.

When you provide a response in the final channel, that content is immediately delivered back to your parent agent.

You will receive messages in the analysis channel in the form:
```
Message Type: NEW_TASK | MESSAGE | FINAL_ANSWER
Task name: <recipient>
Sender: <author>
Payload:
<payload text>
```
You may also see them addressed as to=/root/..., which indicates your identity is /root/...

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


**OpenAI Codex**

我会按只读诊断执行：先确认 RTK/项目边界与当前差异，再锁定指定 URL 最近失败运行的阶段时间，最后交叉检查启动脚本、transport/runtime、selector/helper 与测试，输出带行号的根因和最小修复建议；不会运行设备、网络或抓取测试。

---


**OpenAI Codex**

当前工作树存在多处未提交改动，且最近提交 `553d4bf` 正是“启动稳定化/反应记录”改动，优先级很高。我正在绕过项目忽略规则仅定位交接日志、`api-jobs`/`runs` 证据和指定 URL，不会触碰这些文件。

---


**OpenAI Codex**

已有失败证据已明确：URL 解析/严格目标验证成功，但第一次与重试都在“已验证目标详情”阶段找不到帖子 Reaction 入口；脚本随后只做评论规范化/受限回退，并最终因 `query-not-triggered` fail-closed。接下来我会对照新复现目录和 selector 几处条件，判断是自身最近回归还是页面状态/选择器不匹配。

---


**OpenAI Codex**

新复现与旧同帖运行一致：`query-not-triggered` 不是目标验证失败。`before.xml` 明确是目标详情（关闭/搜索、`RecyclerView`、评论框），第一次受控 reveal 后出现帖子操作条，但其 y=752；selector 当前硬编码 y>=900，因此漏掉了真实的空白 Reaction summary 节点，随后第二次 reveal 才滑进评论区。评论中的“4个心情/20个心情”等节点均带评论作者语义，不能当帖子 Reaction。正在整理行号、阶段耗时与回归测试建议发回主 agent。

---


**OpenAI Codex**

只读诊断已收敛，未修改文件、未运行设备/网络测试。

已确认的主根因是 selector 自身：

- 目标验证成功，不是 URL 或 strict-target 失败。新任务 `fb_b5c178f6321f4944` 从 `15:58:53.154Z` 到 `16:01:28.631Z`，耗时 `155.477s`；`target-open-diagnostics.json` 显示目标已确认，验证耗时仅 `364ms`。
- [`explore_facebook_readonly.ps1:200-248`](C:/Users/lenovo/Desktop/facebook/explore_facebook_readonly.ps1:200) 的 `Find-ReactionSummaryIconNode` 要求候选节点 `Y1 -ge 900`。
- 新运行的 `reaction-strict-detail-reveal-01.xml` 存在完整合法帖子操作行：
  - 右侧 NAF ViewGroup：`[964,752][1048,868]`
  - 同行 Like、Comment、Share：y=752
- 该节点满足现有 NAF、右列、同行三按钮结构，但被 `Y1 -ge 900` 排除。
- 历史成功运行的同类节点位于 `[964,1113][1048,1229]`，所以现有测试没有暴露这个布局变体；测试 [`test_facebook_capture_orchestrator.py:1250-1261`](C:/Users/lenovo/Desktop/facebook/test_facebook_capture_orchestrator.py:1250) 反而把 `Y1 -ge 900` 固化成了契约。
- `Find-ReactionCountNode` 没有漏掉数字节点：本帖 UI 没有显式帖子 Reaction 数字；评论中的 `4/20/23/7个心情` 是评论 Reaction，不能接受。

因此流程是：

1. [`explore_facebook_readonly.ps1:593-675`](C:/Users/lenovo/Desktop/facebook/explore_facebook_readonly.ps1:593) 首次受控滑动 `1500 -> 650`。
2. y=752 帖子操作行出现，但 selector 漏检。
3. 又执行第二次受控滑动，进入评论区。
4. diagnostics 记录 `entry_scan_scrolls=2`、`no-reaction-count-after-strict-target-detail-scan`。
5. [`run_facebook_capture.ps1:1303-1415`](C:/Users/lenovo/Desktop/facebook/run_facebook_capture.ps1:1303) 再次重开同一个已验证目标，增加延迟但没有修复入口识别。
6. fallback 仍因 strict surface/来源保护停止，最终正确 fail-closed 为 `query-not-triggered`。

最小通用修复建议：

- 将 Reaction summary 的垂直约束从固定 `Y1 >= 900` 改为至少覆盖 y≈700 的操作行，或改成基于可验证 action-row 的相对结构判断。
- 保留现有 NAF、右侧候选、同行 Like/Comment/Share 三元条件；不要把裸 `Like`、裸 `Comment` 或评论中的“心情”当帖子入口。
- 增加离线回归 fixture：y=752 三按钮行必须识别；评论 Reaction 行必须不识别。
- 修复后第一次 reveal 应直接点击 summary，不应继续第二次滑入评论。

必须保留的不变量：

- `Find-ReactionCountNode` 只接受数字/`feed_like_count`，不接受裸 Like。
- `Find-CommentsDetailEntryNode` 排除裸 Comment/评论操作。
- strict target 继续要求 fresh source-post guard，禁止 generic cross-post swipe。
- 未确认目标详情时继续拒绝向上内容滑动。
- 当前同 URL 历史运行也失败，说明不是最近 `primaryReactionRetryLimit=1` 改动引入；该改动只减少一次重开次数。

启动慢的可证延迟来源：

- Share URL resolver 的 HEAD 请求最多 15 秒：[`open_facebook_post_readonly.ps1:371-387`](C:/Users/lenovo/Desktop/facebook/open_facebook_post_readonly.ps1:371)、[`resolve_facebook_share_url.py:54-65`](C:/Users/lenovo/Desktop/facebook/resolve_facebook_share_url.py:54)。
- URL intent 后固定等待 8 秒：[`open_facebook_post_readonly.ps1:261-305`](C:/Users/lenovo/Desktop/facebook/open_facebook_post_readonly.ps1:261)。
- worker 启用 Frida 时，启动还包含 UI readiness、3 次稳定 PID 间隔和同步注入：[`run_facebook_capture.ps1:263-304`](C:/Users/lenovo/Desktop/facebook/run_facebook_capture.ps1:263)。
- 当前 API 明确传 `-ScrollCount 0`：[`facebook_capture_api.py:608-613`](C:/Users/lenovo/Desktop/facebook/facebook_capture_api.py:608)，所以本次 API 运行的评论越界不是 feed-scroll loop；但直接调用脚本默认会执行 4 次、每次等待 4 秒的 feed scroll：[`run_facebook_capture.ps1:1198-1209`](C:/Users/lenovo/Desktop/facebook/run_facebook_capture.ps1:1198)。严格目标 URL 流程应跳过该预滚动，除非显式请求。

---


**OpenAI Codex**

主 agent 已明确进入实施阶段。我会只改 `explore_facebook_readonly.ps1` 与对应离线契约测试：先读取调试/验证要求并核对备份与当前差异，再用脱敏 XML 通过 PowerShell AST/函数提取做离线红绿验证，保留 strict-target、source guard 和评论过滤。不会操作模拟器、网络或 API 实测。

---


**OpenAI Codex**

## 当前进度

- 已完成只读根因分析，尚未修改 `explore_facebook_readonly.ps1`。
- 主 agent 已授权实施通用修复，限定文件：
  - `explore_facebook_readonly.ps1`
  - `test_facebook_capture_orchestrator.py`
- 主 agent 已备份：
  - `backups/stabilization-20260915-155505/explore_facebook_readonly.ps1`
  - 同目录还有其他稳定化备份。
- 当前工作树已有主 agent 的未提交测试改动，必须保留；主要是 STOP/启动超时/helper/self-heal 契约测试及 `$primaryReactionRetryLimit = 1` 更新。

## 已确认根因

- 失败任务 `fb_b5c178f6321f4944`：
  - `15:58:53.154Z` 开始
  - `16:01:28.631Z` 结束
  - 耗时 `155.477s`
  - target 已严格确认为 post `1476320540987565`，Reaction total `162`
  - 最终 `query-not-triggered`
- `explore_facebook_readonly.ps1:200-248` 的 `Find-ReactionSummaryIconNode` 使用固定条件：
  - candidate 必须为 Facebook、clickable、NAF、右侧 `ViewGroup`
  - 同行必须存在 Like/Comment/Share
  - 但额外要求 `Y1 -ge 900`，见当前约 `:209`
- 新鲜 XML：
  - `runs/api-fb_b5c178f6321f4944/attempt-001/readonly-ui/reaction-strict-detail-reveal-01.xml`
  - 合法帖子 action row 的候选为 `[964,752][1048,868]`
  - 同行 Like `[0,752][190,868]`
  - Comment `[190,752][361,868]`
  - Share `[361,752][532,868]`
  - 因 y=752 被 `Y1 -ge 900` 漏掉。
- 历史成功 fixture：
  - `runs/batch_34f91feb15b646e1/post-070/attempt-001/readonly-ui/before.xml`
  - 同结构 candidate 在 `[964,1113][1048,1229]`，所以旧测试未发现问题。
- `reaction-strict-detail-reveal-02.xml` 中的 `4/20/23/7个心情` 都属于评论 Reaction，节点有“某人的评论按钮”语义；不能放宽为帖子入口。
- strict scan 在 `explore_facebook_readonly.ps1:593-716`：
  - 首次 bounded reveal 后漏检 y=752
  - 继续第二次滑动，进入评论区域
  - diagnostics 显示 `entry_scan_scrolls=2`、`no-reaction-count-after-strict-target-detail-scan`
- 同 URL 历史运行也失败，非最近 retry-limit 改动引入；`primaryReactionRetryLimit=1` 只减少无效重开次数。

## 实施决策

不要简单把 `900` 改成 `700`。改成可见内容区域约束：

- 在 `Find-ReactionSummaryIconNode` 内从 `Get-UiNodes` 找 Facebook 可滚动内容容器：
  - `Package = com.facebook.katana`
  - `Scrollable = true`
  - `Class` 匹配 `RecyclerView|ScrollView`
  - 真实 fixture viewport 是 `[0,179][1080,1719]`
- candidate 必须完整落在该 viewport 内，避开顶部 header。
- 找到 composer（`AutoCompleteTextView` 等）后，candidate 的底部必须位于 composer 顶部之前，避免命中底部输入区。
- 保留现有 NAF、右列、candidate 尺寸、同行结构。
- 同行 Like/Comment/Share 应额外要求 `Package = com.facebook.katana`，并使用严格的同排边界容差，避免跨评论行匹配。
- 不改：
  - `Find-ReactionCountNode`
  - `Find-CommentsDetailEntryNode`
  - strict target/source guard
  - 禁止 generic cross-post swipe 的行为

## 测试要求

当前测试文件已有主 agent 的未提交改动，需在其基础上添加离线测试：

- 用 PowerShell AST 从真实 `explore_facebook_readonly.ps1` 提取并执行 `Get-UiNodes` / `Find-ReactionSummaryIconNode`，不要复制一份独立实现。
- 使用最小脱敏 XML fixture 覆盖：
  - y=752：必须命中
  - y=1113：必须命中
  - 另一个合理 viewport 内 y 值：必须命中
  - 评论 Reaction row：不得命中
  - 缺 Share：不得命中
  - 裸 Comment：不得命中
  - 非 Facebook package：不得命中
  - header 区域：不得命中
  - composer/遮挡区域：不得命中
  - 异常尺寸：不得命中
- 现有旧契约测试：
  - `test_facebook_capture_orchestrator.py:1250-1261`
  - 当前只断言源码包含 `Y1 -ge 900`，应改成新 viewport/visible-area 约束断言。
- PowerShell 5.1 和 pwsh 都可用；此前确认：
  - `C:\windows\System32\WindowsPowerShell\v1.0\powershell.exe`
  - `C:\Users\lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe`
- 所有 shell 命令必须通过 `rtk`。
- 手工编辑必须使用 `apply_patch`，不要写设备/网络测试。
- 读取过并需遵循：
  - `C:\Users\lenovo\.agents\skills\superpowers\systematic-debugging\SKILL.md`
  - `C:\Users\lenovo\.agents\skills\superpowers\verification-before-completion\SKILL.md`

## 还需完成

1. 检查/保留当前测试差异。
2. 用 `apply_patch` 实现 selector viewport/composer 修复。
3. 添加 AST 提取执行的离线 fixture 测试。
4. 先验证测试在旧实现上能失败（如可安全做到），再恢复修复验证通过。
5. 运行针对性 `test_facebook_capture_orchestrator.py`，必要时运行全模块回归；不运行设备/API/网络测试。
6. 用 `git diff --check`、测试输出、差异行号向主 agent 汇报；不要提交，除非主 agent另行要求。

---


**OpenAI Codex**

我接着现有工作树继续：先核对未提交差异和目标函数的当前实现，再在原实现上直接补选择器与离线契约测试。会保留主 agent 已有改动，并只触碰约定的两个文件。

---


**OpenAI Codex**

当前实现确实只用固定 `Y1 >= 900`，且同行判断未限定 Facebook 包名和垂直边界。接下来我会把候选节点绑定到实际可滚动内容视口，并让同行动作必须与候选行的上下边界近似一致，同时用脱敏 XML 通过 PowerShell 真实执行脚本函数。

---


**OpenAI Codex**

我先把离线 fixture 测试加到现有测试模块，测试调用会从 `explore_facebook_readonly.ps1` 截取真实的 `Get-UiNodes` 和 `Find-ReactionSummaryIconNode` 函数后交给本机 PowerShell 执行。这样先在旧实现上验证新增用例确实能暴露 `y=752` 漏检，再落实现修复。

---


**OpenAI Codex**

旧实现上的新增测试已按预期暴露 `y=752` 漏检；其中两个负例还说明了 fixture 需要贴近真实评论节点语义。现在实现最小修复：候选必须完整落在 Facebook 的可滚动内容容器内，底部不得跨入输入框，并把 Like/Comment/Share 的同行校验收紧到同包名和边界容差。

---


**OpenAI Codex**

修复后的真实函数测试已通过 10 个 fixture 用例；另外直接用失败运行的真实 XML 验证，`reaction-strict-detail-reveal-01.xml` 能命中 `[964,752][1048,868]`，评论详情 `reveal-02.xml` 不会误命中。现在跑整个编排器测试模块，确认没有破坏既有契约。

---


**OpenAI Codex**

已完成并验证：

- 修复 [explore_facebook_readonly.ps1](C:/Users/lenovo/Desktop/facebook/explore_facebook_readonly.ps1:200)：
  - 使用 Facebook `RecyclerView/ScrollView` 实际 viewport，不再固定 `Y1 >= 900`
  - 排除 header、composer 遮挡区和评论 reaction 语义
  - Like/Comment/Share 限定 Facebook 包名及严格同行边界
- 在 [test_facebook_capture_orchestrator.py](C:/Users/lenovo/Desktop/facebook/test_facebook_capture_orchestrator.py:50) 添加基于真实 PowerShell 函数提取执行的 10 个 XML fixture 测试。
- 真实失败 XML 验证：
  - `[964,752][1048,868]` 成功命中
  - 评论详情 XML 不误命中
- 验证通过：
  - 编排器测试：`123 tests, OK`
  - PowerShell 5.1 解析检查通过
  - pwsh fixture 执行通过
  - `git diff --check` 通过

未提交，保留主 agent 现有工作树改动。

---


**OpenAI Codex**

我会对可见模拟器相关脚本做只读审计，不启动设备、不改配置、不重跑旧全模块测试。先核对当前差异、worker 配置和启动链路，再检查 `Session0`、证书/CA、`-writable-system` 与发布文件是否自洽，最后只给最多 3 条有证据的问题。

---


**OpenAI Codex**

已确认主要链路：`start_api_and_emulator.ps1` 在 Session 0 会调用新 launcher，launcher 再调用 `facebook_visible_emulator.py`；`start_facebook_stable.ps1` 只有显式 `-WritableSystem` 或 `-RestoreMitmCertificate` 才追加 `-writable-system`。我继续做静态发布清单和环境引用核对，并运行仅限 `test_facebook_visible_emulator.py` 的离线测试。

---


**OpenAI Codex**

只读审阅结论：

1. **P1：`run_emulator()` 没有 Session 0 边界。**  
   [facebook_visible_emulator.py:253](C:/Users/lenovo/Desktop/facebook/facebook_visible_emulator.py:253) 会直接复用 `find_matching_processes()` 返回的任意会话进程；若匹配到 `SessionId=0`，会返回 `action=reused`，也没有拒绝从 Session 0 直接启动。新 launcher 的交互会话保护只覆盖 [launch_visible_in_user_session.ps1:64](C:/Users/lenovo/Desktop/facebook/launch_visible_in_user_session.ps1:64) 这条路径。建议让 Python 入口拒绝复用/启动 Session 0，或明确限制只能由 launcher 调用，并补对应测试。

2. **P2：正常启动不应盲加 `-writable-system`，但首次 CA 准备路径存在缺口。**  
   `build_emulator_command()` 没有该参数，现有普通 AVD 启动不受影响；README 的手工首次 CA 流程明确要求 `start_facebook_stable.ps1 -WritableSystem`，且 `add_facebook_worker.ps1` 也会显式传入。可是 Session 0 的一键链路先由 launcher 启动无 writable overlay 的模拟器，再在 [start_api_and_emulator.ps1:363](C:/Users/lenovo/Desktop/facebook/start_api_and_emulator.ps1:363) 调用 stable starter 时没有 `-WritableSystem`；已有匹配进程不会被重新启动。因此新 AVD 若依赖这条一键链路首次安装 CA，可能无法满足 README 的 CA 前置条件。建议只在首次 provisioning/CA 流程显式传 `-WritableSystem`，不要改变普通复用启动命令。

其余检查无问题：当前 `facebook_workers.json` 未改动；实际 SDK、ADB、三个 AVD 和本机 mitmproxy CA 文件均存在；发布清单 95 项全部存在且无重复；`test_facebook_visible_emulator.py` 为 `8 tests OK`，目标 Python 可编译，三个 PowerShell 文件解析通过。

<oai-mem-citation>
<citation_entries>
MEMORY.md:355-356|note=[preserved interactive-session emulator lifetime and launcher boundary]
MEMORY.md:386-386|note=[preserved worker-01 account and emulator state]
</citation_entries>
<rollout_ids>
01a06fdc-141f-7660-ae66-3c22e149929d
</rollout_ids>
</oai-mem-citation>

---
