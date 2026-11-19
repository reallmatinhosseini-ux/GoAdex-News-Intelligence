"""Claude: grounded narrative synthesis, cited web research, and the analyst Q&A.

The LLM never produces probabilities or numbers that enter the forecast. It is
given the structured evidence and asked to explain it; web-research claims are
kept only when their URL actually appeared in the search results (anti-fabrication).
"""
import json
import re
import time

from .. import config, db
from .base import AI, NEWS, Provider, ProviderError

SYSTEM = (
    "You are the senior macro strategist of an institutional research desk. You are skeptical, "
    "evidence-driven and explicit about uncertainty. Rules: (1) Use only facts supplied in the "
    "evidence or found via your search tools, and attribute each to its source. (2) Never invent "
    "numbers, releases, prices, citations or accuracy statistics. (3) Never use information dated "
    "after the stated 'as_of' time when reasoning about a pre-release forecast. (4) Your narrative "
    "is an interpretation, not a calibrated probability; never state a probability unless one is "
    "given in the evidence with a 'validated' label. (5) Say plainly when evidence is thin or conflicting."
)


class Claude(Provider):
    name = "Anthropic Claude"
    category = AI
    requires_key = True
    data_delay = "n/a — interpretation layer"
    docs = "https://platform.claude.com/docs"
    notes = "Narratives are labelled AI-generated; web research keeps only claims whose cited URL was returned by search."

    def configured(self):
        return bool(config.ANTHROPIC_API_KEY)

    def _client(self):
        import anthropic
        return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=300, max_retries=2)

    def _call(self, messages, tools=None, effort="medium", max_tokens=16000):
        if not self.configured():
            from .base import NotConfigured
            raise NotConfigured("ANTHROPIC_API_KEY not set")
        import anthropic
        client = self._client()
        t0 = time.monotonic()
        try:
            kwargs = dict(model=config.ANTHROPIC_MODEL, max_tokens=max_tokens, system=SYSTEM,
                          messages=messages, output_config={"effort": effort},
                          betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            if tools:
                kwargs["tools"] = tools
            resp = client.beta.messages.create(**kwargs)
            # Server tools may pause long turns; continue up to 3 times.
            for _ in range(3):
                if resp.stop_reason != "pause_turn":
                    break
                messages = messages + [{"role": "assistant", "content": resp.content}]
                kwargs["messages"] = messages
                resp = client.beta.messages.create(**kwargs)
            if resp.stop_reason == "refusal":
                raise ProviderError("Model declined the request")
            self._ok(int((time.monotonic() - t0) * 1000))
            return resp
        except anthropic.RateLimitError as e:
            self._err(f"rate limited: {e}")
            raise ProviderError("rate limited") from e
        except anthropic.APIStatusError as e:
            self._err(f"HTTP {e.status_code}: {str(e)[:200]}")
            raise ProviderError(str(e)) from e
        except anthropic.APIConnectionError as e:
            self._err(f"connection: {e}")
            raise ProviderError(str(e)) from e

    @staticmethod
    def _text(resp) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    @staticmethod
    def _json(text: str):
        m = re.search(r"```json\s*(.*?)```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    # ------------------------------------------------------------------ research
    def web_research(self, event_title: str, release_utc: str, as_of: str, components: list[str]) -> dict:
        prompt = f"""Research the upcoming release: {event_title}, scheduled {release_utc} (UTC).
Current time (as_of): {as_of}. Use web search. Prioritise primary sources (BLS, BEA, Federal Reserve,
ECB) and reputable, attributable financial media or bank research summaries.

Find: (a) the market consensus for each of these components where published: {', '.join(components)};
(b) the previous value and any revision; (c) range of forecasts if published; (d) 3-6 attributable
analyst views on what matters for this release and how markets might react (gold, USD, yields);
(e) anything changed in the last 24h (Fed speakers, related data).

Return ONLY a JSON object in a ```json block with keys:
"consensus": [{{"component","value","unit","source_name","source_url","published"}}],
"previous": [{{"component","value","revised_from","source_name","source_url"}}],
"views": [{{"claim","stance":"hawkish|dovish|neutral","source_name","source_url","published"}}],
"recent_developments": [{{"claim","source_name","source_url","published"}}],
"gaps": ["what you could not verify"]
Every item must cite a URL you actually opened or saw in search results. Omit anything you cannot cite."""
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
        resp = self._call([{"role": "user", "content": prompt}], tools=tools, effort="medium")
        seen_urls = set()
        for b in resp.content:
            if getattr(b, "type", "") == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    u = getattr(r, "url", None)
                    if u:
                        seen_urls.add(u.rstrip("/"))
        data = self._json(self._text(resp)) or {}
        dropped = 0
        for k in ("consensus", "previous", "views", "recent_developments"):
            kept = []
            for it in data.get(k, []) or []:
                u = (it.get("source_url") or "").rstrip("/")
                if u and u in seen_urls:
                    it["category"] = NEWS if k in ("views", "recent_developments") else "consensus_ai_extracted"
                    it["verified_url"] = True
                    kept.append(it)
                else:
                    dropped += 1
            data[k] = kept
        data["dropped_unverifiable"] = dropped
        data["searched_urls"] = sorted(seen_urls)[:40]
        data["as_of"] = as_of
        data["model"] = config.ANTHROPIC_MODEL
        return data

    # ------------------------------------------------------------------ narrative
    def narrative(self, report: dict) -> dict:
        slim = {k: report.get(k) for k in ("event", "as_of", "regime", "assets", "scenarios", "quant",
                                            "consensus", "data_quality", "web_research")}
        prompt = ("Write the strategist's summary for this pre-release report. Explain WHY the preferred "
                  "directional view for XAUUSD (and EURUSD) is what it is, the strongest argument against it, "
                  "what is likely priced in, and what would invalidate it. If the engine's bias is neutral or "
                  "evidence is contradictory, say so and do not manufacture conviction. 180-260 words, plain "
                  "prose, then a line 'Key invalidation:' and a line 'Watch first:'.\n\nEVIDENCE:\n"
                  + json.dumps(slim, default=str)[:60000])
        resp = self._call([{"role": "user", "content": prompt}], effort="medium", max_tokens=8000)
        return {"text": self._text(resp).strip(), "model": config.ANTHROPIC_MODEL, "category": AI,
                "generated_at": db.iso(db.now_utc()), "label": "AI-generated interpretation — not a probability"}

    def answer(self, question: str, context: dict, history: list[dict]) -> str:
        msgs = []
        for h in history[-8:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content":
                     f"Platform context (reports, revisions, evidence — cite these):\n"
                     f"{json.dumps(context, default=str)[:120000]}\n\nQuestion: {question}\n\n"
                     "Answer concisely as the desk strategist. Reference specific evidence, sources and "
                     "timestamps from the context. If the context does not contain the answer, say so."})
        resp = self._call(msgs, effort="medium", max_tokens=8000)
        return self._text(resp).strip()
