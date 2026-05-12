import os
from typing import List, Dict, Any, Optional

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from peft import PeftModel


class LocalHFQLoRAClient:
    """
    Local QLoRA Llama client using HF Transformers + PEFT.

    - Loads base model in 4-bit (QLoRA-style).
    - Applies LoRA adapter from a directory (your fine-tuned weights).
    - Exposes send_chat(messages) with the same interface as other clients.
    """

    def __init__(
        self,
        base_model_id: str,
        adapter_dir: str,
        logger,
        max_new_tokens: int = 192,
    ):
        self.base_model_id = base_model_id
        self.adapter_dir = adapter_dir
        self.logger = logger
        self.max_new_tokens = max_new_tokens

        if not os.path.isdir(self.adapter_dir):
            raise FileNotFoundError(f"LoRA adapter dir not found: {self.adapter_dir}")

        # 4-bit quantization config (QLoRA)
        self.bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        # Load tokenizer from adapter dir (you saved it there after training)
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.adapter_dir,
            use_fast=True,
            trust_remote_code=True,
        )
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "right"

        # Load base model in 4-bit, then attach LoRA adapter
        self._load_model()

    def _load_model(self):
        self.logger.log_system(
            f"[LocalHFQLoRA] Loading base model '{self.base_model_id}' with adapter at '{self.adapter_dir}'"
        )

        base_model = AutoModelForCausalLM.from_pretrained(
            self.base_model_id,
            quantization_config=self.bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )

        model = PeftModel.from_pretrained(
            base_model,
            self.adapter_dir,
        )

        model.eval()
        self.model = model

    def send_chat(self, model: Optional[str], messages: List[Dict[str, Any]]) -> str:
        """
        Match the interface of OpenAIClient/OllamaClient:
        - ignore the 'model' arg (we already know which one we're using)
        - 'messages' is the usual list of {role, content}
        """
        # TRACE: pseudo-request
        if self.logger:
            self.logger.trace_request(
                method="LOCAL",
                url="local://hf-qlora",
                headers={},
                body={"base_model": self.base_model_id,
                      "adapter_dir": self.adapter_dir,
                      "messages": messages},
                provider_label="LocalHFQLoRA",
            )

        # Build chat-style prompt with model's template
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            padding=False,
        ).to(self.model.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,     # deterministic, like temperature=0
                temperature=0.0,
                top_p=1.0,
            )

        gen_tokens = outputs[0][inputs["input_ids"].shape[-1]:]
        content = self.tokenizer.decode(gen_tokens, skip_special_tokens=True).strip()

        # TRACE: pseudo-response
        if self.logger:
            self.logger.trace_response(
                status=0,
                headers={},
                body={"content": content},
                provider_label="LocalHFQLoRA",
            )

        return content or "[LocalHFQLoRA returned empty content]"
