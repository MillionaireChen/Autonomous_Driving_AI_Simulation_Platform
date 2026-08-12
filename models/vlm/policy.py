"""A vision-language model as the high-level decision module.

    front camera + speed + lead-vehicle context  ->  one manoeuvre

The model never touches the pedals. `simulator/lowlevel.DecisionExecutor` runs a
conventional controller from whatever it decides, which is what makes a 7B model
viable in a 20 Hz loop at all: measured 101 ms per decision, fine at 2 Hz and
hopeless for steering.

Two deliberate choices worth stating:

* **Constrained decoding by scoring, not by parsing.** Rather than generating
  free text and hoping to find a keyword, the model scores the first token of
  each allowed manoeuvre and the best one wins. A model physically cannot answer
  with something outside the set, so there is no parse failure to handle and no
  prompt-injection surface in the reply path.

* **A LoRA adapter is optional and hot-swappable.** The base weights are 16 GB
  and shared; an adapter is tens of megabytes. That asymmetry is the entire
  reason federated fine-tuning of a model this size is practical.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import torch
from PIL import Image

from simulator.policy import DrivingPolicy
from simulator.types import DECISIONS, HighLevelDecision, Observation

DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"

#: The word the model answers with, and the manoeuvre it means.
#:
#: The wire protocol and telemetry keep the descriptive names from spec section
#: 14; the *answer vocabulary* is chosen so every option differs in its first
#: token. CHANGE_LEFT and CHANGE_RIGHT both begin with the token "CHANGE", so
#: scoring first tokens could not tell them apart - an assertion in __init__
#: caught that rather than letting it silently pick one of the two.
ANSWERS = {
    "KEEP": "KEEP_LANE",
    "SLOW": "SLOW_DOWN",
    "BRAKE": "BRAKE",
    "LEFT": "CHANGE_LEFT",
    "RIGHT": "CHANGE_RIGHT",
}

PROMPT = (
    "You are the high-level decision module of a highway driving system.\n"
    "You see the forward camera. Current speed: {speed:.1f} m/s.\n"
    "Lead vehicle: {lead}\n\n"
    "Pick the single safest action. Reply with one word only, from:\n"
    "KEEP  - road clear, hold speed\n"
    "SLOW  - traffic ahead, ease off\n"
    "BRAKE - imminent collision risk, brake hard\n"
    "LEFT  - lane blocked, move to the left lane\n"
    "RIGHT - lane blocked, move to the right lane\n"
)


def describe_lead(observation: Observation) -> str:
    """The lead vehicle as a sentence, or an honest 'unknown'.

    A camera-only deployment has no lead-vehicle ground truth, so this says so
    rather than implying an empty road - claiming "none detected" when the
    sensor is simply absent would teach the model the wrong thing.
    """
    lead = observation.lead_vehicle
    if lead is None:
        return "none detected"
    closing = observation.speed_mps - lead.speed_mps
    if closing > 0.2:
        return (f"{lead.gap_m:.0f} m ahead, closing at {closing:.1f} m/s")
    if closing < -0.2:
        return f"{lead.gap_m:.0f} m ahead, pulling away"
    return f"{lead.gap_m:.0f} m ahead, matching speed"


class VLMDecisionAgent(DrivingPolicy):
    name = "vlm"
    model_type = "HIGH_LEVEL_POLICY"
    required_sensors = ("rgb_front", "speed", "lead_vehicle")

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        adapter: Optional[str | Path] = None,
        device: str = "cuda",
        max_pixels: int = 384 * 384,
        client_id: str = "",
    ) -> None:
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        self.device = device
        self.client_id = client_id
        self.model_id = model_id
        self.adapter_path = str(adapter) if adapter else ""

        self.processor = AutoProcessor.from_pretrained(model_id, max_pixels=max_pixels)
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_id, dtype=torch.bfloat16, device_map=device,
        ).eval()

        if adapter:
            from peft import PeftModel

            self.model = PeftModel.from_pretrained(self.model, str(adapter))
            self.model.eval()

        # First token of each answer word, for constrained scoring.
        tokenizer = self.processor.tokenizer
        self._answer_tokens = {
            word: tokenizer.encode(word, add_special_tokens=False)[0]
            for word in ANSWERS
        }
        if len(set(self._answer_tokens.values())) != len(ANSWERS):
            raise RuntimeError(
                "two answers share a first token; constrained scoring needs "
                f"distinct ones: {self._answer_tokens}"
            )
        assert set(ANSWERS.values()) <= set(DECISIONS), \
            "every answer must map to a known manoeuvre"

        self.last_decision = "KEEP_LANE"
        self.last_scores: dict[str, float] = {}

    def reset(self, config: dict[str, Any]) -> None:
        self.last_decision = "KEEP_LANE"
        # The first forward pass includes kernel autotuning and took 1718 ms
        # against a 101 ms steady state - it would blow the deadline on tick 0.
        blank = Image.new("RGB", (256, 144))
        self._decide(blank, 0.0, "none detected")

    @torch.inference_mode()
    def _decide(self, image: Image.Image, speed: float,
                lead: str) -> tuple[str, dict[str, float]]:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": PROMPT.format(speed=speed, lead=lead)},
        ]}]
        prompt = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
        inputs = self.processor(text=[prompt], images=[image],
                               return_tensors="pt").to(self.device)

        logits = self.model(**inputs).logits[0, -1]
        scores = {
            ANSWERS[word]: float(logits[token])
            for word, token in self._answer_tokens.items()
        }
        return max(scores, key=scores.get), scores

    def infer(self, observation: Observation) -> HighLevelDecision:
        if observation.rgb_front is None:
            raise RuntimeError("vlm requires rgb_front and received none")

        image = Image.fromarray(observation.rgb_front)
        decision, scores = self._decide(
            image, observation.speed_mps, describe_lead(observation)
        )
        self.last_decision = decision
        self.last_scores = scores

        # A margin over the runner-up, as a rough confidence. Useful in the
        # event log for seeing whether a decision was clear-cut or a coin flip.
        ordered = sorted(scores.values(), reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else 0.0
        return HighLevelDecision(
            decision=decision,
            reason=f"{self.client_id or 'vlm'} margin {margin:.2f}",
            confidence=margin,
        )
