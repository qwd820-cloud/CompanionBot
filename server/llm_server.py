"""轻量 LLM 推理服务器 — OpenAI 兼容 API

基于 HuggingFace Transformers 原生推理，提供 /v1/chat/completions 端点。
适配 DGX Spark Blackwell GB10（SGLang/vLLM 尚不支持 SM121）。

用法:
    python -m server.llm_server --model Qwen/Qwen3.5-27B --port 8000
"""

import argparse
import json
import logging
import time
import uuid
from threading import Thread

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_server")

# Lazy import uvicorn/fastapi (NGC 容器已有)

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="CompanionBot LLM Server")

# Global model/tokenizer
_model = None
_tokenizer = None
_model_name = ""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: int = 1024
    stream: bool = False


def load_model(model_path: str):
    global _model, _tokenizer, _model_name
    logger.info(f"加载模型: {model_path}")
    start = time.time()

    _tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    _model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    _model_name = model_path

    elapsed = time.time() - start
    logger.info(f"模型加载完成: {elapsed:.1f}s, device={_model.device}")


@app.get("/v1/models")
async def list_models():
    return {"data": [{"id": _model_name, "object": "model"}]}


@app.get("/health")
async def health():
    if _model is None:
        return JSONResponse({"status": "loading"}, status_code=503)
    return {"status": "ok", "model": _model_name}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    if _model is None:
        return JSONResponse({"error": "Model not loaded"}, status_code=503)

    # 构建 prompt
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    text = _tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = _tokenizer(text, return_tensors="pt").to(_model.device)

    if req.stream:
        return StreamingResponse(
            _stream_generate(inputs, req),
            media_type="text/event-stream",
        )

    # 非流式生成
    with torch.no_grad():
        outputs = _model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            temperature=max(req.temperature, 0.01),
            do_sample=req.temperature > 0,
            top_p=0.9,
        )

    new_tokens = outputs[0][inputs["input_ids"].shape[-1] :]
    response_text = _tokenizer.decode(new_tokens, skip_special_tokens=True)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": inputs["input_ids"].shape[-1],
            "completion_tokens": len(new_tokens),
            "total_tokens": inputs["input_ids"].shape[-1] + len(new_tokens),
        },
    }


async def _stream_generate(inputs, req: ChatRequest):
    streamer = TextIteratorStreamer(
        _tokenizer, skip_prompt=True, skip_special_tokens=True
    )
    gen_kwargs = dict(
        **inputs,
        max_new_tokens=req.max_tokens,
        temperature=max(req.temperature, 0.01),
        do_sample=req.temperature > 0,
        top_p=0.9,
        streamer=streamer,
    )

    thread = Thread(target=lambda: _model.generate(**gen_kwargs))
    thread.start()

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    for text_chunk in streamer:
        if not text_chunk:
            continue
        chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": _model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": text_chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    # 结束标记
    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
    yield "data: [DONE]\n\n"
    thread.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen3.5-27B")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    load_model(args.model)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
