"""Synchronous LiteLLM isolation owned by the actual worker thread."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from threading import RLock
import httpx

from .client import drain_on_cancel
from .contract import LlmError

SDK_LOCK = RLock()


def disable_logging(litellm):
    for name, value in {
        "set_verbose": False, "suppress_debug_info": True, "telemetry": False,
        "turn_off_message_logging": True, "log_raw_request_response": False,
        "callbacks": [], "input_callback": [], "success_callback": [],
        "failure_callback": [], "service_callback": [], "audit_log_callbacks": [],
        "_async_input_callback": [], "_async_success_callback": [], "_async_failure_callback": [],
    }.items():
        setattr(litellm, name, value)
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "httpx", "httpcore", "openai"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False
        logger.disabled = True


class LiteLlmProvider:
    def __init__(self, completion=None):
        self._completion = completion
        self._clients = {}
        self._accepting = True

    async def complete(self, request, connection, credential, *, before_dispatch):
        task = asyncio.create_task(asyncio.to_thread(
            self._complete, request, connection, credential, before_dispatch))
        return await drain_on_cancel(task)

    def _complete(self, request, connection, credential, before_dispatch):
        with SDK_LOCK:
            if not self._accepting:
                raise LlmError("CANCELLED")
            value = request.unpack()
            params = {**value["generation_options"], "model": connection.model,
                      "api_base": connection.api_base, "api_key": credential,
                      "timeout": connection.timeout, "messages": value["messages"],
                      "stream": False, "num_retries": 0, "max_retries": 0,
                      "caching": False, "no-log": True}
            if value["response_format"] is not None:
                params["response_format"] = value["response_format"]
            if self._completion is not None:
                params["proxy_url"] = connection.proxy_url
                before_dispatch()
                return self._completion(**params)
            os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
            os.environ["LITELLM_LOG"] = "ERROR"
            import litellm
            from openai import OpenAI
            disable_logging(litellm)
            if not connection.model.startswith("openai/"):
                raise LlmError("CONFIG_INVALID")
            identity = hashlib.sha256((connection.effective_token + "\0" + credential).encode()).hexdigest()
            cached = self._clients.get(connection.connection_id)
            if cached is not None and cached[0] != identity:
                # No other worker can be using it while SDK_LOCK is held. Passing
                # an explicit client avoids LiteLLM's global authentication cache.
                cached[1].close()
                self._clients.pop(connection.connection_id)
                cached = None
            if cached is None:
                if len(self._clients) >= 32:
                    _, retired = self._clients.popitem()
                    retired[1].close()
                expected = connection.api_base.rstrip("/") + "/chat/completions"

                def guard(outbound):
                    if str(outbound.url) != expected or outbound.method != "POST":
                        raise LlmError("CONFIG_INVALID")

                transport = httpx.Client(proxy=connection.proxy_url, trust_env=False,
                                         follow_redirects=False, event_hooks={"request": [guard]})
                client = OpenAI(api_key=credential, base_url=connection.api_base,
                                timeout=connection.timeout, max_retries=0, http_client=transport)
                cached = (identity, client)
                self._clients[connection.connection_id] = cached
            params["client"] = cached[1]
            previous = litellm.client_session
            litellm.client_session = cached[1]._client
            try:
                before_dispatch()
                return litellm.completion(**params)
            finally:
                litellm.client_session = previous

    async def close(self):
        # Stop acceptance before waiting on an in-flight worker's lock.
        self._accepting = False
        await drain_on_cancel(asyncio.create_task(asyncio.to_thread(self._close)))

    def _close(self):
        with SDK_LOCK:
            for _, client in self._clients.values():
                client.close()
            self._clients.clear()
