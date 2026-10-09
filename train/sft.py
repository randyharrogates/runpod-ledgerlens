"""One explicit Unsloth vision LoRA run, followed by a 16-bit merge."""

import argparse
import json
import logging
import time
from pathlib import Path

from train.common import (
    DEFAULT_MAX_PIXELS,
    assistant_start,
    build_messages,
    read_rows,
    resize_image,
)


def set_max_pixels(image_processor, max_pixels):
    """Cap the processor at the training pixel limit.

    transformers 5.x derives `max_pixels` from `size["longest_edge"]` (a read-only property); older
    versions expose a plain attribute. Pages are already resized to this cap, so this is a backstop.
    """
    size = getattr(image_processor, "size", None)
    try:
        shortest = size["shortest_edge"]
        size["longest_edge"]
    except (TypeError, KeyError):
        image_processor.max_pixels = max_pixels
        return
    image_processor.size = {"shortest_edge": shortest, "longest_edge": max_pixels}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="unsloth/Qwen3-VL-4B-Instruct")
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--finetune-vision", action="store_true")
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    # Same as serving's MAX_MODEL_LEN. A 2 MP page is up to ~1,950 image tokens plus the JSON answer;
    # Unsloth's 2,048 default truncated examples mid-image (rehearsal, 9 Oct).
    parser.add_argument("--max-seq-length", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--eval-steps", type=int, default=50)
    parser.add_argument("--resume-from-checkpoint", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # Unsloth must patch transformers before TRL is imported.
    import torch
    from PIL import Image
    from unsloth import FastVisionModel
    from unsloth.trainer import UnslothVisionDataCollator

    logging.info("Unsloth imported before TRL")
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("This run requires a CUDA GPU with bf16 support")
    args.out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    model, processor = FastVisionModel.from_pretrained(
        args.model,
        load_in_4bit=args.load_in_4bit,
        use_gradient_checkpointing="unsloth",
        max_seq_length=args.max_seq_length,
    )
    set_max_pixels(processor.image_processor, args.max_pixels)
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=args.finetune_vision,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=args.rank,
        lora_alpha=args.rank,
        lora_dropout=0,
        bias="none",
        random_state=args.seed,
    )
    FastVisionModel.for_training(model)

    def pages(split):
        """A Hugging Face Dataset (TRL 1.x requires one) that opens each image only when used."""
        rows = read_rows(args.data, split)
        if not rows:
            raise ValueError(f"Empty {split} dataset")

        def load(batch):
            messages = []
            for path, doc_type, target in zip(
                batch["image"], batch["doc_type"], batch["target"], strict=True
            ):
                with Image.open(path) as image:
                    image = resize_image(image, args.max_pixels)
                messages.append(build_messages(doc_type, image, target))
            return {"messages": messages}

        return Dataset.from_list(rows).with_transform(load)

    eval_steps = (
        min(args.eval_steps, args.max_steps) if args.max_steps else args.eval_steps
    )
    if eval_steps <= 0:
        raise ValueError("eval/max steps must be positive")
    trainer = SFTTrainer(
        model=model,
        processing_class=processor,
        train_dataset=pages("train"),
        eval_dataset=pages("val"),
        # resize="max": no extra resizing. The default ("min") shrinks Qwen3-VL pages to 512 px wide,
        # far below the 1 MP cap used for evaluation and serving.
        data_collator=UnslothVisionDataCollator(
            model, processor, resize="max", max_seq_length=args.max_seq_length
        ),
        args=SFTConfig(
            output_dir=str(args.out / "checkpoints"),
            num_train_epochs=args.epochs,
            max_steps=args.max_steps or -1,
            learning_rate=args.lr,
            per_device_train_batch_size=args.batch,
            per_device_eval_batch_size=1,
            gradient_accumulation_steps=args.grad_accum,
            bf16=True,
            fp16=False,
            logging_steps=10,
            eval_strategy="steps",
            eval_steps=eval_steps,
            save_strategy="steps",
            save_steps=eval_steps,
            save_total_limit=2,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            remove_unused_columns=False,
            dataset_kwargs={"skip_prepare_dataset": True},
            seed=args.seed,
            report_to="none",
            max_length=None,
        ),
    )
    # Some Unsloth versions require Hugging Face Dataset.map, which our lazy
    # image dataset intentionally does not implement. Use an explicit collator
    # fallback when that response-only helper cannot accept a lazy dataset.
    base_collator = trainer.data_collator
    masking = "unsloth.train_on_responses_only"
    try:
        from unsloth.chat_templates import train_on_responses_only

        trainer = train_on_responses_only(
            trainer,
            instruction_part="<|im_start|>user\n",
            response_part="<|im_start|>assistant\n",
        )
    except (ImportError, AttributeError, TypeError) as error:
        logging.warning(
            "Response-only helper unsupported: %s; using token masking", error
        )
        trainer.data_collator = base_collator
        masking = "explicit_assistant_delimiter"

    tokenizer = getattr(processor, "tokenizer", processor)
    delimiter = tokenizer.encode("<|im_start|>assistant\n", add_special_tokens=False)
    collator = trainer.data_collator

    def response_collator(examples):
        batch = collator(examples)
        # Enforce and verify the boundary even when Unsloth supplied masking.
        for index, ids in enumerate(batch["input_ids"].tolist()):
            boundary = assistant_start(ids, delimiter)
            batch["labels"][index, :boundary] = -100
            if not (batch["labels"][index, boundary:] != -100).any():
                raise RuntimeError("No supervised assistant tokens after masking")
        return batch

    trainer.data_collator = response_collator
    # Verify masking on the actual multimodal collator before spending GPU hours.
    batch = trainer.data_collator([trainer.train_dataset[0]])
    labels = batch["labels"]
    if not (labels == -100).any() or not (labels != -100).any():
        raise RuntimeError("Assistant-only label masking failed preflight")
    logging.info("Mask preflight: supervised tokens=%d", (labels != -100).sum().item())
    result = trainer.train(
        resume_from_checkpoint=str(args.resume_from_checkpoint)
        if args.resume_from_checkpoint
        else None
    )
    evaluation = trainer.evaluate()
    model.save_pretrained(str(args.out / "adapter"))
    processor.save_pretrained(str(args.out / "adapter"))
    model.save_pretrained_merged(
        str(args.out / "merged"), processor, save_method="merged_16bit"
    )
    processor.save_pretrained(str(args.out / "merged"))
    summary = {
        "args": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "steps": trainer.state.global_step,
        "final_train_loss": next(
            (
                entry["loss"]
                for entry in reversed(trainer.state.log_history)
                if "loss" in entry
            ),
            result.metrics.get("train_loss"),
        ),
        "average_train_loss": result.metrics.get("train_loss"),
        "final_eval_loss": evaluation.get("eval_loss"),
        "best_eval_loss": trainer.state.best_metric,
        "wall_seconds": time.perf_counter() - start,
        "gpu_name": torch.cuda.get_device_name(),
        "peak_memory_bytes": torch.cuda.max_memory_allocated(),
        "max_pixels": args.max_pixels,
        "response_only": True,
        "masking": masking,
        "log_history": trainer.state.log_history,
    }
    (args.out / "train_summary.json").write_text(json.dumps(summary, indent=2))
    logging.info("Finished: %s", summary)


if __name__ == "__main__":
    main()
