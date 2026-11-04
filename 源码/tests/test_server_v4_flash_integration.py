"""Integration test for server_v4_flash model profile."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.model_profiles import (
    resolve_model_profile,
    SERVER_V4_FLASH_PROFILE_NAME,
)
from app.services.deepseek_client import DeepSeekClient


async def main():
    profile = resolve_model_profile(SERVER_V4_FLASH_PROFILE_NAME)
    print(f"Profile: name={profile.name}, base_url={profile.base_url}, model={profile.model}")

    client = DeepSeekClient(model_profile=profile)
    print(f"Client: base_url={client.base_url}, model={client.model}")

    try:
        result = await client.generate_json(
            system_prompt="你是一个测试助手。请始终以JSON格式回复：{\"status\": \"ok\", \"message\": \"...\"}",
            user_prompt="请回复：连接测试成功",
            max_tokens=200,
            stream=False,
        )
        print(f"Result: {result}")
        print("\nSUCCESS: deepseek-v4-flash integration works!")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
        raise
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
