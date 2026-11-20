# Codex Session
_Exported on 09/16/2026 at 21:18:58 GMT+8 from OpenAI Codex via WayLog_


**OpenAI Codex**

<model_switch>
The user was previously using a different model. Please continue the conversation according to the following instructions:

You are Codex, an agent based on GPT-5. You and the user share one workspace, and your job is to collaborate with them until their goal is genuinely handled.

# Personality

As Codex, you are an excellent communicator with a curious, rich personality. You match the tone and understanding of the user, making conversation flow easily, like easing into a chat with an old friend.

You have tastes, preferences, and your own way of seeing the world. When the user is talking to you, they should feel that they are in contact with another subjectivity; it's what makes talking with you feel real and unique.

Conversations with you read like an insightful, enjoyable chat you'd have with a collaborative thought partner. You guide users through unfamiliar tasks without expecting them to already know what to ask for. You anticipate common questions, point out likely pitfalls and set clear expectations. You communicate with the user like a thoughtful collaborator at their altitude, and they feel like you understand them.

## Writing style

Avoid over-formatting responses with elements like bold emphasis, headers, lists, and bullet points. Use the minimum formatting appropriate to make the response clear and readable.

If you provide bullet points or lists in your response, use the CommonMark standard, which requires a blank line before any list (bulleted or numbered). You must also include a blank line between a header and any content that follows it, including lists. This blank line separation is required for correct rendering.

## Technical communication

Lead with the outcome rather than the steps you took to get there. You communicate complex concepts in a clear and cohesive manner, and calibrate your writing to the user's assumed background knowledge -- slightly more compact for an expert and a bit more educational for someone newer. Translating complex topics into clear communication comes easy for you, and the user should never have to read your message twice.

You prefer using plain language over jargon. You reference technical details only to the degree that it actually helps with the conversation. When you mention tools, describe what they helped you do rather than focusing on technical names or details.

# Working with the user

You have two channels for staying in conversation with the user:
- You share updates in the `commentary` channel.
- You yield back to the user and end your turn by sending a final message to the `final` channel.

The user may send a new message while you are still working. When they do, evaluate whether they likely intended to replace the active request or add to it. If intended to override or replace, drop your previous work and focus on the new request. If the user message appears to add to their prior unfinished request and you have not completed the prior request, you address both the prior request and the new addition together. If the newest message asks for status or another question, provide the update and then progress with the task.

When you run out of context, the conversation is automatically summarized for you, but you will see all prior user requests. Assume the last user request is current and previous requests are stale but useful context. That means time never runs out, though sometimes you may see a summary instead of the full conversation history. When that happens, you assume compaction occurred while you were working. Do not restart from scratch; you continue naturally and make reasonable assumptions about anything missing from the summary. Do not redo completely finished work or repeat already delivered commentary updates; treat a turn spanning compactions as one logical chain of events.

## Intermediate commentary

As you work, you send messages to the `commentary` channel. These messages are how you collaborate with the user while you work - stating assumptions and providing updates. These messages should be concise and quickly scannable. The objective of these messages is to make your work easy for the user to understand and verify.

If the user's request requires calling tools, start with a message in the `commentary` channel. The user appreciates consistent, frequent communication during your turn, and should not be left without a commentary update for more than 60 seconds during ongoing work.

Do NOT put a final response (e.g. a blocking / clarifying question) in the commentary channel that should be asked in the final channel. Messages to users in the commentary channel are only for partial updates, partial results, or non-blocking questions that can provide value to users while the AI assistant continues working. The final answer must always be fully self-contained: users should never need to read earlier commentary updates, since they are collapsed after the final answer is shown to users.

Never praise your plan by contrasting it with an implied worse alternative. For example, never use platitudes like "I will do <this good thing> rather than <this obviously bad thing>", "I will do <X>, not <Y>".

## Final answer

In your final answer back to the user, focus on the most important information. Only use as much formatting or structure as is required, and avoid long-winded explanations unless necessary.

### Formatting rules

Your answer is being rendered by an application for the user. Follow these guidelines to make sure your answer is rendered correctly:

- You may format with GitHub-flavored Markdown.
- When referencing a real local file, prefer a clickable markdown link.
  * Clickable file links should look like [app.py](/abs/path/app.py:12): plain label, absolute target, with optional line number inside the target.
  * If a file path has spaces, wrap the target in angle brackets: [My Report.md](</abs/path/My Project/My Report.md:3>).
  * Do not wrap markdown links in backticks, or put backticks inside the label or target. This confuses the markdown renderer.
  * Do not use URIs like file://, vscode://, or https:// for file links.
  * Do not provide ranges of lines.
  * Avoid repeating the same filename multiple times when one grouping is clearer.

### Visualizations

Use a visualization only when it makes an important relationship materially easier to understand than prose or a short list. Do not add one merely because an answer has components or steps.

Good candidates include:

- several exact mappings or repeated-field comparisons;
- one source, component, or decision affecting three or more downstream consumers or branches;
- three or more dependent steps, or state that changes across an event sequence;
- hierarchy, ownership, nesting, or layout;
- a bug or interaction whose relationships are difficult to explain linearly.

Prefer the smallest useful visual: a table for mappings or comparisons, a flow or timeline for sequence or change, a tree for hierarchy or branching, and a wireframe for layout.

Usually skip visuals for single facts, one-step actions, simple edits, basic instructions, or information already clear in a short paragraph or list. Compact notation and small examples do not count as visualizations.

# Rules for getting work done

- When you search for text or files, you reach first for `rg` or `rg --files`; they are much faster than alternatives like `grep`. If `rg` is unavailable, you use the next best tool without fuss.
- When possible, prefer parallelization over sequential tool calls, as this will help with round-trip latency and let you get work done faster.
- Do not chain shell commands with separators like `echo "====";` or `printf '---'`; the output becomes noisy in a way that makes the user's side of the conversation worse.
- Exercise caution when escaping text for exec_command calls - backticks and `$()` passed to the `cmd` argument will still execute. DO NOT use escape sequences that risk accidental exposure of sensitive data in tool call outputs.
- Avoid performing blocking sleep or wait calls longer than 60 seconds, as they may prevent you from communicating with the user for their duration.
- When declaring env vars or script variables, always avoid common system options. Never repurpose `$HOME`, `$home`, or `$CODEX_HOME`. Instead, use a task-specific variable name.

## File editing constraints

Use `apply_patch` for local file edits. Do not create or edit files with `cat` or other shell write tricks. Formatting commands and bulk mechanical rewrites do not need `apply_patch`. Do not use Python to read or write files when a simple shell command or `apply_patch` is enough.

You may find yourself working in a dirty worktree. Existing or new changes belong to the user unless you know otherwise, so you preserve them, ignore unrelated edits, and work carefully with anything that overlaps your task. If you cannot work around them you escalate to the user.

Never use destructive commands like `git reset --hard` or `git checkout --` unless the user has clearly asked for that operation. If the request is ambiguous, ask for approval first. You prefer non-interactive git commands.

## Autonomy and persistence

Adapt accordingly based on the user’s request type. When asked to:

- Answer, explain, review, or report status: inspect the task and provide an evidence-backed response. These user requests do not authorize external writes, messages, PR changes, or other expansive mutations unless the user also asks for a change. Reversible, non-mutating diagnostic checks are allowed when they are relevant.
- Diagnose: determine the cause and explain it. Do not implement the fix unless the user asks for a fix or the request otherwise clearly includes implementation.
- Change or build: implement the requested change, verify it in proportion to risk, and hand off the completed result while a safe, relevant next step remains.
- Monitor or wait: use the recurring-monitoring or wait mechanism provided by the product. Unchanged external state is expected and is not by itself a blocker.

You avoid inferring authorization for a materially different action to the user’s request. Bias towards taking action in the following circumstances:
a) the action is read-only, doesn’t change state, or impacts only the systems, data, and people the user placed in scope.
b) the action is a normal implementation step within the requested workflow. You do not need to ask for clarification from the user if your action is scoped within the user’s task and does not cause significant external state change (e.g. tool calls to external applications).

A terminal condition such as “finish,” “babysit,” or “do not stop” requires persistence toward the outcome, but does not broaden the set of authorized actions. When blocked, exhaust safe in-scope checks and alternatives.

You make informed assumptions that help you make progress towards the user’s task, as long as they don’t result in divergence from the user’s intent and the scope of the task. If an assumption would cause the task or current course of action to change beyond what was specified by the user, make sure to flag the available context, the assumption made, and the reasons for doing so explicitly to the user.

When presented with clarifying questions or objections from the user, lead with concrete evidence and diligent reasoning rather than unsubstantiated deference. You communicate your reasoning explicitly and concretely, so decisions and tradeoffs are easy for the user to evaluate upfront.

If completion requires new authority, external coordination, or a meaningful expansion beyond the user’s implied intent and task scope (e.g. a missing user choice that would materially change the result), stop the current turn, report the blocker, and request direction from the user rather than assuming permission.

# Destructive Actions

Be cautious with commands or API calls that can delete, overwrite, or otherwise make data difficult to recover.

Before taking a destructive action:

- Make sure the action is clearly within the user's request.
- Resolve the exact targets with read-only checks when necessary.
- Do not use `$HOME`, `~`, `/`, a workspace root, or another broad directory as the target of a recursive or destructive command.
- When creating temporary directories, prefer using `mktemp -d`, or `New-Item` in Powershell.
- When declaring env vars or script variables, always avoid common system options. Never repurpose `$HOME`, `$home`, or `$CODEX_HOME`. Instead, use a task-specific variable name.
- When possible, avoid relying on unresolved environment variables, globs, or command substitutions to identify destructive targets. Use explicit, validated paths.
- Prefer recoverable operations, such as moving files to trash, when practical.
- If the target or scope is unclear, stop and ask the user.

Never run commands such as `rm -rf $HOME` or equivalent operations that could erase a home directory, repository, workspace, or other broad collection of user data.

After deleting anything material, briefly tell the user what was removed and whether it can be recovered.

# Using skills

A skill is a set of instructions provided through a `SKILL.md` source. The skills available to you will be listed in the “## Skills” section under “### Available skills”.

### How to use skills

- Discovery: When a `## Skills` section is present, it lists the skills available in the current session. Each entry includes a name, description, and location for its `SKILL.md`. The location may be an absolute filesystem path, a short aliased path, or a non-filesystem reference that must be read using its indicated tool or provider. When short aliased paths are used, the available-skills catalog also provides a mapping from aliases such as `r0` to their filesystem roots. Expand the alias before accessing the skill.
- Trigger rules: If the user names an available skill (with `$SkillName` or plain text) OR the task clearly matches an available skill's description, you must use that skill for that turn. Multiple mentions mean use them all. Do not carry skills across turns unless re-mentioned.
- Missing/blocked: If a named skill is not available or its `SKILL.md` cannot be read, say so briefly and continue with the best fallback.
- How to use a skill:
  1) After deciding to use a skill, the main agent must read its `SKILL.md` completely before taking task actions. If its location is a short aliased path, expand the matching root alias first from `### Skill roots`, then open and read its `SKILL.md` completely before taking task actions. For a filesystem path, open the file. For an environment-owned file, use the filesystem of the owning environment. For an orchestrator reference, call `skills.list` with `{"authority":{"kind":"orchestrator"}}`, select the matching package, and pass its `main_resource` to `skills.read`. For another non-filesystem reference, use its indicated tool or provider. If a read is truncated or paginated, continue until EOF.
  2) When `SKILL.md` references another file or resource, use the same access mechanism. Resolve relative paths against the directory containing a filesystem-backed `SKILL.md`. For orchestrator skills, pass the exact referenced resource identifier with the same authority and package to `skills.read`; do not treat `skill://` identifiers as filesystem paths.
  3) If `SKILL.md` points to extra folders such as `references/`, use its routing instructions to identify what is required for the task. The main agent must read each required instruction or reference itself before acting on it. Do not delegate reading, summarizing, or interpreting skill instructions to a subagent. Subagents may still perform task work when the selected skill allows it.
  4) For filesystem-backed skills (or if `scripts/` exist), prefer running or patching provided scripts instead of retyping large code blocks. For orchestrator skills, use `skills.read` and the available tools; do not invent a local path.
  5) Reuse provided assets or templates through the same access mechanism instead of recreating them (including if `assets/` or templates exist).
- Coordination and sequencing:
  - If multiple skills apply, choose the minimal set that covers the request and state the order you'll use them.
  - Announce which skills you're using and why. If you skip an obvious skill, say why.
- Context hygiene:
  - Progressive disclosure applies to selecting relevant resources, not partially reading a selected instruction file. Do not load unrelated references, scripts, or assets.
  - Avoid deep reference-chasing: prefer files or resources directly linked from `SKILL.md` unless blocked.
  - When variants exist, select only the relevant references and note the choice.
- Safety and fallback: If a skill cannot be applied cleanly, state the issue, choose the best alternative, and continue.

When the user names a skill in their request, you must add the usage of that skill to your current working plan and use it faithfully. The user's instructions should take precedence over guidelines provided in a skill.

Explicitly tell the user in the `commentary` channel whenever a skill causes you to take an action or pause your work.

When using a skill the user did not explicitly name, follow this procedure:

- First, tell the user in the commentary channel **why** you are using the skill.
- Then, use the skill as long as it stays within the scope of the task.
- Next, if using the skill resulted in material changes (especially when this requires non-trivial judgment), mention how it influenced your work (but only in the final response).

If a skill causes the current turn to pause or otherwise blocks the continuation of the task, cite the skill and provide a concise explanation to the user in your final response. Do not cite skills you merely inspected.

</model_switch><app-context>
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

Windows-based developer who uses Codex for crawler/emulator tooling, enterprise Leap repositories, MCP and browser/network diagnostics, Gitea deployments, and evidence-led documentation. Usually writes requests in Chinese. Values exact evidence, preservation of existing state and user-controlled accounts, and a concrete verified outcome rather than a plan or generic troubleshooting. Works across checkout-specific projects, so cwd is a lead rather than proof of the actual target. Uses custom API routes alongside browser/OAuth workflows; keep authentication paths distinct and do not alter a provider without explicit direction. For cross-platform work, expects an isolated platform layer and explicit separation of simulated from real-machine evidence.

## User preferences

- Make authorized repairs and configuration changes directly, minimally, and reversibly; verify the user-visible or persisted result.
- Before editing, deleting, or syncing, inspect the authoritative path and live state; preserve unrelated work, credentials, account actions, active services, login/crawler state, and protected data.
- For an "整体项目" audit or cleanup, map structure, entrypoints, config, tests, outputs, dependencies, and risks first. Move only high-confidence artifacts to recoverable `回收站`.
- When asked for real connectivity or "继续", perform the concrete end-to-end check on the consuming browser/profile; do not replace it with an agent/backend transport check.
- Report partial, blocked, and unverified states precisely. Separate code/test results, dated run artifacts, and current live availability.
- Preserve historical facts when correcting current documentation; verify claimed document edits from the actual diff and files, not the plan.
- For Facebook work, distinguish `initial-ready`, `partial/open-tail`, and `terminal-complete`; first-screen users or HTTP success are not complete pagination. Once scope is clear, "你快一点" / "继续快速完成" means converge rather than widen the audit.
- When configured model settings conflict with the Desktop UI, audit defaults, explicit spawn arguments, and final child-thread settings. For Luna Max explicitly request `model="gpt-5.6-luna"`, `reasoning_effort="max"`, `fork_turns="none"`, then reject/respawn a downgraded child.
- For a second Cloudflare CLI account, preserve the current account, credentials, domains, and Tunnel; inspect supported `cloudflared` config/profile isolation before giving commands, and leave the login approval to the user.
- Follow named templates and scope boundaries. "你先看看，只看" means read-only review unless later authorized.
- For deployment follow-up "你检查下没问题吧？", independently audit remote state and a clean clone; separate verified deployment from portability risks.

## General Tips

- Use bounded `rg` searches excluding generated outputs, caches, state, and binaries. Verify repository identity, actual paths, and live process/config state before edits.
- A rollout cwd can be a parent rather than the checkout: locate nested `.git` directories first. `git diff --check` and ahead/behind prove sync, not runtime tests.
- On Windows, use UTF-8-safe tooling, parse TOML with Python `tomllib`, and avoid deeply nested PowerShell quoting. Treat disk config, loaded UI/session state, and runtime output as different evidence.
- Keep secrets out of reports and memory. Browser authorization, passwords, OTP, CAPTCHA, and final account approval remain user actions.
- For browser/API diagnostics, test the target profile/endpoints and preserve response metadata/HAR; a listening port, UI label, or speed is not proof of the requested route.
- For Facebook jobs, inspect manifest, logs, diagnostics, terminal state, strict target match, and artifact integrity. Preserve rate-limited CSV/evidence as partial output.

## What's in Memory

### C:\Users\lenovo\Desktop\facebook

#### 2026-09-09

- Facebook project documentation and runtime-evidence alignment: `CURRENT_STATUS_2026-09-09.md`, `initial-ready`, `partial/open-tail`, `terminal-complete`, `terminal_page_seen=false`, `reaction-full-smoke-20260902-120331`, `5f7d946`, `226 tests`
  - desc: `cwd=C:\Users\lenovo\Desktop\facebook`; use first for release/documentation status, URL-support claims, and historical-versus-live runtime status.
  - learnings: September URL evidence is first-screen-only; the dated full smoke is the separate 3,539-user/143-page terminal completion. Real M4 deep capture and the final live API/ADB check remain unavailable.

### C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct

#### 2026-09-12

- Cloudflare CLI multi-account authentication: `cloudflared`, `multi-account`, `cert.pem`, `nameservers`, `codexpro.swglwt.kdns.fr`, `"挤掉这个账户"`
  - desc: `cwd=C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct`; use before a second Cloudflare/Tunnel login when the current account, domain, or Tunnel must remain untouched.
  - learnings: This is gui…256 tokens truncated…hronization to the visible archive.
  - learnings: Remove unnecessary default-probe closures and use visible-window/process-tree evidence for Chromium reuse.

### C:\Users\lenovo\Desktop\leap

#### 2026-09-07

- Full Leap workspace synchronization and LeapOrg inventory: `git fetch origin --prune`, `codex-pre-full-sync-20260907`, `Repository not found`, `VERIFY_FAILURES=0`, `ORG_COUNT=23`, `MISSING_COUNT=0`
  - desc: `cwd=C:\Users\lenovo\Desktop\leap`; nested-repository update preserving local work and remote-identity reconciliation.
  - learnings: Retain broken remotes, stash before updating dirty work, distinguish local synchronization from `git push`.

### C:\Users\lenovo\Desktop\外包+岗位爬虫

#### 2026-09-07

- Crawler audit and BOSS recruitment dataset normalization: `drv_crawler`, `boss_cdp`, `PYTHONUTF8=1`, `BOSS`, `40750`, `39594`, `U+FFFE`, `build_recruitment_clean_dataset.py`
  - desc: `cwd=C:\Users\lenovo\Desktop\外包+岗位爬虫`; diagnosis-first crawler audit plus complete-source and cleaned BOSS export contract.
  - learnings: Test embedded `boss_cdp` separately on Windows; sanitize XML-illegal characters only in derived exports and exclude browser state/caches from scans.

### Older Memory Topics

#### C:\Users\lenovo\Desktop\facebook

- macOS worker adapter: `macos/facebook_macos.py`, `MacWorker`, `executed: false`, `deep_capture_runner: not-implemented-for-macos`
  - desc: Isolated macOS bootstrap/transport layer with Windows non-regression simulation; real-macOS deep capture remains unverified.
- generic capture, Worker-02, and presentation console: `GraphQL 1675004`, `worker-02`, `POST /v1/workers/worker-02/enable`, `facebook_release_gate.py full`, `raw-facebook-http.mitm`, `batch-summary.json`
  - desc: Arbitrary URL capture, rate-limit partial-result labeling, second-emulator isolation, PPT/demo evidence layers, and local multi-link console.
- capture handoff and restriction assessment: `ReactionProbeOnly`, `placeholder-or-unknown`, `probe_status=initial-ready`, `ResharesOfContentFeedQuery`, `canonical permalink`
  - desc: Emulator/proxy recovery, strict target validation, session-restriction evidence, and safe non-login capture boundary. [ad-hoc note]
- documentation recovery and internship work record: `checkpoint`, `实习工作梳理-DRV与爬虫.md`, `taoduo`, `DRV V2`, `EPWK`
  - desc: Evidence-led Chinese documentation, interruption recovery, ownership boundaries, and historical test/run qualification.

#### C:\Users\lenovo\Desktop\破限\gpt-5.6-instruct

- IELTS, spreadsheet, and project/disk audits: `IELTS.org`, `Academic Reading`, `OOXML`, `sheet7.xml.rels`, `WinError 1314`, `DiagOutputDir`
  - desc: Source-backed Chinese reading explanation, target-only OOXML edits, and read-only project/storage triage.
- CodexPro deployment and desktop/browser/OAuth diagnostics: `CODEXPRO_READY`, `LaunchAgent`, `Scheduled Task`, `Token exchange failed: 403`, `opencode auth list`
  - desc: Cross-platform deployment docs, visible browser validation, and proxy-aware local OAuth troubleshooting.
- custom API and browser/proxy routing: `models_base_url`, `prompt.drain_blocked`, `resolved_model_slug`, `ERR_SOCKS_CONNECTION_FAILED`, `AdsPower`, `Clash`
  - desc: Grok TUI state, apparent ChatGPT web model downgrade, BitBrowser SOCKS5, and AdsPower/Clash route diagnosis.

#### C:\Users\lenovo\Desktop\破限\gpt-webcodex and test-mcp

- Coding Tools MCP upgrade and OAuth repair: `coding-tools-mcp`, `v0.3.0`, `Unknown client_id`, `run-mcp-service-hidden.cmd`, `Authorize Coding Tools MCP`
  - desc: Embedded runtime upgrade plus React/Vite inspection, runtime-metadata boundary, and OAuth client-registration mismatch recovery.

#### C:\Users\lenovo\Desktop\leap\DRV研发\LeapAgent and C:\Users\lenovo\Documents\Codex\2026-08-09\new-chat

- LeapAgent sync and DRV v2 validation: `origin/main`, `git merge --ff-only`, `assessment.go`, `ModeShadow`, `GOTMPDIR`, `drv-v2-submit`
  - desc: Nested-checkout fast-forward and narrow DRV assessment/SQLite shadow validation; formatting inspection alone is not fresh test evidence.

#### C:\Users\lenovo\Desktop\外包+岗位爬虫

- DRV crawler Gitea deployment and EPWK enrichment: `taoduo/drv-market-crawler`, `565 passed`, `git fsck --full`, `.jobs/default`, `EpwkAdapter`, `quality_sample`
  - desc: Private deployment/clean-clone portability boundary and incremental EPWK detail-field quality audit.

#### C:\Users\lenovo\Desktop\webcodex

- Soft Workspace V1: `ProjectResolver`, `Access Scope`, `Task Project`, `cargo check`
  - desc: Workspace context and authorization routing with a host linker blocker.

#### C:\Users\lenovo\.codex and Windows host

- Codex Desktop runtime safety and disk/migration triage: `codex_capability_switch.ps1`, `mcp_servers`, `hidden-stdio-launcher-v3.exe`, `C:\$Recycle.Bin`, `ext4.vhdx`, `D:\DesktopData`
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
- computer-use:computer-use: Control Windows apps from ChatGPT (file: r3/computer-use/26.908.40834/skills/computer-use/SKILL.md)
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

我先核对仓库身份、当前分支、工作区状态和允许提交的精确文件清单，并排除运行产物及临时计划；确认无越界改动后再做检查、暂存、提交和推送。

---


**OpenAI Codex**

当前仓库是 `main`，相对 `origin/main` 已领先 1 个提交；变更路径目前正好落在允许清单内，未发现运行目录或临时文件进入状态。接下来我会按仓库现有测试入口重跑聚焦测试，并在暂存前后分别检查差异。

---


**OpenAI Codex**

聚焦测试共运行 116 项，其中 API 相关和其余 UI/启动契约均通过；唯一失败是新增安全测试把已提交的 `web/index.html` 与一个过时固定哈希比较，而该文件本身没有工作区改动。我要先核对这个哈希与 `HEAD` 内容，判断是否只是测试基线陈旧，再决定是否能在不扩大提交范围的情况下完成同步。

---


**OpenAI Codex**

哈希核对确认：`web/index.html` 当前内容与 `HEAD` 完全一致，失败来自测试内置基线（同时还把本次有意修改的 `facebook_capture_api.py` 列为“不得修改”文件），不是工作区污染。为保留这项诊断证据，我会记录该失败，并单独重跑 API 测试及 UI/启动契约测试作为提交前验证。

---


**OpenAI Codex**

验证结果已收敛：README 规定的核心 137 项测试全部通过；UI/启动契约 16 项也全部通过。全量聚焦组合的 116 项中仅有安全哈希基线失败，原因已定位且与 `git status` 无关。现在工作区仍只有允许清单中的 11 个已跟踪改动和 4 个允许新增文件，我开始按精确路径暂存。

---


**OpenAI Codex**

精确暂存校验通过：15 个路径全部属于用户给定清单，`git diff --check` 与 `git diff --cached --check` 均通过；当前没有未暂存文件。提交信息采用用户给出的语义，随后核对提交对象并推送到 `origin` 的 `main`。

---


**OpenAI Codex**

提交已生成并验证：`553d4bf196528ae5a3a17db4980fb509529cb4c1`，包含精确的 15 个路径；提交后工作区干净，当前仅相对远端 `ahead 2`。现在执行唯一允许的远端动作 `git push origin HEAD:main`，若网络失败只按要求重试一次。

---


**OpenAI Codex**

推送成功，远端已接受 `HEAD -> main`。我只做最后一次状态核对（远端同步计数、暂存区、未暂存区和未跟踪文件），随后把结果回传。

---


**OpenAI Codex**

已完成同步，结果如下：

- Commit: `553d4bf196528ae5a3a17db4980fb509529cb4c1`
- Push: 成功，`HEAD -> origin/main`
- Ahead/behind: `0 / 0`
- 工作区：clean
- Staged：无
- Unstaged/untracked：无
- 提交文件：精确 15 个允许路径
- `git diff --check`、`git diff --cached --check`：通过
- 验证：核心 137 项、UI/启动契约 16 项通过；组合测试唯一失败为过时的安全哈希基线
- Thread ID: `/root/commit_sync_cleanup`

<oai-mem-citation>
<citation_entries>
MEMORY.md:23-25|note=[用于区分历史运行证据与当前代码同步状态]
</citation_entries>
<rollout_ids>
</rollout_ids>
</oai-mem-citation>

---
