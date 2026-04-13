import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class UsageSnapshot:
    daily_usd: float
    monthly_usd: float
    run_usd: float
    run_requests: int


class UsageLimitError(RuntimeError):
    pass


class UsageGuard:
    def __init__(
        self,
        state_path: str,
        daily_budget_usd: float,
        monthly_budget_usd: float,
        run_budget_usd: float,
        max_requests_per_run: int,
        max_tokens_per_run: int,
        input_cost_per_1m: float,
        output_cost_per_1m: float,
        image_standard_cost: float,
        image_hd_cost: float,
    ):
        self.state_path = state_path
        self.daily_budget_usd = max(0.0, daily_budget_usd)
        self.monthly_budget_usd = max(0.0, monthly_budget_usd)
        self.run_budget_usd = max(0.0, run_budget_usd)
        self.max_requests_per_run = max(1, max_requests_per_run)
        self.max_tokens_per_run = max(1, max_tokens_per_run)
        self.input_cost_per_1m = max(0.0, input_cost_per_1m)
        self.output_cost_per_1m = max(0.0, output_cost_per_1m)
        self.image_standard_cost = max(0.0, image_standard_cost)
        self.image_hd_cost = max(0.0, image_hd_cost)
        self._run_input_tokens = 0
        self._run_output_tokens = 0

        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        self._state = self._load_state()
        self._ensure_periods()

    @classmethod
    def from_env(cls, output_dir: str) -> "UsageGuard":
        logs_dir = os.path.join(output_dir, "logs")
        state_path = os.path.join(logs_dir, "usage_state.json")
        return cls(
            state_path=state_path,
            daily_budget_usd=float(os.getenv("OPENAI_DAILY_BUDGET_USD", "10")),
            monthly_budget_usd=float(
                os.getenv("OPENAI_MONTHLY_BUDGET_USD", "200")),
            run_budget_usd=float(os.getenv("OPENAI_RUN_BUDGET_USD", "2")),
            max_requests_per_run=int(
                os.getenv("OPENAI_MAX_REQUESTS_PER_RUN", "25")),
            max_tokens_per_run=int(
                os.getenv("OPENAI_MAX_TOKENS_PER_RUN", "100000")),
            input_cost_per_1m=float(
                os.getenv("OPENAI_INPUT_COST_PER_1M_USD", "5")),
            output_cost_per_1m=float(
                os.getenv("OPENAI_OUTPUT_COST_PER_1M_USD", "15")),
            image_standard_cost=float(
                os.getenv("OPENAI_IMAGE_STANDARD_COST_USD", "0.04")),
            image_hd_cost=float(os.getenv("OPENAI_IMAGE_HD_COST_USD", "0.08")),
        )

    def _load_state(self) -> dict[str, Any]:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "daily": {"date": "", "usd": 0.0},
            "monthly": {"month": "", "usd": 0.0},
            "totals": {"requests": 0},
        }

    def _save_state(self) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    def _ensure_periods(self) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        month = datetime.utcnow().strftime("%Y-%m")
        if self._state["daily"].get("date") != today:
            self._state["daily"] = {"date": today, "usd": 0.0}
        if self._state["monthly"].get("month") != month:
            self._state["monthly"] = {"month": month, "usd": 0.0}
        self._save_state()

    def snapshot(self) -> UsageSnapshot:
        return UsageSnapshot(
            daily_usd=float(self._state["daily"]["usd"]),
            monthly_usd=float(self._state["monthly"]["usd"]),
            run_usd=float(self._state.get("run_usd", 0.0)),
            run_requests=int(self._state.get("run_requests", 0)),
        )

    def _assert_caps(self, next_cost_usd: float = 0.0, next_requests: int = 0, next_tokens: int = 0) -> None:
        daily = float(self._state["daily"]["usd"])
        monthly = float(self._state["monthly"]["usd"])
        run_usd = float(self._state.get("run_usd", 0.0))
        run_requests = int(self._state.get("run_requests", 0))
        projected_tokens = self._run_input_tokens + \
            self._run_output_tokens + next_tokens

        if daily + next_cost_usd > self.daily_budget_usd:
            raise UsageLimitError(
                f"Daily budget reached: ${daily:.2f}/${self.daily_budget_usd:.2f}"
            )
        if monthly + next_cost_usd > self.monthly_budget_usd:
            raise UsageLimitError(
                f"Monthly budget reached: ${monthly:.2f}/${self.monthly_budget_usd:.2f}"
            )
        if run_usd + next_cost_usd > self.run_budget_usd:
            raise UsageLimitError(
                f"Run budget reached: ${run_usd:.2f}/${self.run_budget_usd:.2f}"
            )
        if run_requests + next_requests > self.max_requests_per_run:
            raise UsageLimitError(
                f"Run request limit reached: {run_requests}/{self.max_requests_per_run}"
            )
        if projected_tokens > self.max_tokens_per_run:
            raise UsageLimitError(
                f"Run token limit reached: {projected_tokens}/{self.max_tokens_per_run}"
            )

    def start_run(self) -> None:
        self._ensure_periods()
        self._state["run_usd"] = 0.0
        self._state["run_requests"] = 0
        self._run_input_tokens = 0
        self._run_output_tokens = 0
        self._assert_caps()
        self._save_state()
        logging.info("Usage guard active: run budget checks enabled.")

    def register_chat_usage(self, usage_obj: Any) -> None:
        prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        completion_tokens = int(
            getattr(usage_obj, "completion_tokens", 0) or 0)
        if prompt_tokens == 0 and completion_tokens == 0:
            # Keep strict request counting even if token usage isn't returned.
            self._assert_caps(next_requests=1)
            self._state["run_requests"] = int(
                self._state.get("run_requests", 0)) + 1
            self._state["totals"]["requests"] = int(
                self._state["totals"].get("requests", 0)) + 1
            self._save_state()
            return

        usd = ((prompt_tokens / 1_000_000) * self.input_cost_per_1m) + (
            (completion_tokens / 1_000_000) * self.output_cost_per_1m
        )
        self._assert_caps(next_cost_usd=usd, next_requests=1,
                          next_tokens=prompt_tokens + completion_tokens)
        self._run_input_tokens += prompt_tokens
        self._run_output_tokens += completion_tokens
        self._apply_usage(usd, 1)
        logging.info(
            f"Usage updated: +${usd:.4f}, run=${self._state['run_usd']:.4f}, "
            f"day=${self._state['daily']['usd']:.4f}, month=${self._state['monthly']['usd']:.4f}"
        )

    def register_image_generation(self, quality: str) -> None:
        q = (quality or "hd").strip().lower()
        usd = self.image_hd_cost if q == "hd" else self.image_standard_cost
        self._assert_caps(next_cost_usd=usd, next_requests=1)
        self._apply_usage(usd, 1)
        logging.info(
            f"Image usage updated ({q}): +${usd:.4f}, run=${self._state['run_usd']:.4f}"
        )

    def _apply_usage(self, usd: float, requests: int) -> None:
        self._state["daily"]["usd"] = float(self._state["daily"]["usd"]) + usd
        self._state["monthly"]["usd"] = float(
            self._state["monthly"]["usd"]) + usd
        self._state["run_usd"] = float(self._state.get("run_usd", 0.0)) + usd
        self._state["run_requests"] = int(
            self._state.get("run_requests", 0)) + requests
        self._state["totals"]["requests"] = int(
            self._state["totals"].get("requests", 0)) + requests
        self._save_state()
