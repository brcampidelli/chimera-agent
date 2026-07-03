"""Opt-in model evolution — curate trajectories into training-ready data + a recipe.

The :class:`~chimera.ecosystem.trajectory.TrajectoryCollector` logs raw runs. This
module turns that log into *curated* SFT/DPO datasets (reward gating, dedup,
preference margins), reports whether there is enough signal to bother training,
and emits a self-contained, runnable LoRA recipe (a ``train.py`` + README +
requirements). Training itself is **external and never automatic** — Chimera
prepares the data and the script, then stops. Changing model weights is a
deliberate, reviewed act, not a background side effect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.ecosystem.trajectory import Trajectory, TrajectoryCollector


@dataclass
class CurationConfig:
    """How to filter raw trajectories into training data.

    The ``min_steps`` / ``max_per_prompt`` knobs apply the *Data Recipes for Agentic
    Models* findings: long-horizon traces (>= 5 turns) are higher-value supervision, and
    task-description diversity is the bottleneck at scale, so capping examples per unique
    task avoids over-representing a few prompts.
    """

    min_reward: float = 0.0
    dedup: bool = True
    min_margin: float = 0.0  # DPO: require chosen.reward - rejected.reward > this
    min_steps: int = 0  # long-horizon: keep only traces with >= this many tool-calling steps
    max_per_prompt: int = 0  # diversity: cap SFT examples per unique task (0 = unlimited)
    # SkillCoach process filter: keep only traces whose step-following score >= this, so a
    # lucky-but-sloppy success (wrong/failed tool steps) is not trained on. 0.0 = off.
    min_process: float = 0.0


def curate_sft(trajectories: list[Trajectory], config: CurationConfig | None = None) -> list[dict[str, Any]]:
    """Successful, reward-gated, de-duplicated chat examples (highest reward first).

    Applies the long-horizon and diversity recipes when configured.
    """
    cfg = config or CurationConfig()
    seen: set[tuple[str, str]] = set()
    per_prompt: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for item in sorted(trajectories, key=lambda t: -t.reward):
        if item.outcome != "success" or item.reward < cfg.min_reward:
            continue
        if item.steps < cfg.min_steps:  # long-horizon recipe
            continue
        if item.process_score() < cfg.min_process:  # SkillCoach process-quality filter
            continue
        prompt = item.prompt.strip()
        if cfg.max_per_prompt and per_prompt.get(prompt, 0) >= cfg.max_per_prompt:
            continue  # diversity recipe: cap examples per unique task
        key = (prompt, item.response.strip())
        if cfg.dedup and key in seen:
            continue
        seen.add(key)
        per_prompt[prompt] = per_prompt.get(prompt, 0) + 1
        rows.append(
            {
                "messages": [
                    {"role": "user", "content": item.prompt},
                    {"role": "assistant", "content": item.response},
                ]
            }
        )
    return rows


def curate_dpo(trajectories: list[Trajectory], config: CurationConfig | None = None) -> list[dict[str, Any]]:
    """Preference pairs per prompt: best success vs worst failure, with a reward margin."""
    cfg = config or CurationConfig()
    best: dict[str, Trajectory] = {}
    worst: dict[str, Trajectory] = {}
    for item in trajectories:
        if (
            item.outcome == "success"
            and item.process_score() >= cfg.min_process
            and (item.prompt not in best or item.reward > best[item.prompt].reward)
        ):
            best[item.prompt] = item
        elif item.outcome == "failure" and (item.prompt not in worst or item.reward < worst[item.prompt].reward):
            worst[item.prompt] = item
    rows: list[dict[str, Any]] = []
    for prompt, chosen in best.items():
        rejected = worst.get(prompt)
        if rejected is None or (chosen.reward - rejected.reward) <= cfg.min_margin:
            continue
        rows.append({"prompt": prompt, "chosen": chosen.response, "rejected": rejected.response})
    return rows


@dataclass
class EvolutionReadiness:
    """Whether the collected trajectories are worth a training run."""

    total: int
    successes: int
    failures: int
    sft_examples: int
    dpo_pairs: int
    min_examples: int = 30

    @property
    def ready(self) -> bool:
        return self.sft_examples >= self.min_examples or self.dpo_pairs >= self.min_examples

    @property
    def reason(self) -> str:
        have = max(self.sft_examples, self.dpo_pairs)
        if self.ready:
            return "enough signal to attempt a LoRA run"
        return f"keep collecting — need >= {self.min_examples} curated examples (have {have})"


def assess(
    collector: TrajectoryCollector,
    config: CurationConfig | None = None,
    *,
    min_examples: int = 30,
) -> EvolutionReadiness:
    """Summarize how much training signal the collector holds."""
    cfg = config or CurationConfig()
    items = collector.all()
    return EvolutionReadiness(
        total=len(items),
        successes=sum(1 for t in items if t.outcome == "success"),
        failures=sum(1 for t in items if t.outcome == "failure"),
        sft_examples=len(curate_sft(items, cfg)),
        dpo_pairs=len(curate_dpo(items, cfg)),
        min_examples=min_examples,
    )


def write_jsonl(out_path: Path, rows: list[dict[str, Any]]) -> int:
    """Write rows as JSONL; returns the count."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""), encoding="utf-8"
    )
    return len(rows)


_SFT_SCRIPT = '''\
"""LoRA SFT for Chimera — run on a GPU (Colab works). External + opt-in.

  pip install -r requirements.txt
  python train.py
The adapter lands in ./chimera-lora; load it with peft for inference.
trl's API shifts across versions — check the trl docs if a call signature differs.
"""
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

BASE_MODEL = "{base_model}"
DATASET = "{dataset}"  # JSONL of {{"messages": [...]}}

dataset = load_dataset("json", data_files=DATASET, split="train")
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, device_map="auto")

trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM"),
    args=SFTConfig(output_dir="./chimera-lora", num_train_epochs=1,
                   per_device_train_batch_size=2, learning_rate=2e-4),
)
trainer.train()
trainer.save_model("./chimera-lora")
'''

_DPO_SCRIPT = '''\
"""LoRA DPO for Chimera — run on a GPU (Colab works). External + opt-in.

  pip install -r requirements.txt
  python train.py
The adapter lands in ./chimera-dpo. Check the trl docs if a call signature differs.
"""
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import DPOConfig, DPOTrainer

BASE_MODEL = "{base_model}"
DATASET = "{dataset}"  # JSONL of {{"prompt", "chosen", "rejected"}}

dataset = load_dataset("json", data_files=DATASET, split="train")
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, device_map="auto")

trainer = DPOTrainer(
    model=model,
    train_dataset=dataset,
    processing_class=tokenizer,
    peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM"),
    args=DPOConfig(output_dir="./chimera-dpo", num_train_epochs=1,
                   per_device_train_batch_size=2, learning_rate=5e-5),
)
trainer.train()
trainer.save_model("./chimera-dpo")
'''

_REQUIREMENTS = "trl>=0.9\npeft>=0.11\ntransformers>=4.40\ndatasets>=2.19\naccelerate>=0.30\n"

_README = """\
# Chimera LoRA recipe ({fmt})

Generated by `chimera evolve recipe`. Chimera prepared the data and this script;
**training is external and opt-in** — it changes model behaviour, so review the
data and the result before using the adapter anywhere.

## Steps
1. Export the dataset (if you haven't):
   `chimera evolve export --format {fmt} --out {dataset}`
2. On a machine with a GPU (or Colab): `pip install -r requirements.txt`
3. `python train.py`  → the LoRA adapter is written to `./chimera-{fmt}`.
4. Serve the base model + adapter (vLLM/TGI/peft) and point Chimera's
   `CHIMERA_DEFAULT_MODEL` at it.

Base model: `{base_model}`
"""


def write_recipe(
    out_dir: Path,
    *,
    base_model: str = "meta-llama/Llama-3.1-8B-Instruct",
    fmt: str = "sft",
    dataset: str = "dataset.jsonl",
) -> list[Path]:
    """Emit a runnable LoRA training recipe (train.py + README + requirements)."""
    if fmt not in ("sft", "dpo"):
        raise ValueError(f"fmt must be 'sft' or 'dpo', got {fmt!r}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = (_SFT_SCRIPT if fmt == "sft" else _DPO_SCRIPT).format(base_model=base_model, dataset=dataset)
    files = {
        "train.py": script,
        "requirements.txt": _REQUIREMENTS,
        "README.md": _README.format(fmt=fmt, base_model=base_model, dataset=dataset),
    }
    written: list[Path] = []
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
