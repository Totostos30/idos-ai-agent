"""Model router: many keys per provider, live rate-limit tracking, proactive key switching and fallbacks.

Every key is an *account*; every (account, model) pair has a *bucket* holding the limits reported by the
provider (x-ratelimit-* headers). A request goes to the healthiest bucket of the first usable model in the
chain. When a key gets close to its limit (models.switch_before) the router moves to the next key before an
error happens; 429/5xx/network errors put the bucket on cooldown and 401 disables the key.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import pathlib
import re
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable

from . import bus, config, llm
from .keystore import KeyStore
from .providers import (PAID_ORDER, PROVIDERS, alias_spec, estimate_tokens, human_seconds, params_for, parse_reset,
                        parse_retry_after, split_spec, wants_paid)
from .util import clip

log = logging.getLogger("idos.router")
STATE_FILE = config.RUNTIME / "router_state.json"
USER_AGENT = f"IDOS/{config.VERSION}"
YEAR = 365 * 86400.0
_SPEC_RE = re.compile(r"\b([a-zA-Z]+):([\w./:\-]*)")


# Free tiers with a small daily token limit: context_mode=auto keeps the economy budget (models.max_input_tokens) there;
# other providers get models.max_input_tokens_full (0 = only the model's own limits).
ECONOMY_PROVIDERS = {"groq"}


class RouterError(Exception):
    pass


class Exhausted(RouterError):
    retry_at: float = 0.0  # epoch seconds when the nearest key should be free


class ContextTooLong(RouterError):
    pass


class ToolCallFailed(RouterError):
    pass


from .providers import describe_rate_limit, parse_rate_limit  # noqa: E402


@dataclass
class Bucket:
    remaining_requests: int | None = None
    limit_requests: int | None = None
    reset_requests_at: float = 0.0
    remaining_tokens: int | None = None
    limit_tokens: int | None = None
    reset_tokens_at: float = 0.0
    cooldown_until: float = 0.0
    last_error: str = ""
    used_today: int = 0
    tokens_today: int = 0
    day: str = ""
    too_large: int = 0  # smallest request (tokens) rejected as too large
    limit_kind: str = ""  # last 429 kind: TPD / RPD / TPM / RPM
    day_limit_tokens: int = 0  # from the last TPD 429 body
    day_used_tokens: int = 0
    day_seen_at: float = 0.0

    def roll(self, today: str) -> None:
        if self.day != today:
            self.day, self.used_today, self.tokens_today = today, 0, 0

    def wait(self, need: int, now: float) -> float:
        """0 = usable now, inf = can never take a request this big, otherwise seconds to wait."""
        if (self.too_large and need >= self.too_large) or (self.limit_tokens and need > self.limit_tokens):
            return math.inf
        waits = []
        if self.cooldown_until > now:
            waits.append(self.cooldown_until - now)
        if self.remaining_requests is not None and self.remaining_requests <= 0 and self.reset_requests_at > now:
            waits.append(self.reset_requests_at - now)
        if self.remaining_tokens is not None and need > self.remaining_tokens and self.reset_tokens_at > now:
            waits.append(self.reset_tokens_at - now)
        return max(waits) if waits else 0.0


_BUCKET_FIELDS = {f.name for f in fields(Bucket)}


@dataclass
class Account:
    id: str
    provider: str
    secret: str
    label: str
    masked: str = ""
    source: str = "file"
    enabled: bool = True
    disabled_until: float = 0.0
    disabled_reason: str = ""
    buckets: dict[str, Bucket] = field(default_factory=dict)

    def bucket(self, model: str) -> Bucket:
        return self.buckets.setdefault(model, Bucket())

    def disabled(self, now: float) -> bool:
        return self.disabled_until > now


def _check(cancel: Any) -> None:
    if cancel is not None and cancel.is_set():
        raise llm.Cancelled()


async def _sleep(seconds: float, cancel: Any) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _check(cancel)
        await asyncio.sleep(min(0.2, max(0.0, end - time.monotonic())))
    _check(cancel)


async def _cancellable(coro: Any, cancel: Any) -> Any:
    task = asyncio.ensure_future(coro)
    if cancel is None:
        return await task
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.2)
        if done:
            return task.result()
        if cancel.is_set():
            task.cancel()
            raise llm.Cancelled()


class Router:
    def __init__(self, settings: Any, keystore: KeyStore | None = None, emit: Callable | None = None,
                 state_file: pathlib.Path | str | None = None) -> None:
        self.settings = settings
        self.keystore = keystore or KeyStore()
        self.emit = emit or bus.emit
        self.state_file = pathlib.Path(state_file) if state_file else STATE_FILE
        self._accounts: dict[str, Account] | None = None
        self._current: dict[str, str] = {}
        self._last: dict[str, str] | None = None
        self._saved_at = 0.0
        # spec -> params the provider rejected with 400 ("property 'reasoning_format' is unsupported").
        # Remembered for the whole session so every next request goes through on the first try.
        self._bad_params: dict[str, set[str]] = {}
        self._state = self.load_state()

    # ------------------------------------------------------------------ accounts
    def providers(self) -> dict[str, dict]:
        return {key: {"name": info["name"], "keys_url": info.get("keys_url", ""), "free": bool(info.get("free")),
                      "paid": bool(info.get("paid")), "no_key": bool(info.get("no_key")),
                      "models": list(info.get("models", [])), "vision": list(info.get("vision", [])),
                      "note": info.get("note", "")} for key, info in PROVIDERS.items()}

    def accounts(self) -> list[Account]:
        if self._accounts is None:
            built: dict[str, Account] = {}
            counters: dict[str, int] = {}
            for entry in self.keystore.entries():
                provider = entry["provider"]
                if provider not in PROVIDERS:
                    continue
                counters[provider] = counters.get(provider, 0) + 1
                account_id = f"{provider}:{entry['id']}"
                label = entry.get("label") or f"{PROVIDERS[provider]['name']} · ключ {counters[provider]}"
                built[account_id] = Account(account_id, provider, entry["secret"], label, KeyStore.mask(entry["secret"]),
                                            entry.get("source", "file"), bool(entry.get("enabled", True)))
            for provider, info in PROVIDERS.items():
                if info.get("no_key"):
                    built[f"{provider}:local"] = Account(f"{provider}:local", provider, "", info["name"], "", "local")
            saved = self._state.get("accounts", {})
            for account_id, account in built.items():
                data = saved.get(account_id) or {}
                account.disabled_until = float(data.get("disabled_until") or 0)
                account.disabled_reason = str(data.get("disabled_reason") or "")
                for model, values in (data.get("buckets") or {}).items():
                    account.buckets[model] = Bucket(**{k: v for k, v in values.items() if k in _BUCKET_FIELDS})
            self._accounts = built
        return list(self._accounts.values())

    def account(self, account_id: str) -> Account:
        for account in self.accounts():
            if account.id == account_id:
                return account
        raise RouterError(f"нет ключа {account_id}")

    def _provider_accounts(self, provider: str) -> list[Account]:
        return [a for a in self.accounts() if a.provider == provider and a.enabled]

    def invalidate(self) -> None:
        """Reload keys (after the UI added or removed one) keeping the collected limits."""
        if self._accounts is not None:
            self._state = self._snapshot()
        self._accounts = None

    def has_provider(self, provider: str) -> bool:
        info = PROVIDERS.get(provider)
        return bool(info) and any(a.secret or info.get("no_key") for a in self._provider_accounts(provider))

    def supports_vision(self, spec: str) -> bool:
        provider, model = split_spec(spec)
        low = model.lower()
        return model in PROVIDERS.get(provider, {}).get("vision", []) or any(
            tag in low for tag in ("vision", "-vl", "vl-", "llava", "gemma3"))

    def vision_only(self, spec: str) -> bool:
        """Models reserved for reading images: they are never used for chat or tools."""
        patterns = self.settings.get("models.vision_only")
        if patterns is None:
            patterns = ["qwen", "nano-omni"]
        if isinstance(patterns, str):
            patterns = [patterns]
        low = (spec or "").lower()
        return any(str(p).strip().lower() in low for p in patterns if str(p).strip())

    def default_model(self, provider: str) -> str:
        paid = self.settings.get("models.paid_default") or {}
        if isinstance(paid, dict) and paid.get(provider):
            return str(paid[provider])
        models = PROVIDERS.get(provider, {}).get("models") or []
        return models[0] if models else ""

    def normalize(self, spec: str) -> str:
        provider, model = split_spec(spec)
        return f"{provider}:{model or self.default_model(provider)}"

    def forced(self) -> str:
        """Model pinned in the chat window ("" = Auto). Only text models with a key count."""
        spec = str(self.settings.get("models.force") or "").strip()
        if not spec or spec.lower() == "auto":
            return ""
        spec = self.normalize(spec)
        provider, model = split_spec(spec)
        if provider in PROVIDERS and model and self.has_provider(provider) and not self.vision_only(spec):
            return spec
        return ""

    def set_force(self, spec: str | None) -> str:
        spec = str(spec or "").strip()
        if not spec or spec.lower() == "auto":
            self.settings.set("models.force", "")
            self.emit("router.status", self.status())
            return ""
        spec = self.normalize(spec)
        provider, model = split_spec(spec)
        if provider not in PROVIDERS or not model:
            raise RouterError(f"Неизвестная модель: {spec}")
        if self.vision_only(spec):
            raise RouterError(f"{model} читает только изображения — для чата её закрепить нельзя")
        if not self.has_provider(provider):
            raise RouterError(f"Нет ключа {PROVIDERS[provider]['name']}: добавьте его в Настройки → Модели")
        self.settings.set("models.force", spec)
        self.emit("router.status", self.status())
        return spec

    def choices(self) -> list[dict]:
        """Models for the chat picker: only what the user configured (active, fast, reserve chain)."""
        get = self.settings.get
        out: list[dict] = []
        for role, specs in (("основная", [get("models.active")]), ("быстрая", [get("models.fast")]),
                            ("резерв", list(get("models.chain") or [])), ("закреплена", [get("models.force")])):
            for spec in specs:
                if not spec or str(spec).lower() == "auto":
                    continue
                spec = self.normalize(spec)
                provider, model = split_spec(spec)
                if provider not in PROVIDERS or not model or self.vision_only(spec) or any(c["spec"] == spec for c in out):
                    continue
                out.append({"spec": spec, "provider": PROVIDERS[provider].get("name", provider), "model": model,
                            "role": role, "ready": bool(self.has_provider(provider))})
        return out

    def chain_for(self, purpose: str = "agent", *, auto: bool = False) -> list[str]:
        get = self.settings.get
        if purpose != "vision" and not auto:
            pinned = self.forced()
            if pinned:
                return [pinned]  # принудительный выбор: только эта модель (ключи всё равно чередуются)
        if purpose == "vision":
            # Only what the user chose: no automatic fallback to other providers' vision models.
            specs = [get("models.vision")] + list(get("models.vision_chain") or [])
        elif purpose == "fast":
            specs = [get("models.fast"), get("models.active")] + list(get("models.chain") or [])
        else:
            specs = [get("models.active")] + list(get("models.chain") or [])
        out: list[str] = []
        for spec in specs:
            if not spec:
                continue
            spec = self.normalize(spec)
            provider, model = split_spec(spec)
            if purpose != "vision" and self.vision_only(spec):
                continue
            if provider in PROVIDERS and model and spec not in out and self.has_provider(provider):
                out.append(spec)
        return out

    def budget_tokens(self, purpose: str = "agent", max_output: int | None = None) -> int:
        """Input-token budget for the agent context so a request fits the first model's per-minute limit."""
        max_output = int(max_output or self.settings.get("models.max_output_tokens", 1200))
        chain = self.chain_for(purpose)
        if not chain:
            return 6000
        provider, model = split_spec(chain[0])
        buckets = [a.buckets[model] for a in self._provider_accounts(provider) if model in a.buckets]
        limits = [b.limit_tokens for b in buckets if b.limit_tokens]
        budget = max(limits) - max_output - 400 if limits else int(PROVIDERS[provider].get("input_budget", 30000))
        oversize = [b.too_large for b in buckets if b.too_large]
        if oversize:
            budget = min(budget, min(oversize) - max_output - 200)
        mode = str(self.settings.get("models.context_mode", "auto") or "auto").lower()
        economy = mode == "economy" or (mode == "auto" and provider in ECONOMY_PROVIDERS)
        key = "models.max_input_tokens" if economy else "models.max_input_tokens_full"
        cap = int(self.settings.get(key, 0) or 0)
        if cap:
            budget = min(budget, cap)
        return max(2500, budget)

    # ------------------------------------------------------------------ chat
    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None, stream: bool = True,
                   on_text: Callable[[str], Any] | None = None, on_reset: Callable[[], Any] | None = None,
                   cancel: Any = None, chain: list[str] | None = None, purpose: str = "agent",
                   max_tokens: int | None = None, temperature: float | None = None, extra: dict | None = None,
                   tool_choice: Any = None) -> llm.Completion:
        specs = [s for s in dict.fromkeys(self.normalize(s) for s in (chain or self.chain_for(purpose)) if s)
                 if split_spec(s)[1]]
        if purpose != "vision":
            specs = [s for s in specs if not self.vision_only(s)]
        if not specs:
            raise Exhausted("Не выбрана модель. Откройте Настройки → Модели.")
        max_tokens = int(max_tokens or self.settings.get("models.max_output_tokens", 1200))
        if temperature is None:
            temperature = self.settings.get("models.temperature", 0.3)
        need = estimate_tokens(messages, tools) + max_tokens
        max_wait = float(self.settings.get("models.max_wait_seconds", 70))
        waited = 0.0
        stripped: set[str] = set()
        broken: set[str] = set()
        failures: list[str] = []
        for _attempt in range(40):
            _check(cancel)
            live = [s for s in specs if s not in broken]
            if not live:
                break
            pick, wait, reasons, oversize = self._pick(live, need)
            if pick is None:
                if oversize and wait is None:
                    raise ContextTooLong("запрос больше лимита токенов у всех доступных моделей")
                if wait is None or waited + wait > max_wait:
                    error = Exhausted(self._exhausted_text(failures + reasons, wait))
                    error.retry_at = time.time() + wait if wait else 0.0
                    raise error
                self.emit("router.wait", {"seconds": round(wait, 1), "reason": reasons[0] if reasons else ""})
                await _sleep(wait + 0.2, cancel)
                waited += wait + 0.2
                continue
            spec, account = pick
            streamed = {"any": False}

            def relay(text: str, flag: dict = streamed) -> None:
                flag["any"] = True
                if on_text is not None:
                    on_text(text)

            try:
                return await self._request(spec, account, messages, tools, stream, relay, cancel, max_tokens,
                                           temperature, extra, tool_choice, strip=spec in stripped)
            except llm.HTTPError as exc:
                action = self._on_http_error(spec, account, exc, need)
                failures.append(f"{account.label} · {split_spec(spec)[1]}: "
                                f"{account.bucket(split_spec(spec)[1]).last_error or exc.status}")
                if streamed["any"] and on_reset is not None:
                    on_reset()
                if action == "toolcall":
                    raise ToolCallFailed(exc.message) from None
                if action == "context":
                    raise ContextTooLong(exc.message) from None
                if action == "strip":
                    sent = {**params_for(self.settings.get("models.params"), spec), **(extra or {})}
                    bad = self._bad_params.setdefault(spec, set())
                    low = str(exc.message or "").lower()
                    named = {k for k in sent if k.lower() in low and k not in bad}
                    if named:
                        bad.update(named)
                        log.info("%s rejected %s, retrying without it", spec, ", ".join(sorted(named)))
                        continue
                    if spec not in stripped and (params_for(self.settings.get("models.params"), spec) or extra):
                        stripped.add(spec)
                        continue
                    broken.add(spec)
                    if not [s for s in specs if s not in broken]:
                        raise RouterError(f"Модель отклонила запрос: {exc.message}") from None
            except llm.NetworkError as exc:
                bucket = account.bucket(split_spec(spec)[1])
                bucket.cooldown_until = time.time() + 8
                bucket.last_error = f"нет связи: {clip(str(exc), 100)}"
                failures.append(f"{account.label}: нет связи ({clip(str(exc), 80)})")
                if streamed["any"] and on_reset is not None:
                    on_reset()
                self._dirty()
        raise Exhausted(self._exhausted_text(failures, None))

    def _pick(self, specs: list[str], need: int) -> tuple[tuple[str, Account] | None, float | None, list[str], bool]:
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        margin = self.settings.get("models.switch_before") or {}
        req_margin = int(margin.get("requests", 20) or 0)
        tok_margin = int(margin.get("tokens", 1500) or 0)
        min_wait: float | None = None
        reasons: list[str] = []
        oversize = False
        for spec in specs:
            provider, model = split_spec(spec)
            candidates = []
            for account in self._provider_accounts(provider):
                if account.disabled(now):
                    reasons.append(f"{account.label}: {account.disabled_reason}")
                    continue
                bucket = account.bucket(model)
                bucket.roll(today)
                wait = bucket.wait(need, now)
                if wait == math.inf:
                    oversize = True
                    reasons.append(f"{account.label} · {model}: запрос больше лимита модели")
                    continue
                if wait > 0:
                    min_wait = wait if min_wait is None else min(min_wait, wait)
                    reasons.append(f"{account.label} · {model}: {bucket.last_error or 'лимит'} (≈{human_seconds(wait)})")
                    continue
                near = ((bucket.remaining_requests is not None and bucket.remaining_requests <= req_margin
                         and bucket.reset_requests_at > now)
                        or (bucket.remaining_tokens is not None and bucket.remaining_tokens - need <= tok_margin
                            and bucket.reset_tokens_at > now))
                current = self._current.get(spec) == account.id
                candidates.append((near, not current, bucket.used_today, account))
            if candidates:
                candidates.sort(key=lambda item: item[:3])
                return (spec, candidates[0][3]), None, reasons, oversize
        return None, min_wait, reasons, oversize

    async def _request(self, spec: str, account: Account, messages: list[dict], tools: list[dict] | None,
                       stream: bool, on_text: Callable[[str], Any], cancel: Any, max_tokens: int,
                       temperature: float | None, extra: dict | None, tool_choice: Any,
                       strip: bool = False) -> llm.Completion:
        provider, model = split_spec(spec)
        info = PROVIDERS[provider]
        url = info["base"].rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **info.get("headers", {})}
        if account.secret:
            headers["Authorization"] = f"Bearer {account.secret}"
        payload: dict[str, Any] = {"model": model, "messages": messages,
                                   info.get("max_tokens_param", "max_tokens"): max_tokens}
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if not strip:
            payload.update(params_for(self.settings.get("models.params"), spec))
            payload.update(extra or {})
            for key in self._bad_params.get(spec, ()):
                payload.pop(key, None)
        if stream:
            payload["stream"] = True
            if provider == "openai":
                payload["stream_options"] = {"include_usage": True}
        self.emit("router.request", {"spec": spec, "account": account.label})
        bucket = account.bucket(model)
        started = time.time()
        try:
            if stream:
                completion, response_headers = await llm.stream_chat(url, headers, payload, 120.0, on_text, cancel)
            else:
                completion, response_headers = await _cancellable(self._post(url, payload, headers), cancel)
        except llm.HTTPError as exc:
            self._apply_headers(bucket, exc.headers)
            raise
        self._apply_headers(bucket, response_headers)
        bucket.used_today += 1
        bucket.tokens_today += int((completion.usage or {}).get("total_tokens") or 0)
        bucket.last_error = ""
        bucket.cooldown_until = 0.0
        completion.spec, completion.provider, completion.account = spec, provider, account.label
        completion.model = completion.model or model
        completion.headers = response_headers
        self._switched(spec, account)
        self._current[spec] = account.id
        log.info("%s via %s: %d ms, tokens %s", spec, account.label, (time.time() - started) * 1000,
                 (completion.usage or {}).get("total_tokens", "?"))
        self._dirty()
        self.emit("router.status", self.status())
        return completion

    @staticmethod
    async def _post(url: str, payload: dict, headers: dict) -> tuple[llm.Completion, dict]:
        data, response_headers = await llm.request_json_async(url, payload, headers, timeout=120)
        return llm.parse_completion(data), response_headers

    def _switched(self, spec: str, account: Account) -> None:
        last = self._last
        self._last = {"spec": spec, "account": account.label, "account_id": account.id}
        if last and (last["spec"], last["account_id"]) != (spec, account.id):
            reason = "смена модели" if last["spec"] != spec else "смена ключа"
            self.emit("router.switch", {"from": f"{last['account']} · {last['spec']}",
                                        "to": f"{account.label} · {spec}", "reason": reason})

    @staticmethod
    def _apply_headers(bucket: Bucket, headers: dict | None, now: float | None = None) -> None:
        if not headers:
            return
        now = time.time() if now is None else now

        def number(*names: str) -> int | None:
            for name in names:
                value = headers.get(name)
                if value not in (None, ""):
                    try:
                        return int(float(value))
                    except ValueError:
                        continue
            return None

        limit_r = number("x-ratelimit-limit-requests", "x-ratelimit-limit-requests-day", "x-ratelimit-limit")
        remain_r = number("x-ratelimit-remaining-requests", "x-ratelimit-remaining-requests-day",
                          "x-ratelimit-remaining")
        limit_t = number("x-ratelimit-limit-tokens", "x-ratelimit-limit-tokens-minute")
        remain_t = number("x-ratelimit-remaining-tokens", "x-ratelimit-remaining-tokens-minute")
        reset_r = parse_reset(headers.get("x-ratelimit-reset-requests") or headers.get("x-ratelimit-reset-requests-day")
                              or headers.get("x-ratelimit-reset"), now)
        reset_t = parse_reset(headers.get("x-ratelimit-reset-tokens") or headers.get("x-ratelimit-reset-tokens-minute"),
                              now)
        updates = {
            "limit_requests": limit_r,
            "remaining_requests": remain_r,
            "limit_tokens": limit_t,
            "remaining_tokens": remain_t,
            "reset_requests_at": now + reset_r if reset_r is not None else None,
            "reset_tokens_at": now + reset_t if reset_t is not None else None,
        }
        for attr, value in updates.items():
            if value is not None:
                setattr(bucket, attr, value)

    def _disable(self, account: Account, seconds: float, reason: str) -> None:
        account.disabled_until = time.time() + seconds
        account.disabled_reason = reason
        log.warning("account %s disabled: %s", account.id, reason)
        self.emit("router.account_disabled", {"id": account.id, "label": account.label, "reason": reason})
        self._dirty(force=True)

    def _on_http_error(self, spec: str, account: Account, exc: llm.HTTPError, need: int) -> str:
        now = time.time()
        model = split_spec(spec)[1]
        bucket = account.bucket(model)
        status, message = exc.status, exc.message or ""
        low = message.lower()
        bucket.last_error = f"{status}: {clip(message, 120)}"
        log.warning("%s via %s failed: %s %s", spec, account.label, status, clip(message, 300))
        try:
            if status == 429:
                info = parse_rate_limit(message)
                wait = info["wait"] or parse_retry_after(exc.headers) or 0.0
                daily = info["kind"] in ("TPD", "RPD") or any(
                    word in low for word in ("per day", "(rpd)", "(tpd)", "daily"))
                if daily and not info["wait"]:
                    # No exact time from the provider: guess, but never invent a fixed 10-minute floor.
                    wait = max(wait, max(bucket.reset_requests_at, bucket.reset_tokens_at) - now, 60.0)
                bucket.cooldown_until = now + (wait or 20.0)
                bucket.limit_kind = info["kind"]
                if info["kind"] == "TPD" and info["limit"]:
                    bucket.day_limit_tokens, bucket.day_used_tokens = info["limit"], info["used"] or 0
                    bucket.day_seen_at = now
                bucket.last_error = describe_rate_limit(info)
                return "retry"
            if status == 413 or "request too large" in low or "reduce your message size" in low:
                bucket.too_large = need if not bucket.too_large else min(bucket.too_large, need)
                bucket.last_error = "запрос слишком большой (413)"
                return "retry"
            if status == 401:
                self._disable(account, YEAR, "ключ отклонён (401)")
                return "retry"
            if status == 402:
                self._disable(account, 3600, "нужна оплата или закончился баланс (402)")
                return "retry"
            if status == 403:
                reason = "доступ запрещён (403)"
                if "1010" in message:
                    reason += " — сервис блокирует ваш IP или регион, включите VPN"
                self._disable(account, 900, reason)
                return "retry"
            if status == 404:
                bucket.cooldown_until = now + 3600
                bucket.last_error = "модель не найдена (404)"
                return "retry"
            if status in (400, 422):
                if any(w in low for w in ("tool_use_failed", "failed to call a function", "tool call validation",
                                          "parse tool call", "failed_generation")):
                    return "toolcall"
                if any(w in low for w in ("context length", "context_length", "maximum context", "context window",
                                          "too many tokens", "reduce the length", "prompt is too long")):
                    return "context"
                return "strip"
            bucket.cooldown_until = now + (10 if status >= 500 else 30)
            return "retry"
        finally:
            self._dirty()
            self.emit("router.status", self.status())

    @staticmethod
    def _exhausted_text(reasons: list[str], wait: float | None) -> str:
        by_key: dict[str, str] = {}
        for reason in reasons:
            if reason:
                key = reason.split(": ", 1)[0]
                by_key.pop(key, None)
                by_key[key] = reason  # the latest line per key wins: one line per key
        lines = list(by_key.values())[-6:]
        text = "Все модели и ключи сейчас недоступны."
        if lines:
            text += "\n" + "\n".join("• " + line for line in lines)
        if any("дневной лимит токенов" in line for line in lines):
            text += ("\nЭто суточный лимит токенов Groq (скользящие 24 часа): токены возвращаются постепенно, "
                     "поэтому при каждой новой попытке время ожидания пересчитывается.")
        if wait:
            text += f"\nБлижайший ключ освободится через ≈{human_seconds(wait)} (на момент ответа)."
        return text + "\nДобавьте ключи в Настройки → Модели или подождите."

    # ------------------------------------------------------------------ speech, models, switching
    async def transcribe(self, wav: bytes, language: str = "ru", prompt: str = "") -> str:
        spec = self.normalize(self.settings.get("models.stt") or "groq:whisper-large-v3-turbo")
        provider, model = split_spec(spec)
        info = PROVIDERS.get(provider)
        if not info:
            raise RouterError(f"неизвестный провайдер распознавания «{provider}»")
        form = {"model": model, "response_format": "json", "temperature": "0"}
        if language:
            form["language"] = language
        if prompt:
            form["prompt"] = prompt[:800]
        body, content_type = llm.multipart(form, {"file": ("speech.wav", wav, "audio/wav")})
        now = time.time()
        accounts = sorted((a for a in self._provider_accounts(provider) if not a.disabled(now)),
                          key=lambda a: a.bucket(model).cooldown_until)
        errors: list[str] = []
        for account in accounts:
            bucket = account.bucket(model)
            if bucket.cooldown_until > time.time():
                errors.append(f"{account.label}: лимит")
                continue
            headers = {"Content-Type": content_type, "User-Agent": USER_AGENT}
            if account.secret:
                headers["Authorization"] = f"Bearer {account.secret}"
            try:
                raw, response_headers = await asyncio.to_thread(
                    llm.request_raw, info["base"].rstrip("/") + "/audio/transcriptions", body, headers, "POST", 60)
            except llm.HTTPError as exc:
                self._on_http_error(spec, account, exc, 0)
                errors.append(f"{account.label}: {exc.status}")
                continue
            except llm.NetworkError as exc:
                errors.append(f"{account.label}: нет связи ({clip(str(exc), 60)})")
                continue
            self._apply_headers(bucket, response_headers)
            bucket.used_today += 1
            try:
                return str(json.loads(raw.decode("utf-8")).get("text", "")).strip()
            except (ValueError, AttributeError):
                return raw.decode("utf-8", "replace").strip()
        raise Exhausted("Распознавание речи недоступно: " + ("; ".join(errors) or f"нет ключа {info['name']}"))

    def _auth_headers(self, account: Account) -> dict:
        headers = {"User-Agent": USER_AGENT, **PROVIDERS[account.provider].get("headers", {})}
        if account.secret:
            headers["Authorization"] = f"Bearer {account.secret}"
        return headers

    async def list_models(self, provider: str) -> list[str]:
        info = PROVIDERS.get(provider)
        if not info:
            raise RouterError(f"неизвестный провайдер «{provider}»")
        accounts = [a for a in self._provider_accounts(provider) if not a.disabled(time.time())]
        if not accounts:
            raise RouterError(f"Нет ключа {info['name']}")
        try:
            data, _ = await llm.request_json_async(info["base"].rstrip("/") + "/models", None,
                                                   self._auth_headers(accounts[0]), "GET", 20)
        except (llm.HTTPError, llm.NetworkError) as exc:
            raise RouterError(f"{info['name']}: не удалось получить список моделей ({exc})") from None
        items = data.get("data") if isinstance(data, dict) else data
        return sorted({str(item["id"]) for item in items or [] if isinstance(item, dict) and item.get("id")})

    async def _complete(self, provider: str, model: str) -> str:
        if model:
            return f"{provider}:{model}"
        model = self.default_model(provider)
        if not model and self.has_provider(provider):
            try:
                names = await self.list_models(provider)
            except RouterError:
                names = []
            model = names[0] if names else ""
        if not model:
            raise RouterError(f"У {PROVIDERS[provider]['name']} не найдено ни одной модели")
        return f"{provider}:{model}"

    async def resolve_model(self, query: str) -> str:
        """Turn «быстрая», «платная», «клод» or «groq:openai/gpt-oss-20b» into a provider:model spec."""
        text = (query or "").strip()
        if not text:
            raise RouterError("Не указана модель")
        low = text.lower()
        provider, model = split_spec(text)
        if ":" in text and provider in PROVIDERS and " " not in text:
            return await self._complete(provider, model)
        for match in _SPEC_RE.finditer(text):
            if match.group(1).lower() in PROVIDERS:
                return await self._complete(match.group(1).lower(), match.group(2).rstrip(".,!?»\""))
        for key, info in PROVIDERS.items():
            for name in info.get("models", []):
                if low in (name.lower(), name.lower().rsplit("/", 1)[-1]):
                    return f"{key}:{name}"
        alias = alias_spec(low)
        if alias and PROVIDERS[split_spec(alias)[0]].get("paid"):
            return await self._complete(*split_spec(alias))
        if wants_paid(low):
            paid = self.settings.get("models.paid_default") or {}
            preferred = paid.get("spec") if isinstance(paid, dict) else ""
            if preferred and self.has_provider(split_spec(preferred)[0]):
                return self.normalize(preferred)
            for key in PAID_ORDER:
                if self.has_provider(key):
                    return await self._complete(key, "")
            raise RouterError("Платная модель ещё не настроена: добавьте ключ OpenAI, Anthropic, Mistral, DeepSeek "
                              "или xAI в Настройки → Модели.")
        if alias:
            return await self._complete(*split_spec(alias))
        for key, info in PROVIDERS.items():
            for name in info.get("models", []):
                if len(low) >= 3 and low in name.lower():
                    return f"{key}:{name}"
        raise RouterError(f"Не понял, какую модель выбрать: «{text}». Пример: groq:openai/gpt-oss-120b")

    def set_active(self, spec: str) -> str:
        spec = self.normalize(spec)
        provider, model = split_spec(spec)
        if provider not in PROVIDERS or not model:
            raise RouterError(f"Неизвестная модель: {spec}")
        if not self.has_provider(provider):
            info = PROVIDERS[provider]
            raise RouterError(f"Нет ключа {info['name']}: добавьте его в Настройки → Модели ({info.get('keys_url', '')}).")
        # Vision-only models (Qwen) become the image reader; the main model stays as it is.
        self.settings.set("models.vision" if self.vision_only(spec) else "models.active", spec)
        if not self.vision_only(spec) and self.forced():
            self.settings.set("models.force", spec)
        self.emit("router.status", self.status())
        return spec

    def enable(self, account_id: str, enabled: bool = True) -> None:
        account = self.account(account_id)
        if account.source != "local":
            self.keystore.set_enabled(account_id.split(":", 1)[1], enabled)
        account.enabled = bool(enabled)
        if enabled:
            account.disabled_until, account.disabled_reason = 0.0, ""
        self._dirty(force=True)
        self.emit("router.status", self.status())

    async def test_account(self, account_id: str) -> dict:
        """Check a key without spending chat quota (GET /models)."""
        account = self.account(account_id)
        info = PROVIDERS[account.provider]
        started = time.time()

        def elapsed() -> int:
            return int((time.time() - started) * 1000)

        try:
            await llm.request_json_async(info["base"].rstrip("/") + "/models", None, self._auth_headers(account),
                                         "GET", 20)
        except llm.HTTPError as exc:
            if exc.status == 401:
                self._disable(account, YEAR, "ключ отклонён (401)")
            return {"ok": False, "message": f"{exc.status}: {clip(exc.message, 200)}", "ms": elapsed()}
        except llm.NetworkError as exc:
            return {"ok": False, "message": f"нет связи: {clip(str(exc), 200)}", "ms": elapsed()}
        if account.disabled_until:
            account.disabled_until, account.disabled_reason = 0.0, ""
            self._dirty(force=True)
        result = {"ok": True, "message": "ключ работает", "ms": elapsed()}
        if info.get("balance_path"):
            balance = await self.balance(account)
            if balance is not None:
                result["balance"] = balance
                result["message"] += f" · баланс {balance:,} токенов".replace(",", " ")
                if balance <= 0:
                    result["message"] += " — пополните хотя бы немного, иначе бесплатные модели не ответят"
        return result

    async def balance(self, account: Account) -> int | None:
        """Tokens left on the key (AnyModel GET /v1/balance); None when the provider does not report it."""
        info = PROVIDERS[account.provider]
        if not info.get("balance_path"):
            return None
        try:
            data, _ = await llm.request_json_async(info["base"].rstrip("/") + info["balance_path"], None,
                                                   self._auth_headers(account), "GET", 20)
        except (llm.HTTPError, llm.NetworkError):
            return None
        if isinstance(data, dict) and data.get("unlimited"):
            return 10 ** 12
        try:
            return int((data or {}).get("balance"))
        except (TypeError, ValueError):
            return None

    async def selftest_model(self, spec: str) -> dict:
        """Check a model before trusting it: plain answer, tool calling (chat models) and images (vision models)."""
        spec = self.normalize(spec)
        provider, model = split_spec(spec)
        if provider not in PROVIDERS or not model:
            return {"spec": spec, "ok": False, "checks": [], "message": f"неизвестная модель {spec}"}
        if not self.has_provider(provider):
            return {"spec": spec, "ok": False, "checks": [],
                    "message": f"нет ключа {PROVIDERS[provider]['name']} — добавьте его выше"}
        reader = self.vision_only(spec)
        purpose = "vision" if reader else "agent"
        checks: list[dict] = []

        async def run(name: str, messages: list[dict], verify: Callable[[llm.Completion], bool], **kw: Any) -> None:
            started = time.time()
            try:
                done = await self.chat(messages, chain=[spec], stream=False, purpose=purpose, max_tokens=700,
                                       temperature=0, **kw)
                ok = bool(verify(done))
                detail = clip(done.text or ", ".join(c["name"] + " " + c.get("arguments", "")
                                                   for c in done.tool_calls) or "пустой ответ", 140)
            except Exception as exc:  # noqa: BLE001 - every failure is a result of the check
                ok, detail = False, clip(str(exc), 200)
            checks.append({"name": name, "ok": ok, "ms": int((time.time() - started) * 1000), "detail": detail})

        await run("ответ", [{"role": "user", "content": "Столица Франции? Ответь одним словом."}],
                  lambda c: any(w in (c.text or "").lower() for w in ("париж", "paris")))
        if not reader:
            tool = {"type": "function", "function": {
                "name": "get_time", "description": "Текущее время в городе",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
            await run("инструменты", [{"role": "user", "content": "Который час в Москве? Узнай через инструмент."}],
                      lambda c: any(t["name"] == "get_time" and "оск" in (t.get("arguments") or "").lower()
                                    or t["name"] == "get_time" and "mosc" in (t.get("arguments") or "").lower()
                                    for t in c.tool_calls), tools=[tool])
        if reader or self.supports_vision(spec):
            image = "data:image/png;base64," + base64.b64encode(_square_png((220, 30, 30))).decode()
            await run("изображение", [{"role": "user", "content": [
                {"type": "text", "text": "Какого цвета этот квадрат? Ответь одним словом."},
                {"type": "image_url", "image_url": {"url": image}}]}],
                lambda c: any(w in (c.text or "").lower() for w in ("красн", "red", "алый")))
        ok = bool(checks) and all(c["ok"] for c in checks)
        text = "; ".join(f"{c['name']}: {'да' if c['ok'] else 'нет'} ({c['ms'] / 1000:.1f} с)" for c in checks)
        return {"spec": spec, "ok": ok, "checks": checks, "vision_only": reader,
                "message": ("модель подходит — " if ok else "есть проблемы — ") + text}

    def status(self) -> dict:
        now = time.time()
        today = time.strftime("%Y-%m-%d")

        def left(moment: float) -> int | None:
            return max(0, round(moment - now)) if moment > now else None

        accounts = []
        for account in self.accounts():
            if account.source == "local":
                continue
            buckets = {model: {"remaining_requests": b.remaining_requests, "limit_requests": b.limit_requests,
                               "reset_requests_in": left(b.reset_requests_at), "remaining_tokens": b.remaining_tokens,
                               "limit_tokens": b.limit_tokens, "reset_tokens_in": left(b.reset_tokens_at),
                               "cooldown_in": left(b.cooldown_until) or 0, "last_error": b.last_error,
                               "used_today": b.used_today if b.day == today else 0,
                               "tokens_today": b.tokens_today if b.day == today else 0}
                       for model, b in account.buckets.items()}
            disabled = account.disabled(now)
            accounts.append({"id": account.id, "provider": account.provider, "label": account.label,
                             "masked": account.masked, "source": account.source, "enabled": account.enabled,
                             "disabled": disabled, "disabled_reason": account.disabled_reason if disabled else "",
                             "current": bool(self._last and self._last.get("account_id") == account.id),
                             "buckets": buckets})
        get = self.settings.get
        return {"accounts": accounts, "providers": self.providers(), "chain": self.chain_for("agent"),
                "vision_chain": self.chain_for("vision"),
                "active": get("models.active"), "fast": get("models.fast"), "vision": get("models.vision"),
                "force": self.forced(), "auto_chain": self.chain_for("agent", auto=True),
                "current": {"spec": self._last["spec"], "account": self._last["account"]} if self._last else None}

    # ------------------------------------------------------------------ persistence
    def load_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _snapshot(self) -> dict:
        accounts = dict(self._state.get("accounts", {}))
        for account in (self._accounts or {}).values():
            accounts[account.id] = {"disabled_until": account.disabled_until, "disabled_reason": account.disabled_reason,
                                    "buckets": {m: asdict(b) for m, b in account.buckets.items()}}
        return {"accounts": accounts, "saved": time.time()}

    def save_state(self) -> None:
        try:
            self._state = self._snapshot()
            config.atomic_write(self.state_file, json.dumps(self._state, ensure_ascii=False))
            self._saved_at = time.time()
        except OSError as exc:
            log.warning("router state not saved: %s", exc)

    def _dirty(self, force: bool = False) -> None:
        if force or time.time() - self._saved_at > 3:
            self.save_state()


def _square_png(rgb: tuple[int, int, int], size: int = 48) -> bytes:
    """A tiny solid-colour PNG for the vision self-test (no Pillow needed)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))
