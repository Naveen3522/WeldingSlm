"""QLoRA fine-tune of Llama-3.2-1B-Instruct on a dataset written by welding_ai.training.datasets.

    python train.py --train train.jsonl --out adapter_dir --checkpoints checkpoint_dir [--max-steps N] [--resume]

The recipe is the one the current model was trained with, recovered from its checkpoint: LoRA r=16, alpha=32, all seven projection modules, learning rate 2e-4
with a cosine schedule and 5% warmup, batch 1 with gradient accumulation 8, fp16, 8-bit AdamW, one epoch. Two things are done on purpose:
  * the text is laid out the way the model is served (the Ollama template: header, blank line, content, end-of-turn), not with the library's default
    template, so training and serving see the same text;
  * only the assistant's answer counts in the loss, so the model learns to answer and refuse, not to repeat the excerpts it is shown.

It prints lines the retrain job reads:   PROGRESS {"step": s, "max_steps": n, "loss": l, "elapsed": seconds}      DONE {"adapter": dir, "steps": n}
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import unsloth  # noqa: F401  (must come before trl / transformers so it can patch them)
from unsloth import FastLanguageModel
from unsloth.chat_templates import train_on_responses_only

BASE = "unsloth/llama-3.2-1b-instruct-unsloth-bnb-4bit"
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
USER_PART = "<|start_header_id|>user<|end_header_id|>\n\n"
ASSISTANT_PART = "<|start_header_id|>assistant<|end_header_id|>\n\n"
SEED = 3407


def serve_format(messages):
    """The conversation laid out as Ollama serves it (the model's own start-of-text token is added by the tokenizer)."""
    return "".join(f"<|start_header_id|>{m['role']}<|end_header_id|>\n\n{m['content']}<|eot_id|>" for m in messages)


def say(tag, payload):
    print(f"{tag} {json.dumps(payload)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--checkpoints", required=True)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--max-seq", type=int, default=2048)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    from datasets import Dataset
    from transformers import TrainerCallback
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(line) for line in Path(a.train).read_text(encoding="utf-8").splitlines() if line.strip()]
    say("STAGE", {"name": "loading the base model", "rows": len(rows)})
    model, tokenizer = FastLanguageModel.from_pretrained(model_name=BASE, max_seq_length=a.max_seq, dtype=None, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(model, r=16, target_modules=TARGETS, lora_alpha=32, lora_dropout=0, bias="none",
                                             use_gradient_checkpointing="unsloth", random_state=SEED)
    data = Dataset.from_list([{"text": serve_format(r["messages"])} for r in rows])

    class Progress(TrainerCallback):
        def __init__(self):
            self.t0, self.loss = time.time(), None

        def on_train_begin(self, args, state, control, **kw):
            say("PROGRESS", {"step": state.global_step, "max_steps": state.max_steps, "loss": None, "elapsed": 0})

        def on_log(self, args, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                self.loss = round(float(logs["loss"]), 4)

        def on_step_end(self, args, state, control, **kw):
            say("PROGRESS", {"step": state.global_step, "max_steps": state.max_steps, "loss": self.loss, "elapsed": round(time.time() - self.t0, 1)})

    trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=data, callbacks=[Progress()], args=SFTConfig(
        per_device_train_batch_size=1, gradient_accumulation_steps=8, warmup_ratio=0.05, num_train_epochs=a.epochs, max_steps=a.max_steps,
        learning_rate=2e-4, fp16=True, bf16=False, logging_steps=1, optim="adamw_8bit", weight_decay=0.01, lr_scheduler_type="cosine", seed=SEED,
        output_dir=a.checkpoints, save_strategy="steps", save_steps=50, save_total_limit=2, eval_strategy="no", report_to="none",
        dataset_text_field="text", max_length=a.max_seq, packing=False, dataset_num_proc=1, dataloader_num_workers=0))
    trainer = train_on_responses_only(trainer, instruction_part=USER_PART, response_part=ASSISTANT_PART)

    say("STAGE", {"name": "training"})
    last = None
    if a.resume and Path(a.checkpoints).is_dir():
        found = sorted(Path(a.checkpoints).glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[1]))
        last = bool(found)
    trainer.train(resume_from_checkpoint=last or None)

    say("STAGE", {"name": "saving the adapter"})
    Path(a.out).mkdir(parents=True, exist_ok=True)
    model.save_pretrained(a.out)
    tokenizer.save_pretrained(a.out)
    say("DONE", {"adapter": a.out, "steps": trainer.state.global_step})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:
        say("FAILED", {"error": f"{type(e).__name__}: {str(e)[:400]}"})
        raise
