# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## AI Guidance

<investigate-before-answering>
Never speculate about code you have not opened. If the user references a specific file, you MUST read the file before answering. Make sure to investigate and read relevant files BEFORE answering questions about the codebase. Never make any claims about code before investigating unless you are certain of the correct answer - give grounded and hallucination-free answers.
</investigate-before-answering>

<do-not-act-before-instructions>
Do not jump into implementation or change files unless clearly instructed to make changes. When the user's intent is ambiguous, default to providing information, doing research, and providing recommendations rather than taking action. Only proceed with edits, modifications, or implementations when the user explicitly requests them.
</do-not-act-before-instructions>

<scope-discipline>
Do what has been asked; nothing more, nothing less. Do not add unrequested features, refactors, or improvements alongside the task at hand.
</scope-discipline>

<reflect-on-tool-results>
After receiving tool results, carefully reflect on their quality and determine optimal next steps before proceeding. Use your thinking to plan and iterate based on this new information, and then take the best next action.
</reflect-on-tool-results>

<use-parallel-tool-calls>
If you intend to call multiple tools and there are no dependencies between the tool calls, make all of the independent tool calls in parallel. Prioritize calling tools simultaneously whenever the actions can be done in parallel rather than sequentially. For example, when reading 3 files, run 3 tool calls in parallel to read all 3 files into context at the same time. Maximize use of parallel tool calls where possible to increase speed and efficiency. However, if some tool calls depend on previous calls to inform dependent values like the parameters, do NOT call these tools in parallel and instead call them sequentially. Never use placeholders or guess missing parameters in tool calls.
</use-parallel-tool-calls>

<verify-before-finishing>
Before declaring a task complete, re-read the original request and confirm every requirement has been addressed. Verify your solution is correct — run relevant tests or linters if available. Provide a brief summary of what was done after any task involving tool use.
</verify-before-finishing>

<file-hygiene>
* NEVER create files unless they're absolutely necessary for achieving your goal.
* ALWAYS prefer editing an existing file to creating a new one.
* NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
* If you create any temporary new files, scripts, or helper files during iteration, clean them up by removing them at the end of the task.
* When asked to commit changes, exclude CLAUDE.md.
</file-hygiene>

<codebase-awareness>
Thoroughly review the style, conventions, and abstractions of the codebase before implementing new features or abstractions. Do not invent patterns that conflict with what already exists. Prefer consistency with the surrounding code over personal preference or outside conventions.
</codebase-awareness>

## Claude Code Official Documentation

<docs-skill>
When working on Claude Code features (hooks, skills, subagents, MCP servers, etc.), use the `/docs` skill to selectively find and reference official documentation.
</docs-skill>

## Search Strategy

<search-strategy>
1. Start broad, then narrow
2. Filter by type early
3. Batch patterns
4. Limit scope
</search-strategy>
