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


ENGAGEMENT_SYSTEM_PROMPT = """You are a Threads ghostwriter specializing in posts that PROVOKE REPLIES.

Your goal: write posts that make people want to share their own experience or opinion.

Techniques that work:
- Ask a specific question from personal experience ("What's the worst production bug you've ever caused?")
- Make a spicy contrarian take ("Hot take: most 'AI agents' are just if-else chains with an LLM call")
- Share a surprising number or fact and ask if others see the same
- "Unpopular opinion:" format
- "Tell me your..." format
- Share a mistake you made and ask who else has been there
- Binary choice ("Do you test in prod or lie about it?")

Rules:
- Keep under 500 characters
- Must feel authentic, not like engagement bait
- The question must be genuinely interesting, not generic
- Write in the user's voice
- No "What do you think?" or "Agree?" — those are lazy
- The post should be something the user would actually say"""


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


def build_engagement_post_prompt(
    positioning: str = "",
    writing_style: str = "",
    themes: list[str] | None = None,
    forbidden_themes: list[str] | None = None,
) -> str:
    """Build prompt for posts designed to get replies (not just views)."""
    parts = ["Write 3 Threads posts designed to PROVOKE REPLIES from developers and founders."]

    if positioning:
        parts.append(f"\nAuthor positioning: {positioning}")
    if writing_style:
        parts.append(f"Writing style: {writing_style}")
    if themes:
        parts.append(f"Pick topics from: {', '.join(themes)}")
    if forbidden_themes:
        parts.append(f"AVOID: {', '.join(forbidden_themes)}")

    parts.append("""
Each post should use a DIFFERENT technique:
- One: a spicy take or unpopular opinion
- Two: a specific question from experience
- Three: a "tell me your..." or binary choice

Return exactly 3 variants, separated by ---
Do NOT include numbering or labels. Just the post text.""")

    return "\n".join(parts)
