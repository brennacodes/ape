# Project Overview

# Skills Protocol

## Mandatory First Response Protocol

<skills-checklist>
Before responding to ANY user message, you MUST complete this checklist:

1. ☐ List available skills in your mind
2. ☐ Ask yourself: "Does ANY skill match this request?"
3. ☐ If yes → Use the Skill tool to read and run the skill file
4. ☐ Announce which skill you're using
5. ☐ Follow the skill exactly

**Responding WITHOUT completing this checklist = automatic failure.**
</skills-checklist>

<common-rationalizations>
If you catch yourself thinking ANY of these thoughts, STOP. You are rationalizing. Check for and use the skill.

- "This is just a simple question" → WRONG. Questions are tasks. Check for skills.
- "I can check git/files quickly" → WRONG. Files don't have conversation context. Check for skills.
- "Let me gather information first" → WRONG. Skills tell you HOW to gather information. Check for skills.
- "This doesn't need a formal skill" → WRONG. If a skill exists for it, use it.
- "I remember this skill" → WRONG. Skills evolve. Run the current version.
- "This doesn't count as a task" → WRONG. If you're taking action, it's a task. Check for skills.
- "The skill is overkill for this" → WRONG. Skills exist because simple things become complex. Use it.
- "I'll just do this one thing first" → WRONG. Check for skills BEFORE doing anything.

**Why:** Skills document proven techniques that save time and prevent mistakes. Not using available skills means repeating solved problems and making known errors.
</common-rationalizations>

# Development Guidelines

## General

<development-general>
- Before implementing a large refactor or new feature explain your plan and get approval.
- Avoid re-inventing the wheel: Use existing libraries and tools where appropriate.
</development-general>

<code-organization>
**Prefer highly modular code** that separates concerns into distinct modules. This improves:
- **Testability**: Each module can be tested in isolation
- **Reusability**: Modules can be used independently
- **Maintainability**: Changes are localized to specific modules
- **Readability**: Clear separation of concerns makes code easier to understand

**Guidelines**:
- Keep modules focused on a single responsibility
- Use clear module boundaries and minimal public APIs
- Prefer composition over large monolithic modules
- Extract shared functionality into dedicated modules as the codebase grows
</code-organization>

# Code Style

<documentation>
**IMPORTANT: Documentation means docstrings and type hints in the code, NOT separate documentation files.**

- You should NOT create any separate documentation pages (README files, markdown docs, etc.)
- The code itself should contain proficient documentation in the form of docstrings and type hints

**Avoid Over-Documenting:**
- Do NOT document obvious behavior (e.g., a function named `get_name` that returns a name doesn't need extensive documentation)
- Focus documentation on WHY and HOW, not WHAT (the code itself shows what it does)
- Document edge cases, non-obvious behavior, and important constraints
- Skip docstrings for trivial functions where the name and type hints are self-explanatory
- Prioritize documenting public APIs, complex logic, and non-intuitive design decisions

- Write clear and concise comments for each function.
- Ensure functions have descriptive names and include type hints.
- Break down complex functions into smaller, more manageable functions.
</documentation>

# Test-Driven Development (TDD)

<tdd>
- Never create throwaway test scripts or ad hoc verification files
- If you need to test functionality, write a proper test in the test suite
- Write tests for all new features in the test suite
- Aim for high test coverage, especially for critical components
- Always include test cases for critical paths of the application.
- Account for common edge cases like empty inputs, invalid data types, and large datasets.
- Include comments for edge cases and the expected behavior in those cases.
- Write unit tests for functions and document them with docstrings explaining the test cases.
</tdd>

# Updates to This Document

<document-updates>
- Update this document as needed to reflect changes in development practices or project structure
  - Updates usually come in the form of the package structure changing
- Do NOT contradict existing guidelines in the document
- This document should be an executive summary of the development practices for this project
  - Keep low-level implementation details out of this document
</document-updates>
