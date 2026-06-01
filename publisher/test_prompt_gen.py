"""
test_prompt_gen.py — Test AI-powered DALL-E prompt generation.

Tests the new GPT-4o prompt generator against 3 sample topics
WITHOUT generating any images. Just prints the prompts.

Run: python test_prompt_gen.py
"""

import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# ---------------------------------------------------------------------------
# The new AI prompt builder (to be added to post_generator.py if test passes)
# ---------------------------------------------------------------------------

PROMPT_SYSTEM = (
    "You are a cinematic art director for Gen Z Capital, a dark luxury wealth/AI/automation brand. "
    "Your job: given a post topic and key points, write ONE specific DALL-E 3 image prompt. "
    "The image must be a PHOTO-REALISTIC cinematic scene — NOT a chart, NOT a generic office. "
    "Rules:\n"
    "- Scene must be SPECIFIC to the topic. If topic is about AI replacing jobs, show something "
    "that represents that world visually — e.g. a robot hand signing documents, an empty glass "
    "office with holographic AI interfaces, a human silhouette fading into code.\n"
    "- Dark, moody, cinematic. Single dramatic light source. Deep shadows.\n"
    "- No people's faces visible (silhouettes or backs only).\n"
    "- No text, no watermarks, no UI elements, no phone screens with visible apps.\n"
    "- Shot on RED Komodo 6K, anamorphic lens, f/1.8, ISO 3200 film grain, Kodachrome grade.\n"
    "- Output: ONE paragraph, max 60 words. No intro, no explanation. Just the prompt."
)


def build_dalle_prompt_ai(topic: str, key_points: str, enriched_context: str,
                           client: OpenAI) -> str:
    """Use GPT-4o to generate a topic-specific cinematic DALL-E prompt."""
    user_msg = (
        f"Topic: {topic}\n"
        f"Key Points: {key_points}\n"
        f"Context: {enriched_context[:300] if enriched_context else 'none'}\n\n"
        "Write the DALL-E 3 image prompt."
    )
    resp = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.9,
        max_tokens=120,
        messages=[
            {"role": "system", "content": PROMPT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    return resp.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Test with 3 different topics
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "topic": "AI Tools That Will Replace Your Job",
        "key_points": "automation, ChatGPT, white-collar jobs disappearing, prompt engineers, AI agents",
        "enriched_context": "Goldman Sachs estimates 300 million jobs could be automated. "
                            "Legal, accounting, and coding roles most at risk.",
    },
    {
        "topic": "How Gen Z is Building Wealth Without a 9-5",
        "key_points": "content creation, digital products, no-code tools, leverage, passive income",
        "enriched_context": "73% of Gen Z wants to be self-employed. "
                            "Creator economy worth $250B by 2027.",
    },
    {
        "topic": "The Secret Wealth Gap Nobody Talks About",
        "key_points": "inflation eating savings, stock market vs real estate, compound interest, "
                      "financial literacy, generational wealth",
        "enriched_context": "Top 1% own 32% of all wealth. "
                            "Median Gen Z net worth is negative due to student debt.",
    },
]


def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    print("=" * 60)
    print("AI-POWERED DALL-E PROMPT TEST")
    print("=" * 60)

    for i, case in enumerate(TEST_CASES, 1):
        print(f"\n--- Test {i}: {case['topic']} ---")
        print("Generating prompt...", end=" ", flush=True)
        prompt = build_dalle_prompt_ai(
            case["topic"],
            case["key_points"],
            case["enriched_context"],
            client,
        )
        print("done.")
        print(f"\nPROMPT:\n{prompt}\n")

    print("=" * 60)
    print("Test complete. Review prompts above.")
    print("If they look good, reply and I will add this to post_generator.py")


if __name__ == "__main__":
    main()
