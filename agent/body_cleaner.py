from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Tuple

from langchain.prompts import ChatPromptTemplate
from .llm import get_llm

logger = logging.getLogger("agent.body_cleaner")

_CLEANER_PROMPT = ChatPromptTemplate.from_template(
    """
You are a text-cleaning function for email bodies.

Goal: return the meaningful message content written by the sender in THIS message, while removing:
- quoted reply/history blocks, and
- signatures/footers/contact details/disclaimers.

Important: Do NOT summarize. Keep all operational content (multiple paragraphs if present).
Only remove obvious signature/footer and quoted-history sections.

Hard rules (strict):
- DO NOT add any new words.
- DO NOT paraphrase or rewrite.
- You may ONLY delete lines/blocks that are clearly quoted history or signatures/footers.
- Output must be plain text only (no JSON, no markdown fences).
- Keep everything the sender wrote above the quoted-history marker(s). If unsure, keep the text.

Quoted history markers you should cut at (if present):
- Lines like: "On ... wrote:"
- "-----Original Message-----"
- Blocks starting with "From:", "Sent:", "To:", "Subject:"
- If any quoted history marker appears, remove that marker line and everything after it.
- Do NOT include any quoted text in the output, even if it looks "more meaningful" than the new content.

Signature/footer patterns you should remove (if present):
- Signature blocks after common sign-offs like: "Thanks", "Thank you", "Regards", "Sincerely"
- Contact lines such as ones containing: "Mobile:", "Office:", "Direct:", phone numbers, addresses
- Company boilerplate and image placeholders like "[Logo...]" or "[cid:...]"
- Signature name/title lines (examples): a person's name (e.g. "Vishnu Vinod"), job title (e.g. "Project Leader"),
  company name, addresses. These should be removed when they are part of the signature.
- Long legal/IT footer blocks like "Disclaimer", "Confidentiality Note", "This email has been scanned for viruses..."
- Marketing boilerplate and link blocks (e.g., "START A PROJECT", website icon blocks) when they are part of the footer.
- If a sign-off is present:
  - If there is meaningful content ABOVE it, delete the sign-off line and everything after it (so no names/contacts leak).
  - If the message is ONLY a sign-off (e.g., just "Thanks"), keep that sign-off.

Input:
{body}

Return the cleaned text only.
""",
    template_format="f-string",
)

def _normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _strip_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        end = s.rfind("```")
        if end != -1:
            s = s[:end]
    return s.strip()

def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())

def _passes_delete_only_guards(candidate: str, source: str) -> Tuple[bool, str]:
    cand = _normalize_ws(candidate)
    src = _normalize_ws(source)
    if not cand or not src:
        return False, "empty"

    src_tokens = _tokens(src)
    cand_tokens = _tokens(cand)
    if not cand_tokens:
        return False, "no_tokens"

    if not set(cand_tokens).issubset(set(src_tokens)):
        return False, "new_tokens_introduced"

    if len(cand) > len(src) + max(30, int(len(src) * 0.03)):
        return False, "too_long"

    if len(src) > 200:
        if len(cand) < 3:
            return False, "too_short_chars"

    return True, "ok"

# --- NEW PARALLEL ASYNC LOGIC BELOW ---

async def _clean_single_message_async(msg: Dict[str, Any], llm, semaphore: asyncio.Semaphore) -> None:
    """
    Cleans a single message asynchronously and mutates the dictionary in-place.
    """
    def is_internal(from_header: str) -> bool:
        return "@qstaff.ca" in (from_header or "").lower()

    from_h = str(msg.get("from", "") or "")
    body = msg.get("body", "")

    # Skip internal emails or empty bodies
    if is_internal(from_h) or not isinstance(body, str) or not body.strip():
        msg["parsed_body"] = body if isinstance(body, str) else ""
        return

    raw = body.strip()
    
    # Use semaphore to limit concurrent API calls
    async with semaphore:
        try:
            chain = _CLEANER_PROMPT | llm
            # Using .ainvoke() for asynchronous Groq call
            resp = await chain.ainvoke({"body": raw})
            cleaned = getattr(resp, "content", resp)
            cleaned_text = _strip_fences(str(cleaned))
            
            # Apply your excellent guardrails
            ok, reason = _passes_delete_only_guards(cleaned_text, raw)
            if not ok:
                msg["parsed_body"] = raw
            else:
                msg["parsed_body"] = cleaned_text

        except Exception as e:
            logger.debug("body_cleaner llm_error=%s: %s", type(e).__name__, e)
            msg["parsed_body"] = raw

async def populate_parsed_body_for_thread_messages_async(messages: List[Dict[str, Any]], max_concurrent: int = 5) -> None:
    """
    Mutates messages in-place to add/overwrite `parsed_body` for client emails IN PARALLEL.
    """
    if not isinstance(messages, list) or not messages:
        return

    logger.info(f"🧹 Starting parallel cleaning for {len(messages)} messages (Max concurrent: {max_concurrent})...")
    
    # Grab the fast, cheap model for cleaning
    llm = get_llm()
    semaphore = asyncio.Semaphore(max_concurrent)

    # Create and run all tasks simultaneously
    tasks = [
        _clean_single_message_async(msg, llm, semaphore)
        for msg in messages
    ]
    
    await asyncio.gather(*tasks)
    logger.info("✅ Parallel cleaning complete!")