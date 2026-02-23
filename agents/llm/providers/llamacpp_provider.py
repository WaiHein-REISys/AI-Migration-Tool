"""
LlamaCpp Local Provider
========================
Runs any GGUF model locally via llama-cpp-python.
No internet required. Full GPU acceleration support via CUDA/Metal.

Required env vars:
    LLAMACPP_MODEL_PATH  – absolute path to the .gguf model file

Optional env vars:
    LLAMACPP_N_CTX       – context window size (default: 4096)
    LLAMACPP_N_GPU_LAYERS– GPU layers to offload (-1 = all, 0 = CPU only; default: -1)
    LLM_MAX_TOKENS       – max tokens to generate (default: 8192)
    LLM_TEMPERATURE      – temperature (default: 0.2)

Install:
    # CPU only
    pip install llama-cpp-python

    # With CUDA GPU support (NVIDIA)
    CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python

    # With Metal GPU support (Apple Silicon)
    CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python

Popular GGUF models to download:
    # Mistral 7B Q4 (good balance of quality/speed)
    https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF

    # CodeLlama 13B Q4 (code-focused)
    https://huggingface.co/TheBloke/CodeLlama-13B-Instruct-GGUF

    # DeepSeek Coder 7B Q4 (excellent for code migration)
    https://huggingface.co/TheBloke/deepseek-coder-6.7B-instruct-GGUF

    # Qwen2.5 Coder 7B Q4 (state-of-the-art code model)
    https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from agents.llm.base import (
    BaseLLMProvider,
    LLMConfig,
    LLMMessage,
    LLMNotAvailableError,
    LLMProviderError,
    LLMResponse,
)

logger = logging.getLogger(__name__)


class LlamaCppProvider(BaseLLMProvider):
    """
    Local GGUF model inference via llama-cpp-python.
    Uses the ChatML / chatml_function_calling template for system+user messages.
    """

    def _setup(self) -> None:
        model_path = self.config.model_path or os.environ.get("LLAMACPP_MODEL_PATH", "")

        if not model_path:
            logger.warning(
                "LlamaCppProvider: LLAMACPP_MODEL_PATH not set. "
                "Provider will be unavailable."
            )
            self._client = None
            return

        if not Path(model_path).exists():
            logger.warning(
                "LlamaCppProvider: model file not found: %s. "
                "Provider will be unavailable.",
                model_path,
            )
            self._client = None
            return

        try:
            from llama_cpp import Llama  # type: ignore

            n_ctx        = self.config.n_ctx
            n_gpu_layers = self.config.n_gpu_layers

            logger.info(
                "Loading LlamaCpp model: %s (n_ctx=%d, n_gpu_layers=%d) ...",
                model_path, n_ctx, n_gpu_layers,
            )
            self._client = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
                chat_format="chatml",   # works for most instruct models
                verbose=False,
            )
            logger.info(
                "LlamaCppProvider ready: model=%s", Path(model_path).name
            )
        except ImportError:
            logger.warning(
                "LlamaCppProvider: 'llama-cpp-python' not installed. "
                "pip install llama-cpp-python"
            )
            self._client = None
        except Exception as exc:
            logger.error("LlamaCppProvider: failed to load model: %s", exc)
            self._client = None

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
    ) -> LLMResponse:
        if not self._client:
            raise LLMNotAvailableError(
                "LlamaCppProvider not configured. "
                "Set LLAMACPP_MODEL_PATH to a valid .gguf file and pip install llama-cpp-python."
            )

        sdk_messages = [{"role": "system", "content": system}]
        sdk_messages += [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = self._client.create_chat_completion(
                messages=sdk_messages,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                top_p=self.config.top_p,
            )
            text  = response["choices"][0]["message"]["content"] or ""
            usage = response.get("usage", {})
            return LLMResponse(
                text=text,
                model=self.config.model or Path(self.config.model_path).name,
                provider="llamacpp",
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                raw=response,
            )
        except Exception as exc:
            raise LLMProviderError(
                f"LlamaCpp inference error: {exc}"
            ) from exc
