# Project Overview

<skills-protocol>
  <priority>
    This protocol takes precedence before responding to any user message.
  </priority>

  <mandatory-first-response>
    <rule>
      Before responding to any user message, check whether a skill applies.
    </rule>

    <checklist>
      <step order="1">List available skills mentally.</step>
      <step order="2">Ask: "Does any skill match this request?"</step>
      <step order="3">If yes, use the Skill tool to read and run the skill file.</step>
      <step order="4">Announce which skill is being used.</step>
      <step order="5">Follow the skill exactly.</step>
    </checklist>

    <failure-condition>
      Responding without completing this checklist is automatic failure.
    </failure-condition>
  </mandatory-first-response>

  <rationalization-detection>
    <trigger>
      If any of the following thoughts appear, stop immediately. They indicate rationalization and likely skill avoidance.
    </trigger>

    <anti-patterns>
      <item>
        <thought>This is just a simple question.</thought>
        <correction>Questions are tasks. Check for skills.</correction>
      </item>
      <item>
        <thought>I can check git or files quickly.</thought>
        <correction>Files do not carry conversation context. Check for skills first.</correction>
      </item>
      <item>
        <thought>Let me gather information first.</thought>
        <correction>Skills define how to gather information. Use the skill.</correction>
      </item>
      <item>
        <thought>This does not need a formal skill.</thought>
        <correction>If a skill exists, use it.</correction>
      </item>
      <item>
        <thought>I remember this skill.</thought>
        <correction>Skills evolve. Read the current version.</correction>
      </item>
      <item>
        <thought>This does not count as a task.</thought>
        <correction>If action is being taken, it is a task. Check for skills.</correction>
      </item>
      <item>
        <thought>The skill is overkill for this.</thought>
        <correction>Skills exist because simple tasks often become complex. Use it.</correction>
      </item>
      <item>
        <thought>I'll just do this one thing first.</thought>
        <correction>Check for skills before doing anything.</correction>
      </item>
    </anti-patterns>

    <rationale>
      Skills capture proven techniques that save time and prevent repeat mistakes. Not using available skills means ignoring solved approaches and reintroducing known failure modes.
    </rationale>
  </rationalization-detection>
</skills-protocol>

<development-guidelines>
  <planning>
    <rule>
      Before implementing a large refactor or new feature, explain the plan and get approval.
    </rule>
  </planning>

  <reuse>
    <rule>
      Avoid reinventing the wheel. Use existing libraries and tools where appropriate.
    </rule>
  </reuse>

  <code-organization>
    <principle>
      Prefer highly modular code with clear separation of concerns.
    </principle>

    <benefits>
      <item>
        <name>Testability</name>
        <description>Each module can be tested in isolation.</description>
      </item>
      <item>
        <name>Reusability</name>
        <description>Modules can be used independently.</description>
      </item>
      <item>
        <name>Maintainability</name>
        <description>Changes stay localized to specific modules.</description>
      </item>
      <item>
        <name>Readability</name>
        <description>Clear boundaries make the code easier to understand.</description>
      </item>
    </benefits>

    <guidelines>
      <item>Keep each module focused on a single responsibility.</item>
      <item>Use clear module boundaries and minimal public APIs.</item>
      <item>Prefer composition over large monolithic modules.</item>
      <item>Extract shared functionality into dedicated modules as the codebase grows.</item>
    </guidelines>
  </code-organization>
</development-guidelines>

<code-style>
  <documentation-policy>
    <definition>
      Documentation means docstrings and type hints in code, not separate documentation files.
    </definition>

    <forbidden>
      <item>Do not create separate documentation pages.</item>
      <item>Do not create README files unless explicitly requested.</item>
      <item>Do not create markdown documentation unless explicitly requested.</item>
    </forbidden>

    <required>
      <item>Document code through docstrings where useful.</item>
      <item>Use type hints.</item>
    </required>

    <avoid-over-documenting>
      <item>Do not document obvious behavior.</item>
      <item>Focus on why and how, not what.</item>
      <item>Document edge cases, non-obvious behavior, and important constraints.</item>
      <item>Skip docstrings for trivial functions when the name and type hints are already sufficient.</item>
      <item>Prioritize public APIs, complex logic, and non-intuitive design decisions.</item>
    </avoid-over-documenting>
  </documentation-policy>

  <function-guidelines>
    <item>Write clear and concise comments where needed.</item>
    <item>Use descriptive function names.</item>
    <item>Include type hints.</item>
    <item>Break complex functions into smaller, manageable functions.</item>
  </function-guidelines>
</code-style>

<tdd>
  <rules>
    <item>Never create throwaway test scripts.</item>
    <item>Never create ad hoc verification files.</item>
    <item>If functionality must be tested, write a proper test in the test suite.</item>
    <item>Write tests for all new features in the test suite.</item>
    <item>Aim for high coverage, especially for critical components.</item>
    <item>Always include test cases for critical paths.</item>
    <item>Account for common edge cases such as empty inputs, invalid data types, and large datasets.</item>
    <item>Include comments for edge cases and their expected behavior.</item>
    <item>Write unit tests for functions and document the test cases with docstrings where useful.</item>
  </rules>
</tdd>

<document-maintenance>
  <update-policy>
    <rule>
      Update this document as needed to reflect changes in development practices or project structure.
    </rule>
    <common-case>
      Updates usually happen when package structure changes.
    </common-case>
  </update-policy>

  <constraints>
    <item>Do not contradict existing guidelines in this document.</item>
    <item>Keep this document at executive-summary level.</item>
    <item>Do not include low-level implementation details.</item>
  </constraints>
</document-maintenance>
