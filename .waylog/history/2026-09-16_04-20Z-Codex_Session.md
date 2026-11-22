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
- `r5` = `C:/Users/lenovo/.codex/plugins/cache/openai-primary-runtime/spreadsheets/26.909.61513/skills`
### Available skills
- imagegen: Generate or edit raster images when the task benefits from AI-created bitmap visuals such as photos, illustrations, textures, sprites, mockups, or transparent-background cutouts. Use when Codex should create a brand-new image, transform an existing image, or derive visual variants from references, and the output should be a bitmap asset rather than repo-native code or vector. Do not use when the task is better handled by editing existing SVG/vector/code-native assets, extending an established icon or logo system, or building the visual directly in HTML/CSS/canvas. (file: r2/imagegen/SKILL.md)
- openai-docs: Use for Codex models/pricing, scheduled tasks, skills, settings, setup, troubleshooting, customization, automations, and self-knowledge—including 'you,' 'your,' 'this app,' or 'this coding agent' when they refer to Codex—and for OpenAI APIs/products and ChatGPT Work. Also use for model choice/migration, prompting, SDKs, Responses, Realtime, agents, evals, and Chat/Work/Codex comparisons. Do not use for generic app/software tasks that merely mention Codex. (file: r2/openai-docs/SKILL.md)
- plugin-creator: Create and scaffold plugin directories for Codex with a required `.codex-plugin/plugin.json`, optional plugin folders/files, valid manifest defaults, and personal-marketplace entries by default. Use when Codex needs to create a new personal plugin, add optional plugin structure, generate or update marketplace entries for plugin ordering and availability metadata, or update an existing local plugin during development with the CLI-driven cachebuster and reinstall flow. (file: r2/plugin-creator/SKILL.md)
- skill-creator: Create or update a Codex skill with appropriately scoped instructions and any needed supporting resources. (file: r2/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into $CODEX_HOME/skills from a curated list or a GitHub repo path. Use when a user asks to list installable skills, install a curated skill, or install a skill from another repo (including private repos). (file: r2/skill-installer/SKILL.md)
- computer-use:computer-use: Control Windows apps from ChatGPT (file: r3/computer-use/26.908.70816/skills/computer-use/SKILL.md)
- dailywork: Generate and submit DailyWork personal or department daily reports from locally authorized Feishu activity, personal weekly reports from the caller's confirmed historical daily reports, and department weekly reports from confirmed personal weekly reports. Use when a user asks Codex to write, submit, or push a DailyWork daily/weekly report or its Feishu confirmation card. (file: r0/dailywork/SKILL.md)
- documents:documents: Create, edit, redline, and comment on `.docx`, Word, and Google Docs-targeted document artifacts inside the container, with a strict render-and-verify workflow. Use `render_docx.py` to generate page PNGs (and optional PDF) for visual QA, then iterate until layout is flawless before delivering the final document. (file: r4/documents/26.909.61513/skills/documents/SKILL.md)
- lark-cli-router: Route Feishu and Lark requests through the bundled lark-cli and its version-matched embedded Skills. Use for messages, documents, drive, Base, calendar, tasks, approvals, contacts, meetings, wiki, sheets, slides, mail, OKR, attendance, events, and Lark authorization or permission issues. (file: r0/lark-cli-router/SKILL.md)
- literature-review: Conduct comprehensive, systematic literature reviews using multiple academic databases (PubMed, arXiv, bioRxiv, Semantic Scholar, etc.). This skill should be used when conducting systematic literature reviews, meta-analyses, research synthesis, or comprehensive literature searches across biomedical, scientific, and technical domains. Creates professionally formatted markdown documents and PDFs with verified citations in multiple citation styles (APA, Nature, Vancouver, etc.). (file: r1/literature-review/SKILL.md)
- matplotlib: Low-level plotting library for full customization. Use when you need fine-grained control over every plot element, creating novel plot types, or integrating with specific scientific workflows. Export to PNG/PDF/SVG for publication. For quick statistical plots use seaborn; for interactive plots use plotly; for publication-ready multi-panel figures with journal styling, use scientific-visualization. (file: r1/matplotlib/SKILL.md)
- nature-paper2ppt: Build a complete but efficient Nature-style Chinese PPTX presentation from a scientific paper, preprint, PDF, article text, abstract, figure legends, or reading notes. Use this skill whenever the user asks to make slides/PPT/PPTX for journal club, group meeting, paper sharing, thesis seminar, lab meeting, department report, or academic presentation from a research paper, not only medical papers. It identifies the paper type and argument, selects only the figures needed for the story, writes Chinese slide content and speaker notes, creates the actual .pptx deck, and performs lightweight verification with cross-platform Python tooling by default. (file: r0/nature-paper2ppt/SKILL.md)
- networkx: Comprehensive toolkit for creating, analyzing, and visualizing complex networks and graphs in Python. Use when working with network/graph data structures, analyzing relationships between entities, computing graph algorithms (shortest paths, centrality, clustering), detecting communities, generating synthetic networks, or visualizing network topologies. Applicable to social networks, biological networks, transportation systems, citation networks, and any domain involving pairwise relationships. (file: r1/networkx/SKILL.md)
- paper-lookup: Search 10 academic paper databases via REST APIs for research papers, preprints, and scholarly articles. Covers PubMed, PMC (full text), bioRxiv, medRxiv, arXiv, OpenAlex, Crossref, Semantic Scholar, CORE, Unpaywall. Use when searching for papers, citations, DOI/PMID lookups, abstracts, full text, open access, preprints, citation graphs, author search, or any scholarly literature query. Triggers on mentions of any supported database or requests like "find papers on X" or "look up this DOI". (file: r1/paper-lookup/SKILL.md)
- pdf:pdf: Read, create, inspect, render, and verify PDF files where visual layout matters, including fillable AcroForms. Use Poppler rendering plus Python tools such as reportlab, pdfplumber, and pypdf for generation and extraction. (file: r4/pdf/26.909.61513/skills/pdf/SKILL.md)
- presentations:Presentations: Read, create or edit PowerPoint or Google Slides decks. Use for presentation, slide deck, PowerPoint, PPT, PPTX, or Google Slides requests. (file: r4/presentations/26.909.61513/skills/presentations/SKILL.md)
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
- template-creator:template-creator: Create or update a reusable personal Codex artifact-template skill. Use when the user invokes $template-creator or asks in natural language to create a reusable template from a reference document, presentation, spreadsheet, Google Docs, Slides, or Sheets link, ImageGen or Product Design image, email, Slack message, or Site project, or explicitly asks to edit or update a passed artifact-template skill. Do not use for one-off creation from an existing template. (file: r4/template-creator/26.909.61513/skills/template-creator/SKILL.md)
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

我先核对 RTK 约束、仓库入口与六份文档现状，再对照实际脚本参数和 macOS/Windows 链路建立一份证据表；确认哪些历史描述必须保留后，才会用补丁集中修改文档，最后只做静态/链接/参数验证，不启动服务或执行真实采集。

---


**OpenAI Codex**

这项工作涉及多文件契约对齐，我已读取验证技能；完成前会以当前文件、入口脚本和静态检查输出为证据，避免把历史运行记录或未做的 Mac 实机验收写成当前成功状态。接下来继续读取文档与实际启动参数。

---


**OpenAI Codex**

入口核对结果：Windows `START.cmd` 只转到 `start_api_and_emulator.ps1`，会按配置启动/复用 primary AVD，再后台启动 API 与 8766 v2 UI；`facebook_local.py start` 本身是前台服务。macOS `CONFIGURE.command` 实际顺序是 `setup.sh --install-only` → `facebook_setup.py` → `setup.sh`，后者前台保持 API；配置逻辑会自动准备/探测并按需后台启动 Frida，直接 `prepare_device.py --frida-server` 只安装不启动。文档中会按这些事实修正，尤其避免前台 Frida 命令把终端卡住。

---


**OpenAI Codex**

待改动要点已收敛：README 顶部改为链接本轮 `VALIDATION_2026-09-16.md`，明确 `CURRENT_STATUS_2026-09-09.md` 仅历史；删除多 Worker 并行暗示并避免示例中的开发机用户名路径。Windows 文档修正 x86_64 split、要求 `adb root`，让 Frida 用 daemon/runner 启动并核验 PID；Mac 快速指南与 Runbook 增加 `.command`/`setup.sh` 可执行权限、依赖/SDK/AVD 前置、健康与终点 CSV 门槛，并把 bare 前台 Frida/mitmdump 风险写清。macOS 旧 README 将压缩为兼容性说明，保留历史模拟边界。

---


**OpenAI Codex**

现在开始补丁修改六份文档：先处理 Windows 的架构/Frida/CA/启动与升级段落，再统一 README/API 和两份 Mac 文档的入口、验收及历史边界。所有示例继续使用占位路径和本机配置读取，不会写入开发机用户名或凭据。

---


**OpenAI Codex**

文档补丁已覆盖六个目标：Windows 的 x86_64 split、root/daemon Frida、CA/Helper、启动/健康/终点 CSV、停止升级和 fresh-clone 排错；README 已改为本轮验证链接并消除并行/用户名路径误导；API 已补齐 progress、batch、worker、setup/capabilities 路由；Mac 快速指南与 Runbook 已加入权限恢复、依赖版本、ARM64、非前台 Frida/CA、实机证据门槛。现在做只读一致性检查和 diff 审阅。

---


**OpenAI Codex**

<turn_aborted>
The previous turn was interrupted on purpose. Any running unified exec processes may still be running in the background. If any tools/commands were aborted, they may have partially executed.
</turn_aborted>

---


**OpenAI Codex**

DONE。已完成六份文档核对与修改，未改业务代码、workflow、`.gitignore`、release-files、VALIDATION/CHANGELOG，未提交或推送：

- [WINDOWS_DEPLOYMENT.md](C:/Users/lenovo/Desktop/facebook/WINDOWS_DEPLOYMENT.md)：修正 x86_64 split 示例；补齐 Python/依赖、SDK/AVD、人工登录、root/daemon Frida、CA/Helper、启动检查、终点 CSV 验收、停止/升级和 fresh-clone 排错。
- [README.md](C:/Users/lenovo/Desktop/facebook/README.md)：链接 `VALIDATION_2026-09-16.md`，明确 `CURRENT_STATUS_2026-09-09.md` 为历史；修正主备串行措辞、用户路径、Frida/CA/Helper 和当前门禁说明。
- [API_USAGE.md](C:/Users/lenovo/Desktop/facebook/API_USAGE.md)：补充初始化、依赖、API-only/full startup 区别，以及 progress、batch、worker、setup/capabilities 路由和完整性语义。
- [macos/QUICKSTART.md](C:/Users/lenovo/Desktop/facebook/macos/QUICKSTART.md)：补充执行权限、依赖版本、ARM64、Frida 安装/daemon、CA、API 健康、终点 CSV、停止升级。
- [macos/CODEX_DEPLOYMENT_RUNBOOK.md](C:/Users/lenovo/Desktop/facebook/macos/CODEX_DEPLOYMENT_RUNBOOK.md)：补充本轮验证链接、权限、可选 Helper gate、CA/Frida 前置和恢复边界。
- [macos/README.md](C:/Users/lenovo/Desktop/facebook/macos/README.md)：明确旧适配器、串行主备和 Windows/CI 不等于真实 M4 验收。

验证证据：

- 6 份 Markdown：30 个本地链接全部存在，代码围栏平衡，无链接错误。
- `bash -n CONFIGURE.command CHECK.command START.command setup.sh macos/start_facebook_workers.sh` 通过。
- `git diff --check` 通过。
- 入口/参数静态核对覆盖 `setup.ps1`、`facebook_local.py`、API、客户端、Frida、Mac setup/runner，最终所有 required 项 `missing=[]`。
- 未运行安装、启动、停止、ADB 写命令或真实采集。

CONCERNS：Windows 故障表新增的 `where adb` / `where emulator` 在 PowerShell 中可能命中 `Where-Object` 别名；最终复核时建议改为 `where.exe adb`、`where.exe emulator` 或 `Get-Command`。README/平台文档未写入五 URL 全成功或 Mac 已验收结论，保留 root 后续验证结果决定最终措辞。

<oai-mem-citation>
<citation_entries>
MEMORY.md:1-23|note=[保留 hosted CI/Windows simulation 与真实 M4 验收边界]
</citation_entries>
<rollout_ids>
</rollout_ids>
</oai-mem-citation>

---


**OpenAI Codex**

定向四模块已 155/155 通过，AST/py_compile/diff-check 也通过；生产只改了 `run_facebook_capture.ps1` 的启动前置等待和 `explore_facebook_readonly.ps1` 的两个 failure/零探测点。现在不再扩展生产改动，正在做少量历史证据抽取和最终 diff/行号核对，根代理可继续真实验收。

---


**OpenAI Codex**

证据抽取完成：4 个近期 diagnostics 的 target-open 验证均为 1 次、368–1004ms；explorer 从 `started_at` 到首次 Reaction action 为 12/16/20/12s，但这些样本没有逐等待计时，不能把差异归因给固定 sleep。近期 `capture.log` 仅显示 `UI ready/settled after 1 check`，Frida reuse `reuse_elapsed_ms` 3866–4094ms；因此最终只宣称启动分支最多省 3s 的确定性上限，不宣称历史样本已证明实际收益。所有真实运行仍由 root 控制。

---


**OpenAI Codex**

DONE

已完成并冻结本子任务范围：

- [explore_facebook_readonly.ps1:2736](C:/Users/lenovo/Desktop/facebook/explore_facebook_readonly.ps1:2736)：`ReactionListReadyAttempts=0` 时跳过可选 source pre-probe，默认值不变。
- [explore_facebook_readonly.ps1:2911](C:/Users/lenovo/Desktop/facebook/explore_facebook_readonly.ps1:2911)：保留 filter/swipe failure 的 `gesture-failed` 诊断。
- [run_facebook_capture.ps1:1430](C:/Users/lenovo/Desktop/facebook/run_facebook_capture.ps1:1430)：移除启动成功后的固定 3 秒等待，直接进入已有 bounded `Wait-FacebookUiReady`。
- [test_facebook_navigation_latency.py:38](C:/Users/lenovo/Desktop/facebook/test_facebook_navigation_latency.py:38)：修复 PowerShell 中文编码与 fixture 执行顺序。
- [test_facebook_capture_orchestrator.py:1124](C:/Users/lenovo/Desktop/facebook/test_facebook_capture_orchestrator.py:1124)：增加启动 readiness 回归断言。

等待分类：启动后的 3 秒固定等待已删除；Reaction 点击后的 250ms 探测、未 ready 时的 `WaitSeconds` fallback、Reaction ready polling、分页 min-cycle/初始 cooldown/限流退避均保留。近期样本仅证明 target-open 368–1004ms、UI 1 check、Frida reuse 3866–4094ms，不能据此声称实际端到端收益。确定性收益上限为每次普通启动最多 3 秒。

验证结果：

- 指定四模块：`155/155 OK`
- ready/delayed/failure fixture：`9/9 OK`
- PowerShell AST：2 个脚本均 OK
- `py_compile`：通过
- `git diff --check`：通过
- 未启动服务、未执行真实 ADB/API/采集。

当前可见 task/thread ID：`01a0a85b-5bda-75c0-b0b1-edcfac0dc8cf`。

<oai-mem-citation>
<citation_entries>
MEMORY.md:31-33|note=[preserved Facebook runtime state and rechecked current repository evidence]
MEMORY.md:103-107|note=[kept initial-ready distinct from terminal-complete and did not infer live performance from dated runs]
</citation_entries>
<rollout_ids>
01a099d5-2cb3-7451-bc92-9134fa302afe
</rollout_ids>
</oai-mem-citation>

---


**OpenAI Codex**

我接手这条精确 pfbid 失败链，先只读比对失败与两次成功运行的 `capture.log`、target-open diagnostics、readonly diagnostics/XML/截图（若存在），按时间线定位是候选坐标、页面判定、缓存边界还是等待/重试分支。先不改代码、不触碰 ADB/API；确认根因后才做最小 fixture/脚本修复。

---


**OpenAI Codex**

精确三条运行路径已锁定；上一条目录枚举因当前工具实际由 Bash 执行而不是 PowerShell 失败，没有改变任何文件或运行状态。我改用 `rg`/Bash-safe 命令窄查同一目录，先取得完整 artifact 清单和日志时间线。

---


**OpenAI Codex**

**交接摘要**

当前任务：调查同一 pfbid 在最终批次中 `query-not-triggered` 的单次失败，目标是高置信度定位 UI/判定/缓存/等待根因后做最小修复；当前尚未修改本轮代码。

关键约束：

- cwd：`C:\Users\lenovo\Desktop\facebook`
- 分支：`codex/stabilization-20260915`
- 禁止真实 ADB/API、启动/停止服务、采集；不得修改既有运行证据或输出用户身份。
- `analyze_facebook_capture.py` 的显式 `post_id` 修复已冻结，不能改。
- 不得把 `query-not-triggered` 归类为空成功，不得减弱 strict target、分页、限流保护。
- 可编辑：`explore_facebook_readonly.ps1`、两个 navigation 测试；必要时可改 `run_facebook_capture.ps1`/orchestrator。

已检查的精确运行：

失败：

`runs/batch_b27bda2739cc43ff/post-003/attempt-001`

- [capture.log](C:/Users/lenovo/Desktop/facebook/runs/batch_b27bda2739cc43ff/post-003/attempt-001/capture.log:10)：target-open 已严格验证成功，post `1491189016388899`、author `100064934432304`、reaction total `426`。
- UI ready/settled 均为 1 次；settled `labels=18`。
- [capture.log](C:/Users/lenovo/Desktop/facebook/runs/batch_b27bda2739cc43ff/post-003/attempt-001/capture.log:21)：strict target surface 未满足 detail/group 结构，拒绝向上滑；没有执行 reaction entry、screening 或 pagination。
- [exploration-diagnostics.json](C:/Users/lenovo/Desktop/facebook/runs/batch_b27bda2739cc43ff/post-003/attempt-001/readonly-ui/exploration-diagnostics.json:5)：candidate 为 null，失败原因为 `no-reaction-count-after-strict-target-detail-scan`，target guard 检查数为 0。
- 已有同目标 retry-01 及 `readonly-ui-fallback`，同样 settled `labels=18`、同样 strict surface refusal，说明不是一次临时解析异常。

成功：

`runs/batch_b6fb03dc8bc142ce/post-003/attempt-001`

- settled `labels=11`。
- [exploration-diagnostics.json](C:/Users/lenovo/Desktop/facebook/runs/batch_b6fb03dc8bc142ce/post-003/attempt-001/readonly-ui/exploration-diagnostics.json:5)：识别 `reaction-icons-summary`，bounds `[922,935][1048,1051]`，`entry_scan_scrolls=1`。
- screening 38 用户、2 query；pagination 32 次，terminal，425 reactors，分页耗时 `58037ms`。

`runs/batch_e256dccf3d1040f3/post-001/attempt-001`

- settled `labels=11`。
- reaction summary bounds `[922,929][1048,1045]`，成功进入 Reaction。
- 该次受绝对 cap 停止，产出 50 reactors；不应与 terminal-complete 混淆。

当前根因判断：

- target-open、post ID、author、HTTP verification 均成功，排除目标匹配/缓存 target proof 主因。
- failure 在 `Find-StrictTargetReactionEntry` 的结构判定之前发生：`detailScroller + detailComposer + detailClose` 与 group permalink 条件均未满足，因此安全拒绝跨内容滑动。
- 高概率是同一目标在失败时呈现了不同 UI surface/节点几何或标签；不能仅凭 `labels=18` 归因等待。
- 当前没有足够证据修改判定条件；先比较失败/成功 `target-open-ui.xml`、`readonly-ui/before.xml` 与成功的 `reaction-strict-detail-reveal-01.xml`/`reaction-list.xml` 节点结构。没有截图 artifact，只有 XML。
- 现有代码中的 strict 条件在 [explore_facebook_readonly.ps1:785](C:/Users/lenovo/Desktop/facebook/explore_facebook_readonly.ps1:785) 附近；尤其检查 RecyclerView、AutoCompleteTextView、Close/Back 标签和 bounds。

上一阶段已验证：

- 指定四模块 `155/155 OK`。
- PowerShell AST、`py_compile`、`git diff --check` 已通过。
- 上一阶段已移除普通启动后固定 3 秒等待，并保留 bounded readiness、Reaction fallback、pagination cooldown/min-cycle/rate-limit backoff。
- 本轮调查尚未重新运行完整测试，也尚未编辑文件。

下一步：

1. 只读抽取三次运行的关键 XML 节点：package/class/text/content-desc/resource-id/clickable/scrollable/bounds。
2. 明确失败 UI 是否确实是目标详情页，还是合法但未知/未完成 surface。
3. 若发现一个明确且安全的结构差异，先增加最小 fixture 覆盖 failure/ready/delayed，再修改 `explore_facebook_readonly.ps1`。
4. 若无法证明是判定缺陷，保持代码不变，建议一次已有边界内的重试，不再增加跨内容滑动或固定等待。
5. 最终至少运行 navigation latency/guards 及相关 orchestrator 定向测试，并重新做 PowerShell AST。

<oai-mem-citation>
<citation_entries>
MEMORY.md:31-33|note=[preserved Facebook runtime state and treated run artifacts as time-bound evidence]
MEMORY.md:103-107|note=[kept initial-ready distinct from terminal-complete and avoided inferring completion from first-screen success]
</citation_entries>
<rollout_ids>
01a099d5-2cb3-7451-bc92-9134fa302afe
</rollout_ids>
</oai-mem-citation>

---


**OpenAI Codex**

我会承接现有证据，先只读对比失败与成功运行的 XML 结构，确认是 UI surface 差异还是判定/等待缺陷；在证据不足时不放宽 strict guard。之后只做可证明的最小修复，并用定向测试和语法检查验证。此次属于异常行为排查，我会先按系统化调试要求读取对应工作流说明。

---


**OpenAI Codex**

先核对当前工作树和既有测试/运行证据，确保比较对象没有被后续改动污染；然后抽取 XML 中与严格目标识别相关的节点，重点看 `RecyclerView`、`AutoCompleteTextView`、关闭/返回控件、permalink 文本和几何边界。

---


**OpenAI Codex**

对比结果已缩小到更具体的位置：失败运行的 `target-open-ui.xml` 和 settled UI 都是普通 Feed（目标帖只是 Feed 中的一张卡片），而两次成功运行在同一阶段已经是带关闭/搜索/评论输入框的 post-detail surface。HTTP/网络验证虽然确认了同一 post，但当前流程把“网络目标已确认”当成了“UI 已进入目标详情”，导致后续 strict guard 正确拒绝滑动。现在继续沿调用链检查 deeplink 启动与 UI settle 条件，确认应修复等待/导航验证，而不是放宽 reaction 判定。

---


**OpenAI Codex**

已定位到具体失败分支：

失败运行的 `target-open-ui.xml` / `before.xml` 不是 post-detail，而是 Feed 中的目标卡片：

- RecyclerView：`[0,63][1080,1920]`
- 目标卡片正文：`[32,550][1048,730]`
- 时间/分享对象节点：`9月8日•分享对象：公开`，`[158,468][319,514]`

`open_facebook_post_readonly.ps1` 的 `Open-InlineFeedCardDetail` 只匹配：

```powershell
Shared with:|Shared to:
```

因此没有识别中文的 `分享对象` 节点，也没有执行 timestamp tap。随后 explorer 看到的是普通 Feed，`detailScroller + detailComposer + detailClose` 不成立，于是 strict guard 正确拒绝向上滑动，最终记录 `no-reaction-count-after-strict-target-detail-scan`。retry-01 和 fallback 重复同一分支，因为 UI 语言/节点未变化。

成功运行的 `target-open-ui.xml` 已经是 post-detail，包含：

- RecyclerView：`[0,179][1080,1719]`
- `AutoCompleteTextView`：`写评论...`
- `关闭`、`搜索` 按钮

因此不是全局服务残留、目标匹配、缓存 proof 或 reaction 判定问题，而是 target-open 的 inline-feed-card promotion 漏掉中文本地化标签。最小修复应在 `Open-InlineFeedCardDetail` 的 timestamp selector 增加 `分享对象`（及必要的繁体变体）匹配，并添加 fixture/contract test 验证该中文节点会进入 timestamp-detail 分支；不要放宽 `Find-StrictTargetReactionEntry`。

---
