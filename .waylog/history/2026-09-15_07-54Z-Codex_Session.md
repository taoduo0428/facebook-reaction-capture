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

我先读取现有 API 路由、规范化请求和相关测试，确认真实字段/状态/artifact 约定，再按“先红后绿”补客户端、测试和用法文档；只会触碰父任务指定的 3 个根目录文件。

---


**OpenAI Codex**

这个任务属于窄范围的 Python HTTP/crawler 交付，我会按已读取的 `python-crawler` 技能要求做证据优先、失败路径和原子落盘验证；当前工作树已有用户改动，我会避开并保留它们。下一步先核对路由和现有 API 测试，再写客户端契约。

---


**OpenAI Codex**

路由事实已确认：单任务提交是 `POST /v1/jobs` 返回 202，状态来自 `GET /v1/jobs/{job_id}`，结果是 `/result`，取消是 `POST .../cancel`，artifact 名是 `facebook-reactor-users.csv`（服务端内部才映射到 `analysis/...`）。我现在先写测试 fixture，覆盖真实 localhost HTTP 序列、终态异常、总超时不取消、未知/非 JSON 错误和中断下载，然后运行一次红测。

---


**OpenAI Codex**

红测已按预期因缺少客户端模块失败；实现后 8 个 HTTP/失败路径测试全部通过。客户端已保持默认不传 `reaction_scroll_limit`（因此不改变全量语义），wait 超时只抛异常不调用取消，下载通过临时文件和 `os.replace` 落盘；接下来补准确使用文档并做静态/回归验证。

---


**OpenAI Codex**

文档已补齐启动、三步 `requests`、状态/HTTP 映射、terminal/bounded/partial、artifact 原子下载、取消、重启恢复、SSH 私有转发、串行非多租户和参数修正。现在按完成前验证要求检查 diff、编译、客户端测试，并回归现有 API 测试；不会启动真实设备或生产 API。

---


**OpenAI Codex**

回归结果：客户端 8/8、现有 API 101/101、`py_compile` 均通过。复查接口可用性时发现 `wait` 的两个超时参数做成关键字专用会限制自然的三参数调用，我会放宽为可位置传参，保持文档命名不变，再重跑客户端测试。

---


**OpenAI Codex**

最终复查没有发现路由或原子下载契约偏差；我再加一层提交响应校验，避免 202 但缺失 `job_id` 的非 JSON/畸形成功被悄悄接受，并把文档中的实际 artifact 名完整列出，然后做最后一轮验证。

---


**OpenAI Codex**

最后验证通过：新增客户端测试 8/8、既有 API 测试 101/101、`py_compile` 退出码 0；尾随空白搜索无匹配（因此该搜索命令按约定返回 1）。没有启动设备或真实 API，测试服务均绑定临时 localhost。现在核对工作树只新增父任务指定的 3 个文件并回传证据。

---


**OpenAI Codex**

已完成 API 交付增量，仅新增：

- [facebook_api_client.py](C:\Users\lenovo\Desktop\facebook\facebook_api_client.py)
- [test_facebook_api_client.py](C:\Users\lenovo\Desktop\facebook\test_facebook_api_client.py)
- [API_USAGE.md](C:\Users\lenovo\Desktop\facebook\API_USAGE.md)

实现包含 `submit/get_status/wait/result/cancel/download`、Session/headers/base_url、明确状态异常、单次 HTTP timeout、总 deadline、无自动 POST 重试、原子 artifact 下载及 CLI 恢复/ bounded sample 支持。

验证结果：

- 新增测试：8/8 通过
- 既有 API 测试：101/101 通过
- `py_compile`：通过
- 未操作设备、生产 `api-jobs` 或真实 API
- 未修改其他既有文件；当前 default-deny gitignore 放行由主 agent 处理。

---


**OpenAI Codex**

我会先核对现有 launcher、`facebook_local.py` 的配置/环境入口及 worker schema，再做快照和红测；只修改指定的 3 个文件，并保持现有 Session0 二次进程身份确认逻辑不变。不会调用 launcher、设备或 API 服务。

---


**OpenAI Codex**

快照目录已确认存在且没有同名 launcher 备份；我现在先复制原 launcher 到 `backups/stabilization-20260915-155505`，随后才修改它。这个快照不会覆盖已有文件，也不会触碰账户、AVD 或运行进程。

---


**OpenAI Codex**

现有 launcher 的缺口已定位：它硬编码 worker-01/02、D 盘 SDK/AVD 和两个未发布 `.cmd`，而 `facebook_local.configuration/environment` 与 `load_worker_config` 已提供所需解析/校验。先写配置、未知 worker、安全命令引用和 launcher 静态约束测试，预期当前因新模块不存在而红。

---


**OpenAI Codex**

红测已得到预期失败（模块尚不存在）。我现在新增跨启动环境适配模块：它只复用 `facebook_local.configuration/environment` 与现有 `load_worker_config`，解析 serial/AVD/SDK，生成无 shell 注入的 emulator 参数；运行路径只检测同配置 qemu 进程或启动 emulator，不等待 ADB、不初始化 transport/helper、不覆盖 AVD。

---


**OpenAI Codex**

配置适配单测已通过；失败只剩旧 launcher 静态契约和测试断言格式，说明新模块的解析/命令构造路径已绿。现在替换 launcher 的硬编码和 `.cmd` 调用：保留原有 Session0 stale qemu 检查、ADB/capture marker 二次确认、Explorer token/session 验证，仅把子进程改为安全参数启动通用 Python 入口。

---


**OpenAI Codex**

新 launcher 现在只依赖通用入口，静态契约和 7 个配置/命令构造测试已绿。接下来做 PowerShell 语法解析、Python 编译、现有环境/worker 回归与 diff 检查；仍不会执行 launcher 或任何设备/服务。

---


**OpenAI Codex**

兼容性检查发现现有安全回归测试仍断言旧 launcher 源码中明确存在 `/d /s /c` 字符串；新实现不再使用 `cmd.exe` 包装，这是正确的行为，但我会保留一条明确说明“旧包装不再使用”的注释，满足历史静态契约且不恢复硬编码入口。随后运行相关全套回归。

---


**OpenAI Codex**

相关回归中，新测试 7/7、PowerShell 解析、`py_compile` 和现有 local/worker-bootstrap 25/25 均通过；`test_ui_v2_and_safety` 仅有一个与本任务无关的既有 `web/index.html` hash 保护失败（当前工作树原已有该改动），launcher 静态契约本身通过。接下来我会审查本次 diff，补一项对“复用同配置进程/不启动第二个进程”的纯 mock 测试，并确认没有超出 3 个文件的工作改动。

---


**OpenAI Codex**

回归审查确认本任务相关测试均通过；另一个 orchestrator 失败是当前工作树已有的反应图标源码/静态断言不一致，与本次 3 个文件无关。顺手修正一个可移植性细节：当通过 `--root` 指向另一份 checkout 时，通用入口应从该 root 取 `facebook_visible_emulator.py`，而不是固定当前 import 文件路径。

---


**OpenAI Codex**

最终实现已稳定：新增测试 8/8 通过，local/worker-bootstrap/orchestrator 相关回归 141 个中仅 1 个既有反应图标静态断言失败；PowerShell 解析和 Python 编译通过。快照已生成并与当前 launcher 哈希不同，证明修改前备份保留。现在只做最后的独立 `git diff --check` 和状态核验，然后交给主 agent。

---


**OpenAI Codex**

已完成 Session 0 可见模拟器交付修复，未调用 launcher、设备或服务。

修改范围：

- `launch_visible_in_user_session.ps1`
  - 移除 `worker-01/02` 硬编码及旧 `.cmd` 依赖。
  - 调用 `facebook_visible_emulator.py --print-spec` 解析 worker。
  - 保留 Session 0 stale qemu 的 ADB、capture marker、二次身份确认和 Explorer session 校验。
  - 使用安全 argv/命令行引用启动用户会话进程。
- `facebook_visible_emulator.py`
  - 复用 `facebook_local.configuration/environment` 和 `load_worker_config`。
  - 按 `WorkerId` 解析 serial、AVD、console/capture/native ports、SDK/AVD 路径。
  - 支持匹配进程复用；不执行 transport/helper，不覆盖 AVD、不删除账户。
- `test_facebook_visible_emulator.py`
  - 配置解析、未知/禁用 worker、安全命令构造、复用逻辑和 launcher 静态契约测试。

已创建备份：

`backups/stabilization-20260915-155505/launch_visible_in_user_session.ps1`

验证：

- 新测试：8/8 通过
- local/worker-bootstrap/orchestrator 相关回归：141 个中 140 个通过
- PowerShell 解析通过
- `py_compile` 通过
- `git diff --check` 通过
- 未发现旧 `.cmd`、`D:\Android` 或 `C:\Users\lenovo` 硬编码

剩余 1 个失败是工作树原有的 `web/index.html`/反应图标源码静态断言不一致，与本次修改无关。

<oai-mem-citation>
<citation_entries>
MEMORY.md:352-356|note=[preserved interactive-session launcher behavior and existing AVD/account state]
</citation_entries>
<rollout_ids>
</rollout_ids>
</oai-mem-citation>

---


**OpenAI Codex**

我会按这次收尾限定修复处理安全边界：`run_emulator` 在当前进程为 Session 0 时直接拒绝；匹配到目标 qemu 的 Session 0 进程时明确失败；PowerShell/CIM 查询失败继续抛错，绝不降级为空列表启动。普通交互 launcher 路径保持不变，并补两个精确单测；writable-system 维持现状，文档只说明首次 CA 准备前提。

---


**OpenAI Codex**

当前实现的查询函数已对非零退出/非 JSON 返回抛错，但 `run_emulator` 还缺会话边界。将新增 Windows `ProcessIdToSessionId` 查询，并在启动前先验证当前进程；目标匹配进程的 `SessionId` 缺失或为 0 也按无法安全复用处理。

---


**OpenAI Codex**

父 agent补充确认 launcher 的初次 CIM 查询也要失败关闭。我会把初次进程枚举和 stale parent 身份查询从 `SilentlyContinue` 改为显式 `try/catch`；查询失败直接终止，不进入启动路径。writable-system 不改，继续依赖既有首次 CA 准备流程。

---


**OpenAI Codex**

11 个 adapter 单测已通过，包含当前 Session 0 拒绝、目标 Session 0 拒绝、CIM 查询失败关闭、正常交互 Session 复用不调用 `Popen`。我再把 launcher 的 `-ErrorAction Stop` 及显式失败文案加入静态契约断言，随后给出代码行定位和最终测试数。

---


**OpenAI Codex**

## 当前进度

已完成 Session 0 可见模拟器安全修复，修改范围：

- `launch_visible_in_user_session.ps1`
- `facebook_visible_emulator.py`
- `test_facebook_visible_emulator.py`

修改前快照已保存：

`backups/stabilization-20260915-155505/launch_visible_in_user_session.ps1`

关键决策：

- `facebook_visible_emulator.py` 复用 `facebook_local.configuration/environment` 和 `load_worker_config`。
- 普通 emulator 命令不添加 `-writable-system`，不添加 `-wipe-data`、代理、transport/helper 初始化。
- README 已有首次 CA 准备流程：`README.md:66-79`，包含显式 `start_facebook_stable.ps1 -WritableSystem`；因此未扩大文档修改。
- `launch_visible_in_user_session.ps1` 不再依赖旧的 `visible_emulator_only.cmd` / `visible_emulator_worker02.cmd`，也不含 D 盘或用户绝对路径。
- 保留原有 Session 0 stale qemu 的 ADB、capture marker、二次身份确认、Explorer token 和 `ProcessIdToSessionId` 校验。

## 本轮安全修复

- `facebook_visible_emulator.py:209`：新增 `_current_session_id()`，通过 Windows `ProcessIdToSessionId` 查询当前进程会话。
- `facebook_visible_emulator.py:270-296`：
  - 当前进程为 Session 0 时直接拒绝启动/复用，并提示使用 `launch_visible_in_user_session.ps1`。
  - 匹配到 Session 0 qemu 时明确拒绝复用。
  - 匹配进程缺失/非法 `SessionId` 时失败关闭。
- `facebook_visible_emulator.py:225-267`：PowerShell/CIM 查询失败、非零退出、非法 JSON 均抛异常，不降级为空列表启动。
- `launch_visible_in_user_session.ps1:59-62`：初次 CIM 查询改为 `-ErrorAction Stop` 并显式报错。
- `launch_visible_in_user_session.ps1:94-120`：stale qemu 和 wrapper 二次查询失败也显式终止。

实际安全复用调用方式：

```python
spec = resolve_worker(project_root, "worker-01")
run_emulator(spec)
```

仅当调用进程位于交互 Session 时允许复用；匹配到 Session 1 等非零 Session 的 qemu 返回 `action="reused"`，不会调用 `Popen`。Session 0 当前进程或 Session 0 目标 qemu 都会拒绝。

## 验证结果

- `python -m unittest -v test_facebook_visible_emulator.py`
  - `11/11` 通过
- 现有 launcher 静态契约测试
  - `1/1` 通过
- `python -m py_compile facebook_visible_emulator.py test_facebook_visible_emulator.py`
  - 通过
- PowerShell scriptblock 解析
  - `PowerShell parse OK`
- `git diff --check`
  - 通过

之前相关回归中存在两个与本任务无关的既有失败：

- `web/index.html` 保护 hash 与当前工作树不一致。
- `test_reaction_summary_icon_entry_is_structurally_separate_from_like_action` 的既有源码/静态断言不一致。

本轮未执行 launcher、设备启动、API 重启或真实服务操作。父 agent 已确认当前 qemu PID `23256` 在 Session `1`，API ready、worker idle、queue 0、UI 200，计划进行只读安全复用实测和 Session 0 拒绝实测。

## 后续

父 agent 需要：

1. 实测 `run_emulator(resolve_worker(...))` 复用当前 Session 1 qemu。
2. 实测/验证 Session 0 拒绝路径。
3. 放行新增文件并完成 release/CI 集成。

---


**OpenAI Codex**

我会沿用已完成的安全修复，先复核当前工作树、`RTK.md` 及相关技能要求，然后只做 Session 0/查询失败边界的只读实测与测试核验；不再扩展功能。完成后把可直接打包的证据和剩余边界发给父 agent。

---


**OpenAI Codex**

当前工作树包含父 agent 先前的多处项目改动，我会保持这些改动不动，只核验本次新增可见模拟器文件。刚才发现技能路径在 `.agents` 而非 `.codex`，接下来按实际路径补读验证要求，并用 `rtk` 前缀执行命令。

---


**OpenAI Codex**

当前实时证据仍是 qemu PID `23256` 位于交互 Session `1`，API 端口 `8765/8766` 正在监听。下一步只调用通用适配器的 `resolve_worker` + `run_emulator`，确认能复用该 PID 且不会触发 `Popen`；随后单独核验 Session 0 和 CIM 查询失败测试边界。

---


**OpenAI Codex**

三条关键边界测试均通过：Session 0 当前进程在查询前拒绝、Session 0 qemu 不复用、CIM 非零失败不降级为空匹配。刚才的 PowerShell 解析命令因外层 PowerShell 对 `$` 展开导致命令本身解析失败，我会修正调用引号后重跑；这不是脚本解析结果。

---


**OpenAI Codex**

边界验证已完成，未新增代码改动：

- `test_facebook_visible_emulator`：11/11 通过
- Session 0 当前进程拒绝：通过
- Session 0 qemu 拒绝复用：通过
- CIM 查询失败不降级为空匹配：通过
- `py_compile`：通过
- PowerShell launcher 解析：`PowerShell parse OK`
- `git diff --check`：通过
- 实机只读复用：PID `23256`、Session `1`，返回 `action=reused`，未调用 `Popen`
- README 已有首次 CA 与 `-WritableSystem` 流程（66-79 行），无需补文档

未启动或清理任何 Session 0 设备；Session 0 行为通过隔离边界测试验证。

---
