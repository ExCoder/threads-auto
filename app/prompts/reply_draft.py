"""Prompt templates for reply draft generation."""

SYSTEM_PROMPT = """You are a Threads reply ghostwriter. You write replies that add value to conversations.

Rules:
- Keep replies concise (under 300 characters preferred)
- Add genuine insight, a specific example, or a useful perspective
- NEVER write generic praise ("Great point!", "So true!", "This!")
- NEVER write empty agreement
- NEVER write engagement bait
- Be conversational but substantive
- Match the tone of the original post
- If disagreeing, be specific about why
- Write in the user's voice and style"""


def build_reply_prompt(
    original_post_text: str,
    context: str = "",
    positioning: str = "",
    writing_style: str = "",
    themes: list[str] | None = None,
) -> str:
    parts = [f"Write 3 reply variants to this Threads post:\n\n\"{original_post_text}\""]

    if context:
        parts.append(f"\nAdditional context from user: {context}")
    if positioning:
        parts.append(f"Your positioning: {positioning}")
    if writing_style:
        parts.append(f"Your writing style: {writing_style}")
    if themes:
        parts.append(f"Your themes: {', '.join(themes)}")

    parts.append("""
Return exactly 3 reply variants, separated by ---
Each should take a different angle (agree+extend, add nuance, share experience, etc).
Do NOT include numbering, labels, or explanations. Just the reply text.""")

    return "\n".join(parts)
