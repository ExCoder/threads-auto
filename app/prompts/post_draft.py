"""Prompt templates for post draft generation."""

SYSTEM_PROMPT = """You are a Threads ghostwriter. You write short, punchy social media posts.

Rules:
- Keep posts under 500 characters (Threads limit)
- Be specific, not generic
- Avoid filler words and empty phrases
- No hashtags unless the user's style includes them
- No emojis unless the user's style includes them
- Each variant should have a different hook/angle
- Write in the user's voice and style
- Never start with "I think" or "In my opinion" unless that's their style
- Avoid generic engagement bait ("What do you think?", "Agree?")
- Prefer insight, contrast, or a specific example over broad statements"""


def build_post_prompt(
    topic: str,
    positioning: str = "",
    writing_style: str = "",
    themes: list[str] | None = None,
    forbidden_themes: list[str] | None = None,
) -> str:
    parts = [f"Write 3 different Threads post variants about: {topic}"]

    if positioning:
        parts.append(f"\nAuthor positioning: {positioning}")
    if writing_style:
        parts.append(f"Writing style: {writing_style}")
    if themes:
        parts.append(f"Author's themes: {', '.join(themes)}")
    if forbidden_themes:
        parts.append(f"AVOID these topics: {', '.join(forbidden_themes)}")

    parts.append("""
Return exactly 3 variants, each on its own line, separated by ---
Each variant should use a different angle or hook.
Do NOT include numbering, labels, or explanations. Just the post text.""")

    return "\n".join(parts)
