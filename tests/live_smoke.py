"""Explicit smoke test for an already-running server; not collected by pytest.

Run: python -m tests.live_smoke --config config.example.yaml
"""

import argparse
import json
import time

import httpx
from anthropic import Anthropic, DefaultHttpxClient as AnthropicHTTPClient
from openai import OpenAI, DefaultHttpxClient as OpenAIHTTPClient
from websockets.sync.client import connect

from fake_transfer_station.config import load_config
from fake_transfer_station.content import estimate_tokens


def main():
    parser = argparse.ArgumentParser(description="Test an already-running local station")
    parser.add_argument("--base-url", default="http://127.0.0.1:8317")
    parser.add_argument("--config", default="config.example.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    base = args.base_url.rstrip("/")
    model = config.prank.models[0]
    key = config.access.api_keys[0] if config.access.api_keys else "unused"
    pool = config.prank.content_pools[model.content_pool or config.prank.default_content_pool]
    allowed = {item.text for item in pool.items}
    headers = {"Authorization": f"Bearer {key}"}
    stale_usage = False

    def check_text(text):
        assert text in allowed, "Response did not exactly match any configured content item"

    with httpx.Client(base_url=base, headers=headers, trust_env=False, timeout=120) as http:
        assert http.get("/healthz").json() == {"status": "ok"}
        assert http.get("/").json()["message"] == "CLI Proxy API Server"
        assert http.head("/healthz").status_code == 200
        if config.access.api_keys:
            missing = httpx.get(base + "/v1/models", trust_env=False, timeout=5)
            assert missing.status_code == 401
            assert missing.json() == {"error": "Missing API key"}
            assert http.get("/v1/models", headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert http.get("/v1/models").json()["data"][0]["id"] == model.id
        assert "has_more" in http.get("/v1/models", headers={"Anthropic-Version": "2023-06-01"}).json()
        assert http.get("/v1/models?client_version=cpa").json()["models"][0]["slug"] == model.id
        assert http.get("/v1beta/models").status_code == 200
        assert http.post("/v1/responses", content="{").status_code == 400
        assert http.post("/v1/responses", json={"model": "nonexistent-smoke-model", "input": []}).status_code == 502
        print("PASS health, authentication, catalogs and error responses", flush=True)

        with OpenAI(base_url=base + "/v1", api_key=key, max_retries=0,
                    http_client=OpenAIHTTPClient(trust_env=False), timeout=120) as client:
            check_text(client.chat.completions.create(model=model.id, messages=[]).choices[0].message.content)
            check_text(client.completions.create(model=model.id, prompt="hello").choices[0].text)
            arrivals = []
            parts = []
            with client.chat.completions.create(model=model.id, messages=[], stream=True,
                                                stream_options={"include_usage": True}) as stream:
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        parts.append(chunk.choices[0].delta.content)
                        arrivals.append(time.monotonic())
            check_text("".join(parts))
            rate = model.streaming.tokens_per_second
            jitter = model.streaming.tokens_per_second_offset
            if rate > 0 and len(arrivals) > 1:
                first_cost = len(parts[0].encode("utf-8")) / 4
                expected = (estimate_tokens("".join(parts)) - first_cost) / (rate + jitter)
                assert arrivals[-1] - arrivals[0] >= expected * .65
            print(f"PASS OpenAI Chat/Completions; {len(arrivals)} text chunks over {arrivals[-1] - arrivals[0]:.2f}s", flush=True)
            result = client.responses.create(model=model.id, input="hello")
            check_text(result.output_text)
            stale_usage = "cache_write_tokens" not in result.usage.input_tokens_details.model_dump(exclude_unset=True)
            with client.responses.stream(model=model.id, input="hello") as stream:
                check_text("".join(event.delta for event in stream if event.type == "response.output_text.delta"))
                check_text(stream.get_final_response().output_text)
            compact = client.responses.compact(model=model.id, input="hello")
            assert compact.object == "response.compaction"
            assert compact.output[-1].type == "compaction"
            print("PASS Responses SDK streaming/final aggregation and compact", flush=True)

        with Anthropic(base_url=base, api_key=key, max_retries=0,
                       http_client=AnthropicHTTPClient(trust_env=False), timeout=120) as client:
            payload = [{"role": "user", "content": "hello"}]
            message = client.messages.create(model=model.id, messages=payload, max_tokens=4096)
            check_text(message.content[0].text)
            assert client.messages.count_tokens(model=model.id, messages=payload).input_tokens == message.usage.input_tokens
            with client.messages.stream(model=model.id, messages=payload, max_tokens=4096) as stream:
                check_text("".join(stream.text_stream))
                check_text(stream.get_final_text())
            print("PASS Anthropic SDK JSON, stream, final message and token count", flush=True)

        prefix = "/v1beta/models/" + model.id
        check_text(http.post(prefix + ":generateContent", json={"contents": []}).json()["candidates"][0]["content"]["parts"][0]["text"])
        parts = []
        with http.stream("POST", prefix + ":streamGenerateContent?alt=sse", json={"contents": []}) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    parts.append(event["candidates"][0]["content"]["parts"][0]["text"])
        check_text("".join(parts))
        assert http.post(prefix + ":countTokens", json={"contents": []}).status_code == 200
        print("PASS Gemini JSON, stream and countTokens", flush=True)

        check_text(http.post("/backend-api/codex/responses", json={"model": model.id, "input": []}).json()["output"][0]["content"][0]["text"])

    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/backend-api/codex/responses"
    with connect(ws_url, additional_headers=headers, proxy=None, open_timeout=5) as ws:
        def turn(payload):
            ws.send(json.dumps(payload))
            events = []
            while True:
                event = json.loads(ws.recv(timeout=120))
                assert event["type"] != "error", event
                events.append(event)
                if event["type"] == "response.completed":
                    assert [event["sequence_number"] for event in events] == list(range(len(events)))
                    return events

        warm = turn({"type": "response.create", "model": model.id, "input": [], "generate": False})
        assert warm[-1]["response"]["output"] == []
        events = turn({"type": "response.create", "input": [], "previous_response_id": warm[-1]["response"]["id"]})
        check_text("".join(event["delta"] for event in events if event["type"] == "response.output_text.delta"))
        check_text(events[-1]["response"]["output"][0]["content"][0]["text"])
        print("PASS Codex alias, WebSocket prewarm, continuation and ordered events", flush=True)

    if stale_usage:
        print("RESTART REQUIRED: running process lacks the cache_write_tokens fix; SDK strict schema requires it.")
    else:
        print("PASS running process includes cache_write_tokens fix")


if __name__ == "__main__":
    main()
