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

我会先读取 PDF 技能说明，再只提取三份 PDF 中与当前导航回归、strict target/source_post_id、防评论误抓及 GraphQL/MITM/worker 架构直接相关的历史证据；按页码记录，并明确标为旧运行依据，不扩展到项目代码或测试。

---


**OpenAI Codex**

三份 PDF 已确认可读，分别为 28、54、25 页。接下来先用文本抽取定位关键词，再回读命中的具体页，避免把整批旧材料带入上下文。

---


**OpenAI Codex**

三份 PDF 的相关页已回读完毕。证据集中在旧交接中的导航失败、帖子与评论 Reaction 区分、target/source 保护、GraphQL/MITM/worker 架构边界，以及历史 URL/回归线索；当前 `share/p/1Jetj5opcE` 未在 PDF 中出现，不能把旧运行外推为当前成功。

---


**OpenAI Codex**

已只读查看，未编辑或运行测试。可传给主代理：

- 《查看系统优化进度》p.15-17、20-21：旧交接已完成 share URL→canonical post、strict target-open、Reaction 防误触、checkpoint/capture。历史 URL `share/p/1GP4DP9A1z/` 曾解析为 `post_id=1476320540987565`、`reactions=154`，但 `query-not-triggered`、`reaction_pages=0`、`valid_reactor_users=0`；XML 中 4/15/20/7 心情属于评论，不能当帖子 Reaction。当前 `share/p/1Jetj5opcE` 未出现在 PDF，不能称为历史已验证成功。
- 同文 p.22、26-28：只允许修 Android UI 导航/等待；不得改 GraphQL、MITM、数据结构/CSV、worker、API、启动流程。正确链路是原帖→Action Bar→帖子 Reaction→点击→GraphQL→用户；失败最多回顶部重定位一次，禁止进入评论区反复寻找；`reaction_count>0 && users=0` 不得成功。
- 《Facebook点赞显示差异》p.1-2、5、17、23-24：Web/App 名单可能不同，total count、visible actors、API 可枚举数据须分开；保留 target/source guard、`source_post_id`、fresh capture、去重、登录/AVD/代理和历史证据，不做 fake cursor resume、不允许 worker 抢同一 emulator。p.34 的旧对照 URL 包括短 `share/r/1Dg59X6ysJ`、share/post `share/p/1DVLR4FNo6`、group、reel、watch、长 post；均仅为历史 job 线索。
- 《查看桌面项目》p.5-15、22-25：既定证据链为 UI→HTTP/API→raw→post ID→actors→pagination→报告；保留 heartbeat、restart recovery、job 持久化和 Win/Mac adapter。旧回归称 276 passed/2 skipped/0 failed，仅作版本线索，不能替代当前 URL 实测。

:codex-file-citation{path="C:/Users/lenovo/Downloads/查看系统优化进度.pdf" purpose="source"}  
:codex-file-citation{path="C:/Users/lenovo/Downloads/Facebook点赞显示差异.pdf" purpose="source"}  
:codex-file-citation{path="C:/Users/lenovo/Downloads/查看桌面项目.pdf" purpose="source"}

---


**OpenAI Codex**

我会按“先复现当前错误、再做最小生产/测试补丁、最后只跑相关测试”的顺序处理。此次会使用系统调试与完成前验证技能，因为任务明确涉及已观测的错误状态和测试覆盖；不启动或重启生产 API/抓取。

---


**OpenAI Codex**

根因已确认：`final` 阶段的文案条件仍把默认轻量选择与旧的 PCAP/MITM 假设混在一起，且完全没有按 `job_status` 拦截“已完成”措辞；未选 MITM 时解密阶段也把“未保留”写成“已完成校验”。我先用现有测试构造失败断言，再只改这两个函数/测试文件。

---


**OpenAI Codex**

失败测试已经稳定复现了四类误导：旧默认文案、非完成状态、缺失选中证据，以及 bounded 文案未说明必留结果。现在补丁只在 `build_progress_snapshot` 内计算“必留/选中证据是否就绪”和状态化 detail，不改 stage state、字段、路由或结果结构。

---


**OpenAI Codex**

最小补丁已通过新增四类回归和整个 `ProgressTests`（10 项）。我继续跑 `test_facebook_capture_api.py` 全文件，确认同一 API 模块的其他行为没有被 detail 文案改动牵连；仍不会触碰生产服务。

---


**OpenAI Codex**

相关测试已全绿，正在做最后的变更边界检查：确认只新增了进度 detail 分支与对应测试，没有改变状态值、artifact 元数据、路由或生产进程。之后把失败前后证据和测试数量交给主代理。

---


**OpenAI Codex**

已完成该独立修复，未启动/重启生产 API 或抓取。

修改：

- [facebook_capture_api.py:1153](C:/Users/lenovo/Desktop/facebook/facebook_capture_api.py:1153)
  - `completed` 按必留 artifact、已选 artifact 的实际 ready 状态和 bounded scope 生成准确 detail。
  - `failed/cancelled/queued/running` 不再声称证据已落盘。
  - 未选 `mitm` 时改为“仅用于实时处理”，不再声称完成归档解密校验。
  - 未改变状态字段、路由、artifact 结构或计算语义。
- [test_facebook_capture_api.py:665](C:/Users/lenovo/Desktop/facebook/test_facebook_capture_api.py:665)
  - 增加 bounded、失败/运行中状态、默认轻量 completed、缺失选中 artifact 覆盖。

验证证据：

- 修复前新增回归按预期失败，复现旧文案。
- 修复后相关 `ProgressTests`：10/10 通过。
- `test_facebook_capture_api.py` 全文件：104/104 通过。
- `git diff --check`：通过。
- 未修改其他文件；当前两文件中的其他差异是共享工作区已有改动。

---


**OpenAI Codex**

我会先审阅客户端实现、现有行为测试和使用文档，定位三处边界后先添加会失败的行为断言，再做最小修复并运行 `.venv` 中的客户端测试。重点会保持下载完整性校验，不把 requests 的 connect/read timeout 误写成绝对墙钟截止，也不会运行生产抓取。

---


**OpenAI Codex**

根因已定位：客户端当前把 `float("nan")/inf` 当作合法超时，`wait()` 允许零间隔；CLI 只在下载完成后输出；下载则把 gzip 解压后的字节数直接与压缩 `Content-Length` 比较。先补三组红测试，并把 gzip 策略定为显式拒绝非 identity 编码，从而保留未压缩响应的完整性校验。

---


**OpenAI Codex**

红测试已复现：旧代码确实接受非有限值、零轮询间隔，CLI 超时前无 stdout，gzip 被误报为截断。现在落最小修复：有限值校验、正轮询间隔、提交事件即时 flush、非 identity 编码显式拒绝并保留 identity 的长度校验，同时补充 timeout 语义文档。

---


**OpenAI Codex**

核心边界修复已通过，CLI 测试只剩一个测试夹具问题：服务线程的 `[api]` 日志混入捕获输出，导致按首行解析 JSON 不稳定，并非客户端泄露或输出顺序错误。我先核对服务日志流，再把测试改为从输出中筛选客户端 JSON 行，生产代码保持不变。

---


**OpenAI Codex**

四项回归已通过，整套客户端测试当前为 12/12。最后我会做语法检查、文档/源码关键词核对和工作区边界确认，确保没有改动客户端之外的文件，也不把单次请求 timeout 描述成绝对墙钟 deadline。

---


**OpenAI Codex**

客户端小修复已完成，未运行生产抓取/API。

修改了：

- [facebook_api_client.py](C:/Users/lenovo/Desktop/facebook/facebook_api_client.py)
  - 提交后立即 `flush=True` 输出 `{job_id,status}`，等待超时或终端失败时仍可找回任务；不输出 headers。
  - 拒绝 `NaN/inf/-inf` timeout；`poll_interval` 必须为有限正数；`overall_timeout=0` 保留为允许的一次快速状态检查。
  - 下载遇到非 `identity` `Content-Encoding` 明确拒绝，避免 requests 自动解压后与压缩 `Content-Length` 错比；未编码响应仍执行长度校验。
  - 明确文档字符串：requests connect/read timeout 不是绝对墙钟截止。
- [test_facebook_api_client.py](C:/Users/lenovo/Desktop/facebook/test_facebook_api_client.py)
  - 添加超时边界、CLI 即时输出/敏感 header、gzip 长度校验回归。
- [API_USAGE.md](C:/Users/lenovo/Desktop/facebook/API_USAGE.md)
  - 补充 timeout、轮询、CLI 输出及 Content-Encoding 约束。

验证：

- 修复前新增行为测试按预期失败。
- 修复后客户端全套测试：`12/12 passed`。
- `py_compile`：通过。
- 未改导航、启动器或生产服务。

---


**OpenAI Codex**

我会做只读交付审计：核对 `git ls-files`、`.gitignore`、`release-files.txt`、`requirements.txt`、README 和 `API_USAGE.md` 的相互引用与导出关系，区分“已跟踪、已忽略但放行、未提交新增、清单遗漏”。不读取或评估 launcher agent 正在新增的实现，不运行生产服务。

---


**OpenAI Codex**

当前发现一个重要交付边界：`release-files.txt` 已列出客户端、测试、`API_USAGE.md`、STOP/launcher，但这些文件目前仍是未提交新增；CI 的 unittest 命令尚未包含 `test_facebook_api_client.py`。我继续验证清单本身能否无写入地完成导出检查，并对照 `.gitignore` 放行项及 README 的验收命令。

---


**OpenAI Codex**

审计结果：有 2 项最小集成缺口；其余交付清单验证通过。未修改文件、未运行生产服务，也未审阅 launcher agent 正在新增的 visible-emulator 代码。

1. **CI 漏测客户端**  
   [.github/workflows/check.yml:28](C:/Users/lenovo/Desktop/facebook/.github/workflows/check.yml:28) 和 macOS 对应命令都未包含 `test_facebook_api_client`。客户端已有 12 项测试，但 CI 不会执行。建议把 `test_facebook_api_client` 加入 Windows/macOS 两条 unittest 命令。

2. **README 验收命令过时**  
   [README.md:108](C:/Users/lenovo/Desktop/facebook/README.md:108) 的核心验证命令同样未包含 `test_facebook_api_client`。建议与 CI 同步，避免文档验收范围小于实际交付范围。

交付核对：

- `tools.export_source.release_files()` 只读校验通过：93 个条目、1,523,879 字节，无缺失、重复或非法路径。
- `requests==2.32.5` 已在 [requirements.txt:5](C:/Users/lenovo/Desktop/facebook/requirements.txt:5)；客户端、测试、`API_USAGE.md`、STOP/launcher 均已列入 [release-files.txt:25](C:/Users/lenovo/Desktop/facebook/release-files.txt:25)。
- 上述 6 个 release 条目目前都是未提交新增，不是清单遗漏：`STOP.cmd`、`launch_visible_in_user_session.ps1`、`facebook_api_client.py`、`test_facebook_api_client.py`、`API_USAGE.md`、`stop_all_facebook.ps1`。
- `STABILIZATION_2026-09-15.md` 是未提交但未列入发布清单的运行记录；不属于客户端运行依赖。
- `API_USAGE.md` 的 HTTP 路由、requests timeout 语义、CLI job_id 即时输出和非 identity 编码策略与当前客户端实现一致。

`git diff --check` 通过。

---
