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

我先按要求读取调试/计划规范和相关历史线索，再核对取消状态机、PowerShell transport 现状与现有测试结构；随后先补隔离红测，确认失败形态后做最小补丁并运行定向验证。全程只触碰源码/测试，不重启服务、模拟器或 ADB，也不改配置、runs、凭证。

---


**OpenAI Codex**

源码初读已确认现有实现把“进程停止判定”和“lease 释放”分开保护，但取消路径目前只处理进程树，没有看到 transport 恢复链路。下一步我在逐段核对持久化字段、worker takeover 条件和 PowerShell finally 的实际调用边界，避免把清理挂到过早或错误的状态点。

---


**OpenAI Codex**

根因已定位：Windows `taskkill /T /F` 直接杀掉 `run_facebook_capture.ps1`，所以其 `finally` 中的代理/reverse/watchdog 恢复永远不会运行；API 随后 `_mark_process_stopped_locked()` 立即释放 lease。我的补丁会把清理放在“进程树停止确认”之后、lease 释放之前，并把清理结果持久化到 job；清理只通过窄小 PowerShell helper 读取当前代理并做严格 capture-proxy 比对，外部改动保持不动，任何失败都保留 worker/lease blocked。 macOS 分支不调用该 helper。

---


**OpenAI Codex**

当前预计改动范围已收敛：新增 `facebook_cancel_transport_cleanup.ps1`，`facebook_capture_api.py` 增加 Windows-only bounded helper 调用、`transport_cleanup_state/error` 持久字段及失败时 lease/worker 阻塞；新增隔离 `test_facebook_cancel_transport.py`。已有红测尚未落盘，下一步先把“停止后才调用、外部代理不覆盖、清理失败阻塞、无 runner 不探测”四类行为写成 mock 测试。

---


**OpenAI Codex**

我现在落第一批隔离红测，只通过 Python mocks 验证顺序和状态，不启动任何真实组件。测试会把 helper 作为边界：API 只接受 helper 的机器结果，外部代理变化/无 PowerShell 都必须 fail-closed；这样先固定取消状态机契约，再接入实现。

---


**OpenAI Codex**

隔离红测已落盘并按预期失败：5 个用例都因 `_cleanup_transport_after_capture` 尚不存在而失败，说明测试边界确实先于实现固定。现在开始最小实现：先加纯 Python 的 bounded PowerShell 调用/结果解析，再接 JobManager 的停止后清理与持久化字段，最后加 helper 脚本。

---


**OpenAI Codex**

停止后的 Python 管线已接入，且处理了一个关键竞态：capture runner 在 process callback 注册前启动时先持久化 `pending`，避免取消后遗漏清理；bootstrap-only/无 runner 不会误把正常代理当外部改动。接下来写 PowerShell helper，严格保证代理读取先行、外部值直接失败、成功后才 reverse/normal/readback/watchdog。

---


**OpenAI Codex**

父 agent指出一个真实并发窗口：HTTP `cancel()` 和 worker 的 final/process callback 都可能在停止确认后同时看到 `pending`，从而双跑 helper、互相影响 watchdog。我要加一个按 job 的 cleanup-in-flight 保护（不改变 lease 语义），并补并发回归测试；同时保留 helper 全程在锁外运行，避免 HTTP 状态线程被 ADB 清理阻塞。

---


**OpenAI Codex**

## 当前进展

- 已定位根因：Windows API 使用 `taskkill /T /F` 终止 `run_facebook_capture.ps1`，导致其 `finally` 未执行，Android 代理残留为 `127.0.0.1:<capture_port>`。
- 已新增隔离测试文件 [test_facebook_cancel_transport.py](C:\Users\lenovo\Desktop\facebook\test_facebook_cancel_transport.py)：
  - 进程树确认停止后才调用 transport cleanup
  - 外部代理变化不覆盖
  - cleanup 失败保留错误并阻塞 worker/lease
  - 正常 success 不额外探测
  - macOS/no Windows runner 不调用 ADB
  - 并发 cleanup 只执行一次
- 已在 [facebook_capture_api.py](C:\Users\lenovo\Desktop\facebook\facebook_capture_api.py) 初步实现：
  - `_cleanup_transport_after_capture`
  - `capture_process_callback`
  - `transport_cleanup_state/error` 持久字段
  - capture runner 启动时记录 `pending`
  - 停止确认后执行 cleanup
  - cleanup 失败时保持 lease blocked、记录 orphan worker
  - 单 job cleanup in-flight `threading.Event`，防止 cancel/final callback 双重 cleanup
  - macOS 返回 `not-required`/runner-owned，不调用 Windows helper
- 已新增 [facebook_cancel_transport_cleanup.ps1](C:\Users\lenovo\Desktop\facebook\facebook_cancel_transport_cleanup.ps1)：
  - 先读取 Android 当前代理
  - 仅当前值等于 `127.0.0.1:<capture_port>` 或已配置正常代理时继续
  - 外部代理值直接返回 `blocked-external-proxy`，不修改、不启动 watchdog
  - 成功后恢复 reverse mappings、必要时 `Set-FacebookNormalProxy`、显式 readback、启动 watchdog
  - 不重启 emulator/Facebook/ADB server

## 已验证

- 初始红测按预期因 helper 尚不存在全部失败。
- 当前隔离测试除刚补的并发测试外，其余已通过。
- `python -m py_compile facebook_capture_api.py test_facebook_cancel_transport.py` 已通过。
- 并发测试此前失败原因是测试 helper `_manager()` 全局 mock 了 `threading.Thread.start`；已保存 patcher 并在该测试中恢复真实线程，需重新运行确认。

## 关键约束

- 不重启 API、模拟器、Facebook、ADB server；不操作真实 ADB。
- 不修改配置、runs、凭证；不提交。
- 保留现有取消状态机、进程 identity/uncertain lease 保护、正常成功路径。
- 不能覆盖外部代理变化，也不能让下一 job 获得未恢复 worker。
- 保持 macOS runner 的既有 finally cleanup。
- 当前 worktree 有大量用户/其他 agent 修改，不能回滚或覆盖无关改动。

## 下一步

1. 重新运行 `python -m unittest -v test_facebook_cancel_transport.py`，确认并发用例。
2. 运行完整 `test_facebook_capture_api.py`，重点检查 `_set_running_process` 重写和旧取消测试。
3. 用 PowerShell parser 检查 `facebook_cancel_transport_cleanup.ps1`；检查 `if` 表达式兼容 Windows PowerShell 5.1。
4. 检查/修正 `_load_persisted_jobs` 对 `transport_cleanup_state=pending/failed` 的重启阻塞逻辑。
5. 将新 helper/test 加入 `.gitignore` 白名单和 `release-files.txt`，避免发布漏文件。
6. `git diff --check`、Python compile、PowerShell parser 后向 root 汇报精确行号和测试结果。
7. root 需自行获取 child thread id 并执行 Luna Max runtime guard；本 child 无法通过当前工具找到 `/root` thread。

---


**OpenAI Codex**

我继续从现有改动和失败测试状态接手。由于这里涉及取消流程的异常行为，我会先核对系统化调试与完成前验证要求，再重新跑隔离测试、完整 API 测试和 PowerShell 解析检查；编辑前也会确认当前 diff，避免碰到其他未相关改动。

---


**OpenAI Codex**

隔离测试已重新验证，6/6 通过，包括并发取消与 final callback 只触发一次 cleanup。接下来我在检查旧 API 状态机的回归面，尤其是进程句柄注册/清除、重启恢复和失败时 lease 的持有逻辑。

---


**OpenAI Codex**

完整 API 测试暴露了一个旧取消回归：已停止的进程在 final callback 中仍被 lease 分支当成“未验证”，导致状态停在 `cancelling`。同时按主 agent 指出的真实顺序，正常成功路径会在 capture callback 清理后仍遗留 uncertain lease。我会先把这两条 callback 时序写成隔离回归，再做同一处状态判定的最小修复。

---


**OpenAI Codex**

主 agent 提醒当前可能残留了多个测试进程。我先按 PID 精确核对命令行和父子关系，只处理确认属于本次测试且已挂起的进程，不触碰 API、模拟器或其他服务；后续每个长测试都会设置明确超时。隔离回归已加入，尚未宣称结果。

---


**OpenAI Codex**

已精确终止 5 个仍在运行的测试 Python 进程，核对结果为空，API PID 未涉及。为避免回归测试自身进入 scheduler 等待，我按建议把正常成功用例收敛为确定性的真实 callback 顺序：bootstrap 注册/结束、capture runner 标记、capture 注册/结束、成功持久化、final callback，再验证第二 job 能取得 worker。即将更新该测试。

---


**OpenAI Codex**

隔离回归现已 7/7 通过，原先失败的“进程尚未注册”取消用例也单独通过。下一步我先审阅完整测试中曾在 120 秒处停住的 worker-loop 用例，定位是否是测试夹具还是实现状态机造成等待，再用一次 240 秒硬上限跑完整文件，避免再累积挂起进程。

---


**OpenAI Codex**

<turn_aborted>
The previous turn was interrupted on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed.
</turn_aborted>

---
