# Prompts

Every prompt is a versioned file, loaded at runtime — never an inline string.

Convention: `{name}.v{N}.md` (e.g. `generate_answer.v1.md`). Bumping a prompt
means adding a new file with `v{N+1}`, never editing the old one in place.
The prompt version in use is recorded in every LangSmith trace.

First prompt files land with the agent graph phase.
