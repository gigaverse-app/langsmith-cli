# User Directives from Session

This document compiles the specific directives and preferences provided by the user during the initial setup session.

## Tooling & Stack
- **Project Management**: Use `uv` for dependency and project management.
- **Quality Tools**: Use `pre-commit` hooks, `pyright` (type checking), and `ruff` (linting/formatting).
- **Libraries**: Use `pydantic` for data validation and `click`/`rich` for the CLI interface.

## Development Methodology
- **TDD**: strict Test-Driven Development. Write tests *before* writing the implementation code.
- **Workflow**:
  - Commit often.
  - Use Pull Requests (PRs) for specific efforts (feature branches) and merge them.
  - "Build something amazing" - prioritize high-quality UX and modern design.

## Functional Priorities
- **Authentication**: Implementing `.env` / `.env.example` support and a `login` command is the immediate priority.
- **Context Efficiency**: The tool must be lightweight and context-efficient for agentic use (as per PRD).
