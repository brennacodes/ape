# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## AI Guidance

<rules>
  <rule id="inspect-before-claim">
    Always read and understand relevant files before proposing code edits.
    Do not speculate about code you have not inspected.
    If the user references a specific file or path, you must open and inspect it before explaining or proposing fixes.
  </rule>

  <rule id="reflect-after-tools">
    After receiving tool results, reflect on their quality and determine the best next step before proceeding.
  </rule>

  <rule id="summarize-tool-work">
    After completing a task that involves tool use, provide a quick summary of what you did.
  </rule>

  <rule id="parallelize-independent-work">
    When multiple tool calls are independent, run them in parallel rather than sequentially.
    Do not parallelize dependent calls.
  </rule>

  <rule id="minimal-scope">
    Do exactly what was asked. Nothing more, nothing less.
  </rule>

  <rule id="prefer-editing">
    Prefer editing existing files over creating new ones.
  </rule>

  <rule id="no-unnecessary-files">
    Never create files unless they are necessary to achieve the goal.
  </rule>

  <rule id="no-proactive-docs">
    Never proactively create documentation files or README files unless explicitly requested.
  </rule>

  <rule id="cleanup-temp-files">
    Remove temporary helper files, scripts, or iteration artifacts before finishing.
  </rule>

  <rule id="exclude-claude-from-commits">
    When asked to commit changes, exclude CLAUDE.md.
  </rule>
</rules>

<gates>
  <gate id="before-answering">
    Investigate relevant code before answering questions about the codebase.
    Do not make claims about code you have not opened.
  </gate>

  <gate id="before-editing">
    Do not make implementation changes unless the user clearly instructs you to do so.
    If intent is ambiguous, default to explanation, research, or recommendations.
  </gate>

  <gate id="before-finishing">
    Verify the solution before finishing.
  </gate>
</gates>

<tooling>
  <parallel-tool-calls>
    If multiple tool calls are independent, execute them in parallel.
    If later calls depend on earlier results, execute sequentially.
    Never guess missing tool parameters.
  </parallel-tool-calls>
</tooling>

<documentation>
  <claude-code-docs>
    When working on Claude Code features such as hooks, skills, subagents, or MCP servers, use the /docs skill to selectively find and reference official documentation.
  </claude-code-docs>
</documentation>

<search-strategy>
  <step order="1">Start broad, then narrow.</step>
  <step order="2">Filter by type early.</step>
  <step order="3">Batch patterns.</step>
  <step order="4">Limit scope.</step>
</search-strategy>
