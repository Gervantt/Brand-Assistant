"""Manual smoke tool for the LLM router.

uv run python -m brand_api.llm.cli "Какой тон голоса у бренда?"
uv run python -m brand_api.llm.cli --stream "Напиши пост про новый латте"
uv run python -m brand_api.llm.cli --chain-only "..."
"""

import argparse
import asyncio
import sys

from brand_api.config import get_settings
from brand_api.db import create_engine, create_sessionmaker
from brand_api.llm.errors import AllModelsFailedError
from brand_api.llm.registry import build_router
from brand_api.llm.types import ChatRequest, Message, StreamDone, TextDelta, Tier
from brand_api.logging_setup import configure_logging
from brand_api.observability.llm_calls import DbCallRecorder

SYSTEM = "Ты — ассистент креативного агентства. Отвечай кратко и по-русски."


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Send one prompt through the LLM router")
    parser.add_argument("prompt")
    parser.add_argument("--tier", choices=[t.value for t in Tier], help="skip the classifier")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--chain-only", action="store_true", help="print routing and exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging("WARNING", json_logs=False)
    engine = create_engine(settings.database_url)
    router = build_router(settings, DbCallRecorder(create_sessionmaker(engine)))
    try:
        tier = Tier(args.tier) if args.tier else await router.classify(args.prompt)
        print(f"provider={settings.llm.provider} tier={tier.value}")
        print("chain:", " -> ".join(str(r) for r in router.chain(tier)) or "(no providers)")
        if args.chain_only:
            return 0

        request = ChatRequest(
            messages=[
                Message(role="system", content=SYSTEM),
                Message(role="user", content=args.prompt),
            ]
        )
        if args.stream:
            async for event in router.stream(request, tier=tier):
                if isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
                elif isinstance(event, StreamDone):
                    response = event.response
            print()
        else:
            response = await router.chat(request, tier=tier)
            print(response.content)
        print(
            f"\n[{response.provider}/{response.model}] fallback={response.is_fallback} "
            f"tokens in/out={response.usage.input_tokens}/{response.usage.output_tokens}"
        )
        return 0
    except AllModelsFailedError as exc:
        print(f"ERROR: {exc.user_message}\n{exc}", file=sys.stderr)
        return 1
    finally:
        await router.aclose()
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
