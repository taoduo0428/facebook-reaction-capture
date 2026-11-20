# Implement Mac Android prerequisite setup directly 
_Exported on 09/16/2026 at 21:18:59 GMT+8 from OpenAI Codex via WayLog_


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

Windows-based developer who uses Codex for crawler/emulator tooling, enterprise Leap repositories, MCP, browser/network diagnostics, Gitea deployments, and documentation workflows. Usually writes task requests in Chinese. Values exact evidence, preservation of existing state and user-controlled accounts, and a concrete verified outcome rather than a plan or generic troubleshooting. Works across checkout-specific projects, so cwd is a lead rather than proof of the true target. Uses user-maintained `sub2api` routes and custom API configuration alongside browser/OAuth workflows; keep those authentication paths distinct and do not alter a provider without explicit direction. For cross-platform work, expects a platform layer that does not damage the established runtime and clear labeling of simulated versus real-machine evidence.

## User preferences

- Make requested repair/configuration changes directly when authorized; keep them minimal and reversible, then verify the user-visible or persisted result. For user-facing documentation, confirm whether the target is a visible desktop archive rather than an internal runtime path.
- For "看下最新仓库更新并且同步过来", discover the real checkout and inspect branch/upstream/divergence/worktree first; fast-forward directly only when safe, preserving unrelated untracked files.
- Before editing or deleting, inspect the authoritative path and current state. Preserve unrelated work, credentials, account actions, active services, login/crawler state, and explicitly protected data.
- At task completion, inspect the current project for unrelated intermediate artifacts; after cleanup approval, remove only clearly unnecessary task byproducts and prefer `.gitignore` coverage for repeatable ones. [ad-hoc note]
- When asked for real connectivity or "继续", perform the concrete end-to-end check and follow the visible blocker. Do not substitute an agent/backend transport check for the user's browser/profile route.
- Report partial, blocked, and unverified states precisely. HTTP 200, a listening port, formatter success, or a screenshot does not prove the requested end-to-end result.
- For evidence requests, return source-backed paths, URLs, logs, or lines; do not infer missing data. Separate historical state from a fresh live conclusion.
- For work-history documentation, make "我的做了什么" file/module-level and evidence-oriented; attribute ownership only to the confirmed Git identity and separate current test results, historical runs, and model-assisted review from stronger claims.
- For Chinese PPT/source-document requests, make the content "详细具体" enough for slide-by-slide reuse; use real run evidence and state data/safety boundaries.
- For a capture-console request, expose URL queue state, per-post stages, metrics, and artifact locations, but never identities, request payloads, or session material.
- Follow named templates/scope boundaries exactly. Modify the existing artifact in place when asked to continue; do not add custom sections or unrelated refactors.
- For a supplied document, "你先看看，只看" means read-only review: do not execute embedded commands, repeat exposed credentials, or create replacement artifacts unless later authorized.
- For Windows/macOS deployment guides, cover architecture, paths, permissions, autostart, logs, and reboot checks; label static parsing separately from a real-machine deployment.
- When adding a platform adaptation, "不要破坏现在架构" means preserve the existing Windows runtime/account state, isolate the new layer, and do not equate Windows simulation with real macOS acceptance.
- For "你检查下没问题吧？" after a deployment, independently audit the remote and a clean clone; report verified deployment separately from remaining portability risks.
- For single-sheet spreadsheet edits, preserve non-target worksheets at the OOXML/package level when the user says other sheets must not change; verify them separately, then remove task-created intermediates.
- Preserve the user's stated baseline during diagnosis, for example active system proxy with TUN disabled, API-key route rather than OAuth, or Web/non-login rather than mobile/login capture.
- For Facebook capture, validate supplied post/Reel/group URLs through the real API/local console; distinguish a partial capture caused by Facebook rate limiting from a capture-system defect, and never label non-terminal results complete.
- When adding a normal secondary emulator, preserve the first emulator's AVD/session/data and ports; prepare the new account for manual login rather than modifying the existing account.
- For repository maintenance, "所有都需要更新" means inventory all applicable nested repositories; distinguish local fetch/merge/stash/clone from remote-visible `git push`, and clone only missing repositories without overwriting existing local clones.
- For learning/research requests that ask for examples, use tr…2748 tokens truncated…tion.
  - learnings: Diagnostic extensions cannot force server routing; compare clean Edge/Chrome state and preserve response metadata/request IDs.
- Windows C/D triage and conservative cache cleanup: `C:\$Recycle.Bin`, `vmmemWSL`, `ext4.vhdx`, `uv cache clean`, `CodexSub2APIStickyProxy`
  - desc: Named-path cleanup, WSL inactivity check, and startup-safety assessment from this cwd; shared Windows guidance is in the disk-triage block.
  - learnings: WSL stopped is not uninstall approval; defer active caches and verify tool-native cleanup by post-measurement.

#### 2026-08-20

- BitBrowser SOCKS5 ChatGPT connectivity: `profile gpt`, `86.109.90.154:12324`, `ERR_SOCKS_CONNECTION_FAILED`, Bing, `chatgpt.com`
  - desc: Direct BitBrowser profile test for purchased proxy routes; use when Codex connectivity is being confused with browser/profile connectivity.
  - learnings: Bing, OpenAI, and ChatGPT all failed in the same profile, pointing to the SOCKS route rather than an OpenAI-only block.

#### 2026-08-19

- AdsPower and Clash proxy connectivity diagnosis: AdsPower, Clash, system proxy, `12323`, `12324`, SOCKS5
  - desc: Separates provider reachability from browser-profile, Clash, and Windows proxy readiness.
  - learnings: Preserve the active system proxy while testing; HTTP/HTTPS and SOCKS5 endpoints are distinct.

#### 2026-08-17

- OpenCode Desktop OAuth troubleshooting: `Token exchange failed: 403`, `oauthFetch`, Electron `net.fetch`, `app.asar`, `opencode auth list`
  - desc: Proxy-aware OpenCode ChatGPT OAuth path and persisted-credential checks.
  - learnings: ChatGPT browser login is separate from Codex API auth; nonzero local credentials are the completion check.

### C:\Users\lenovo\Desktop\外包+岗位爬虫

#### 2026-09-05

- DRV crawler Gitea deployment and clean-clone portability audit: `taoduo/drv-market-crawler`, `d33a834b`, `565 passed`, `git fsck --full`, `uv lock --check`, `.jobs/default`
  - desc: `cwd=C:\Users\lenovo\Desktop\外包+岗位爬虫`; private `main` deployment, source/runtime boundary, and independent clean-clone verification.
  - learnings: Deployment and clone tests were valid, but hardcoded Windows output paths, a user-specific pre-commit path, and missing `.jobs/` ignore coverage prevent a full portability claim.

#### 2026-08-19

- EPWK crawler audit and quality enrichment: EPWK, 一品威客, `task.epwk.com`, `crawler.config.yaml`, `EpwkAdapter`, Nuxt `taskData`, `quality_sample`
  - desc: Incremental detail-field audit and real 150-row resampling for `cwd=C:\Users\lenovo\Desktop\外包+岗位爬虫`.
  - learnings: Preserve unknown values; Sitemap `lastmod` and recommendation fields are not task truth. Targeted tests passed while online field coverage remains incomplete.

### C:\Users\lenovo\Desktop\webcodex

#### 2026-08-18

- Soft Workspace V1 implementation and validation: Soft Workspace, Access Scope, Task Project, `ProjectResolver`, `cargo check`
  - desc: Workspace context design, authorization routing, and host linker blocker.
  - learnings: Keep selected workspace, filesystem authority, and immutable task binding separate; MSVC/MinGW linker state blocked compilation.

### Older Memory Topics

#### C:\Users\lenovo\Desktop\破限

- embedded Coding Tools MCP upgrade: `coding-tools-mcp`, `v0.3.0`, `prepare-native-runtime.ps1`, `command_id`
  - desc: `cwd=C:\Users\lenovo\Desktop\破限\gpt-webcodex`; upstream sync plus portable-runtime compatibility.
- Feishu Wiki login routing: Feishu Wiki, `302`, OAuth, SSO
  - desc: `cwd=C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct`; authorized login/access routing without bypass guidance.
- project inspection and Coding Tools MCP OAuth: MCP Connector, OAuth 2.0, `127.0.0.1:8765/mcp`, runtime audit
  - desc: `cwd=C:\Users\lenovo\Desktop\破限\test-mcp`; read-only project inspection and connector configuration evidence.

#### C:\Users\lenovo\.codex

- RTK integration and FastCtx governance: FastCtx, `apply_patch`, `rules/default.rules`, UTF-8 BOM
  - desc: Global file-operation routing, deletion prompts, and PowerShell encoding safeguards.
- Codex Desktop capability switching and runtime safety: `codex_capability_switch.ps1`, MCP, `hidden-stdio-launcher-v3.exe`
  - desc: Reversible MCP capability changes and process/runtime safety checks under `cwd=C:\Users\lenovo\.codex`; retain definitions and revalidate after restart.

#### C:\Users\lenovo\Documents\Codex\2026-08-09\new-chat

- LeapAgent DRV v2 assessment and ModeShadow validation: `drv-v2-submit`, `assessment.go`, RED tests
  - desc: Narrow DRV behavior changes, concrete sample inspection, and validation boundaries.

#### Windows storage and desktop runtimes

- Windows disk triage and Desktop-preserving migration: `D:\DesktopData`, junction, `.crawler-state`, `C:\$Recycle.Bin`, WSL
  - desc: Classify first, then copy/validate before path-preserving migration; for named cleanup inspect process/startup state and keep protected runtime data.

#### C:\Users\lenovo\Desktop\leap
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
- `r5` = `C:/Users/lenovo/.codex/plugins/cache/openai-primary-runtime/spreadsheets/26.905.11957/skills`
### Available skills
- imagegen: Generate or edit raster images when the task benefits from AI-created bitmap visuals such as photos, illustrations, textures, sprites, mockups, or transparent-background cutouts. Use when Codex should create a brand-new image, transform an existing image, or derive visual variants from references, and the output should be a bitmap asset rather than repo-native code or vector. Do not use when the task is better handled by editing existing SVG/vector/code-native assets, extending an established icon or logo system, or building the visual directly in HTML/CSS/canvas. (file: r2/imagegen/SKILL.md)
- openai-docs: Use for Codex models/pricing, scheduled tasks, skills, settings, setup, troubleshooting, customization, automations, and self-knowledge—including 'you,' 'your,' 'this app,' or 'this coding agent' when they refer to Codex—and for OpenAI APIs/products and ChatGPT Work. Also use for model choice/migration, prompting, SDKs, Responses, Realtime, agents, evals, and Chat/Work/Codex comparisons. Do not use for generic app/software tasks that merely mention Codex. (file: r2/openai-docs/SKILL.md)
- plugin-creator: Create and scaffold plugin directories for Codex with a required `.codex-plugin/plugin.json`, optional plugin folders/files, valid manifest defaults, and personal-marketplace entries by default. Use when Codex needs to create a new personal plugin, add optional plugin structure, generate or update marketplace entries for plugin ordering and availability metadata, or update an existing local plugin during development with the CLI-driven cachebuster and reinstall flow. (file: r2/plugin-creator/SKILL.md)
- skill-creator: Create or update a Codex skill with appropriately scoped instructions and any needed supporting resources. (file: r2/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into $CODEX_HOME/skills from a curated list or a GitHub repo path. Use when a user asks to list installable skills, install a curated skill, or install a skill from another repo (including private repos). (file: r2/skill-installer/SKILL.md)
- computer-use:computer-use: Control Windows apps from ChatGPT (file: r3/computer-use/26.901.51231/skills/computer-use/SKILL.md)
- dailywork: Generate and submit DailyWork personal or department daily reports from locally authorized Feishu activity, personal weekly reports from the caller's confirmed historical daily reports, and department weekly reports from confirmed personal weekly reports. Use when a user asks Codex to write, submit, or push a DailyWork daily/weekly report or its Feishu confirmation card. (file: r0/dailywork/SKILL.md)
- documents:documents: Create, edit, redline, and comment on `.docx`, Word, and Google Docs-targeted document artifacts inside the container, with a strict render-and-verify workflow. Use `render_docx.py` to generate page PNGs (and optional PDF) for visual QA, then iterate until layout is flawless before delivering the final document. (file: r4/documents/26.905.11957/skills/documents/SKILL.md)
- lark-cli-router: Route Feishu and Lark requests through the bundled lark-cli and its version-matched embedded Skills. Use for messages, documents, drive, Base, calendar, tasks, approvals, contacts, meetings, wiki, sheets, slides, mail, OKR, attendance, events, and Lark authorization or permission issues. (file: r0/lark-cli-router/SKILL.md)
- literature-review: Conduct comprehensive, systematic literature reviews using multiple academic databases (PubMed, arXiv, bioRxiv, Semantic Scholar, etc.). This skill should be used when conducting systematic literature reviews, meta-analyses, research synthesis, or comprehensive literature searches across biomedical, scientific, and technical domains. Creates professionally formatted markdown documents and PDFs with verified citations in multiple citation styles (APA, Nature, Vancouver, etc.). (file: r1/literature-review/SKILL.md)
- matplotlib: Low-level plotting library for full customization. Use when you need fine-grained control over every plot element, creating novel plot types, or integrating with specific scientific workflows. Export to PNG/PDF/SVG for publication. For quick statistical plots use seaborn; for interactive plots use plotly; for publication-ready multi-panel figures with journal styling, use scientific-visualization. (file: r1/matplotlib/SKILL.md)
- nature-paper2ppt: Build a complete but efficient Nature-style Chinese PPTX presentation from a scientific paper, preprint, PDF, article text, abstract, figure legends, or reading notes. Use this skill whenever the user asks to make slides/PPT/PPTX for journal club, group meeting, paper sharing, thesis seminar, lab meeting, department report, or academic presentation from a research paper, not only medical papers. It identifies the paper type and argument, selects only the figures needed for the story, writes Chinese slide content and speaker notes, creates the actual .pptx deck, and performs lightweight verification with cross-platform Python tooling by default. (file: r0/nature-paper2ppt/SKILL.md)
- networkx: Comprehensive toolkit for creating, analyzing, and visualizing complex networks and graphs in Python. Use when working with network/graph data structures, analyzing relationships between entities, computing graph algorithms (shortest paths, centrality, clustering), detecting communities, generating synthetic networks, or visualizing network topologies. Applicable to social networks, biological networks, transportation systems, citation networks, and any domain involving pairwise relationships. (file: r1/networkx/SKILL.md)
- paper-lookup: Search 10 academic paper databases via REST APIs for research papers, preprints, and scholarly articles. Covers PubMed, PMC (full text), bioRxiv, medRxiv, arXiv, OpenAlex, Crossref, Semantic Scholar, CORE, Unpaywall. Use when searching for papers, citations, DOI/PMID lookups, abstracts, full text, open access, preprints, citation graphs, author search, or any scholarly literature query. Triggers on mentions of any supported database or requests like "find papers on X" or "look up this DOI". (file: r1/paper-lookup/SKILL.md)
- pdf:pdf: Read, create, inspect, render, and verify PDF files where visual layout matters, including fillable AcroForms. Use Poppler rendering plus Python tools such as reportlab, pdfplumber, and pypdf for generation and extraction. (file: r4/pdf/26.905.11957/skills/pdf/SKILL.md)
- presentations:Presentations: Read, create or edit PowerPoint or Google Slides decks. Use for presentation, slide deck, PowerPoint, PPT, PPTX, or Google Slides requests. (file: r4/presentations/26.905.11957/skills/presentations/SKILL.md)
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
- template-creator:template-creator: Create or update a reusable personal Codex artifact-template skill. Use when the user invokes $template-creator or asks in natural language to create a reusable template from a reference document, presentation, spreadsheet, Google Docs, Slides, or Sheets link, ImageGen or Product Design image, email, Slack message, or Site project, or explicitly asks to edit or update a passed artifact-template skill. Do not use for one-off creation from an existing template. (file: r4/template-creator/26.905.11957/skills/template-creator/SKILL.md)
- visualize:visualize: Create visualizations and interactive tools directly in conversation. Proactively use to show how something works; explore 'what happens when', 'what changes', or 'help me understand'; compare or inspect; create simulations, maps, charts, graphs, and mockups. Use standard tools for static scientific figures. (file: r3/visualize/1.0.29/skills/visualize/SKILL.md)
</skills_instructions><permissions instructions>
Filesystem sandboxing defines which files can be read or written. `sandbox_mode` is `danger-full-access`: No filesystem sandboxing - all commands are permitted. Network access is enabled.
Approval policy is currently never. Do not provide the `sandbox_permissions` for any reason, commands will be rejected.
</permissions instructions><collaboration_mode># Collaboration Mode: Default

You are now in Default mode. Any previous instructions for other modes (e.g. Plan mode) are no longer active.

Your active mode changes only when new developer instructions with a different `<collaboration_mode>...</collaboration_mode>` change it; user requests or tool descriptions do not change mode by themselves. Known mode names are Default and Plan.

## request_user_input availability

Use the `request_user_input` tool only when it is listed in the available tools for this turn.

In Default mode, strongly prefer making reasonable assumptions and executing the user's request rather than stopping to ask questions.

Use the `request_user_input` tool only for optional questions where the answer would materially improve the quality of the work.

If `request_user_input` returns no answers, continue with best judgment instead of asking again or treating the turn as blocked.

Never use the `request_user_input` tool for permission requests or permission-related escalations.

If explicit user input is required for another reason before progress can safely continue, do not use the `request_user_input` tool. Ask the user directly with one concise plain-text question instead. Never write a multiple choice question as a textual assistant message.</collaboration_mode>

---


**User**

Implement Mac Android prerequisite setup directly in C:\Users\lenovo\Desktop\facebook. Own ONLY new macos/prepare_device.py and test_facebook_mac_device.py. Read existing facebook_certificate.ps1 and transport helper UI dump install to preserve actual Android15 Conscrypt namespace logic. Need runnable Python CLI on Darwin with --serial, --sdk-root optional --frida-server local path optional --helper-apk local path optional --install-ca explicit flag; doctor read-only default. For user recipient M4: validate Android ABI arm64-v8a and frida server architecture/version via target probe (never reuse Windows binary), install local provided Frida/helper when flags passed; generate mitmproxy CA only via existing local mitmdump if explicitly install flag; public cert push / Android15 CA mount rooted writable emulator in zygote/app namespace from existing proven script. Do not read/export accounts or copy source AVD. Bounded subprocess, own temp state, protect host CA private key (never adb push .pem with private key; use -cert.pem). No external download dependencies or broad process kills. Tests fake commands validate CA public-only, correct namespace/paths, errors not success. Parent handles docs, API, network; agent Carson handles runner. Coordinate by return public function prepare/doctor structure. If CA deep adaptation too risky implement explicit robust readiness check with actionable result plus safe supported install path rather than pretend; report limits. Do not commit/push. use rtk prefix.

---


**OpenAI Codex**

我会先核对现有证书脚本、helper 安装方式和 macOS 代码接口，只新增你指定的两个文件；用假命令测试验证公开证书、命名空间路径和失败处理。

---


**OpenAI Codex**

现有脚本的关键是把系统 CA 目录 bind 到 Conscrypt，并进入 zygote 挂载命名空间。这里会额外验证已运行 Facebook 进程的命名空间；若系统分区不可写，会明确提示准备 rooted writable emulator，不自动重启或关闭 verity。Frida 只接收本地 Android ARM64 ELF，并在目标设备执行 `--version` 验证。

---


**OpenAI Codex**

我会按 writing-plans 和 verification-before-completion 的流程实施和验收，计划保留在本任务中以遵守“两文件”范围：提供可注入命令执行器的 `doctor()` / `prepare()`、实现显式安装路径，再运行假设备测试。两者统一返回 `ok`、`checks`、`actions`、`errors`，便于主任务和 Carson 接入。

---
