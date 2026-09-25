#!/usr/bin/env python3
"""Harness de medicao honesto para o vLLM do aisandbox.

Substitui o bench de 2026-08-18/27, cujos numeros de decode foram medidos com o
modelo a emitir "alpha alpha alpha ..." -- repeticao de um unico token, o melhor
caso possivel para speculative decoding. Esse artefacto reportou 97.08 tok/s e
mean acceptance length 4.96; o trafego real da 43 tok/s e 2.6.

Regras deste harness:
  - prompts que produzem texto/codigo genuino, nunca repeticao;
  - decode medido por delta dos contadores /metrics (tokens/step, acceptance,
    per-position) e nao so por wall clock do cliente;
  - TTFT frio medido com prefixo unico por corrida (senao mede-se prefix cache);
  - TTFT quente medido repetindo o MESMO prefixo, para isolar o residuo do
    recompute de fronteira mamba;
  - o servidor tem de estar idle: aborta se houver pedidos em curso.

Uso: python3 bench.py <tag>   ->  resultados_<tag>.json
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("VLLM_BASE", "http://127.0.0.1:8000")
MODEL = os.environ.get("VLLM_MODEL", "qwen38")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

COUNTERS = {
    "draft": "vllm:spec_decode_num_draft_tokens_total",
    "acc": "vllm:spec_decode_num_accepted_tokens_total",
    "gen": "vllm:generation_tokens_total",
    "prompt": "vllm:prompt_tokens_total",
    "itl_count": "vllm:inter_token_latency_seconds_count",
    "itl_sum": "vllm:inter_token_latency_seconds_sum",
    "ttft_count": "vllm:time_to_first_token_seconds_count",
    "ttft_sum": "vllm:time_to_first_token_seconds_sum",
    "pc_q": "vllm:prefix_cache_queries_total",
    "pc_h": "vllm:prefix_cache_hits_total",
    "preempt": "vllm:num_preemptions_total",
}


def scrape():
    txt = urllib.request.urlopen(f"{BASE}/metrics", timeout=30).read().decode()
    out = {}
    for key, name in COUNTERS.items():
        m = re.search(rf"^{re.escape(name)}\{{[^}}]*\}} (\S+)", txt, re.M)
        out[key] = float(m.group(1)) if m else 0.0
    for i in range(8):
        m = re.search(
            rf"per_pos_total\{{engine=\"0\",model_name=\"[^\"]+\",position=\"{i}\"\}} (\S+)",
            txt,
        )
        if m:
            out[f"pos{i}"] = float(m.group(1))
    # Soma de request_success_total sobre todos os finish_reason. Serve para
    # detectar pedidos ALHEIOS durante uma medicao: o LiteLLM (192.168.1.159)
    # continua a mandar chat/completions de outros agentes, e os contadores do
    # /metrics sao globais -- um pedido alheio inflaciona gen/steps/drafted e
    # falsifica o resultado. Se o delta nao casar com o numero de pedidos que o
    # harness fez, o cenario e repetido.
    out["succ"] = sum(
        float(v) for v in re.findall(r"^vllm:request_success_total\{[^}]*\} (\S+)", txt, re.M)
    )
    m = re.search(r"^vllm:num_requests_running\{[^}]*\} (\S+)", txt, re.M)
    out["running"] = float(m.group(1)) if m else 0.0
    return out

def _r(x, nd):
    """round() tolerante a None, para cenarios que falham parcialmente."""
    return None if x is None else round(x, nd)



def require_idle(label, wait_s=90):
    """Espera que o servidor fique idle. O dashboard (.156) e o LiteLLM (.159)
    fazem pollings e ocasionalmente mandam um pedido real, por isso esperar e
    melhor que abortar -- mas nunca medir com carga alheia em curso."""
    for _ in range(wait_s):
        s = scrape()
        if s["running"] == 0:
            return s
        time.sleep(1)
    raise SystemExit(f"ABORT: servidor nao ficou idle em {wait_s}s antes de {label}")


def chat(prompt, max_tokens, thinking=True, timeout=900):
    """Um pedido em streaming. Devolve (ttft, wall, n_chunks, texto)."""
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    ttft = None
    n = 0
    buf = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data: "):
                continue
            if line == "data: [DONE]":
                break
            d = json.loads(line[6:])
            ch = (d.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            # Este deployment usa `reasoning` (nao `reasoning_content`) para o
            # conteudo de raciocinio -- ver o parser qwen3 em producao.
            piece = (
                delta.get("content")
                or delta.get("reasoning")
                or delta.get("reasoning_content")
            )
            if piece:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                n += 1
                buf.append(piece)
    return ttft, time.perf_counter() - t0, n, "".join(buf)


def spec_stats(a, b, expected_reqs=None):
    """Metricas de spec decode a partir do delta dos contadores.

    `expected_reqs` = quantos pedidos este cenario fez. Se o servidor concluiu
    mais do que isso, houve trafego alheio a misturar-se nos contadores e a
    medicao nao presta (ver nota em scrape()).
    """
    steps = b["itl_count"] - a["itl_count"]
    gen = b["gen"] - a["gen"]
    drafted = b["draft"] - a["draft"]
    accepted = b["acc"] - a["acc"]
    itl = b["itl_sum"] - a["itl_sum"]
    out = {
        "engine_gen_tokens": round(gen),
        "engine_steps": round(steps),
        "tokens_per_step": round(gen / steps, 4) if steps else None,
        "itl_ms_per_step": round(1000 * itl / steps, 3) if steps else None,
        "decode_tok_s_from_metrics": round(gen / itl, 2) if itl else None,
        "drafted": round(drafted),
        "accepted": round(accepted),
        "acceptance_pct": round(100 * accepted / drafted, 2) if drafted else None,
        "preemptions": round(b["preempt"] - a["preempt"]),
    }
    if expected_reqs is not None:
        out["foreign_requests"] = round((b["succ"] - a["succ"]) - expected_reqs)
    rates = []
    for i in range(8):
        if f"pos{i}" in a:
            rates.append(round((b[f"pos{i}"] - a[f"pos{i}"]) / steps, 4) if steps else None)
    out["per_position_acceptance"] = rates
    pq, ph = b["pc_q"] - a["pc_q"], b["pc_h"] - a["pc_h"]
    out["prefix_cache_hit_pct"] = round(100 * ph / pq, 2) if pq else None
    return out


# ---------------------------------------------------------------- prompts

TEXT_PROMPT = (
    "Explica em detalhe, passo a passo e em portugues de Portugal, como funciona o "
    "algoritmo de speculative decoding com um draft model: o ciclo propose/verify, "
    "a matematica da rejection sampling e porque preserva exactamente a distribuicao "
    "do modelo alvo. Inclui um exemplo numerico com quatro tokens de draft. "
    "Escreve pelo menos 600 palavras, sem repetir frases."
)

CODE_PROMPT = (
    "Escreve uma implementacao completa em Python de uma cache LRU thread-safe com "
    "TTL por entrada, sem usar functools.lru_cache. Requisitos: O(1) em get e put, "
    "lista duplamente ligada mais dicionario, expiracao preguicosa e tambem um "
    "varredor em background, um lock reentrante, contadores de hits/misses/evictions "
    "e type hints completos. Depois dos ficheiros, explica as decisoes de design e os "
    "casos limite (relogio monotonico, reentrancia, eviction durante iteracao)."
)

AGENT_PROMPT = (
    "Es um agente de codigo a analisar um servidor de inferencia. Dado o excerto de "
    "log abaixo, diagnostica a causa provavel, indica que metricas confirmariam o "
    "diagnostico e propoe duas correccoes com o respectivo risco.\n\n"
    "LOG:\n"
    "SpecDecoding metrics: Mean acceptance length: 2.63, Accepted throughput: 34.70 "
    "tokens/s, Drafted throughput: 85.19 tokens/s, Per-position acceptance rate: "
    "0.723, 0.455, 0.291, 0.160\n"
    "Engine 000: Avg generation throughput: 53.9 tokens/s, Running: 1 reqs, "
    "GPU KV cache usage: 22.3%, Prefix cache hit rate: 84.9%\n"
    "cuda_communicator: Using ['PYNCCL'] all-reduce backends for group 'tp:0'\n"
)


def filler(n_words, seed_text):
    """Texto pseudo-aleatorio deterministico dado o seed_text."""
    import random

    rnd = random.Random(seed_text)
    vocab = (
        "servidor latencia kernel memoria contexto modelo tensor pipeline reposta "
        "grafico escalonador bloco atencao estado recorrente quantizacao amostra "
        "fronteira orcamento verificacao rascunho aceitacao throughput cache"
    ).split()
    return " ".join(rnd.choice(vocab) for _ in range(n_words))


# ---------------------------------------------------------------- cenarios


def scn_single(name, prompt, max_tokens, thinking=True):
    a = require_idle(name)
    ttft, wall, chunks, text = chat(prompt, max_tokens, thinking)
    b = scrape()
    r = {
        "name": name,
        "client_ttft_s": round(ttft, 4) if ttft else None,
        "client_wall_s": round(wall, 4),
        "client_chunks": chunks,
        "client_decode_tok_s": round(chunks / (wall - ttft), 2) if ttft and wall > ttft else None,
        "output_chars": len(text),
        "output_head": text[:120].replace("\n", " "),
        "distinct_word_ratio": round(
            len(set(text.split())) / max(len(text.split()), 1), 4
        ),
    }
    r.update(spec_stats(a, b, expected_reqs=1))
    return r


def scn_concurrent(name, prompt, max_tokens, n=4):
    a = require_idle(name)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [
            ex.submit(chat, f"[pedido {i}] " + prompt, max_tokens) for i in range(n)
        ]
        res = [f.result() for f in futs]
    wall = time.perf_counter() - t0
    b = scrape()
    r = {
        "name": name,
        "requests": n,
        "wall_s": round(wall, 4),
        "client_ttft_s_mean": _r(
            statistics.mean(t for t in (x[0] for x in res) if t is not None), 4
        ),
        "client_ttft_s_max": _r(max((x[0] for x in res if x[0] is not None), default=None), 4),
        "aggregate_chunks_per_s": round(sum(x[2] for x in res) / wall, 2),
    }
    r.update(spec_stats(a, b, expected_reqs=n))
    return r


def scn_prefill_and_warm(name, n_words, max_tokens=1):
    """TTFT frio (prefixo unico) e depois quente (mesmo prefixo)."""
    a = require_idle(name)
    unique = uuid.uuid4().hex
    body = filler(n_words, unique)
    prefix = f"Documento de referencia {unique} (nao resumas):\n{body}\n\n"
    q1 = prefix + "Responde apenas: OK"
    ttft_cold, wall_cold, _, _ = chat(q1, max_tokens)
    mid = scrape()
    time.sleep(1.0)
    ttft_warm, wall_warm, _, _ = chat(q1, max_tokens)
    time.sleep(1.0)
    ttft_warm2, _, _, _ = chat(prefix + "Responde apenas: OK outra vez", max_tokens)
    b = scrape()
    prompt_tokens = mid["prompt"] - a["prompt"]
    return {
        "name": name,
        "approx_prompt_tokens": round(prompt_tokens),
        "cold_ttft_s": _r(ttft_cold, 4),
        "warm_ttft_s": _r(ttft_warm, 4),
        "warm_ttft_s_new_tail": _r(ttft_warm2, 4),
        "cold_prefill_tok_s": _r(prompt_tokens / ttft_cold, 1) if ttft_cold else None,
        "warm_saving_pct": (
            _r(100 * (1 - ttft_warm / ttft_cold), 2) if ttft_cold and ttft_warm else None
        ),
        **{
            k: v
            for k, v in spec_stats(a, b, expected_reqs=3).items()
            if k in ("prefix_cache_hit_pct", "foreign_requests")
        },
    }


def scn_longctx(name, n_words, max_tokens):
    """Forma real do agente: prompt grande, saida moderada."""
    a = require_idle(name)
    unique = uuid.uuid4().hex
    body = filler(n_words, unique)
    prompt = (
        f"Contexto tecnico {unique}:\n{body}\n\n"
        "Ignora o ruido acima. Explica em portugues, em pelo menos 250 palavras, "
        "porque um prefix cache com estados recorrentes precisa de checkpoints "
        "internos durante o prefill."
    )
    ttft, wall, chunks, text = chat(prompt, max_tokens)
    b = scrape()
    r = {
        "name": name,
        "approx_prompt_tokens": round(b["prompt"] - a["prompt"]),
        "client_ttft_s": round(ttft, 4) if ttft else None,
        "client_wall_s": round(wall, 4),
        "client_decode_tok_s": round(chunks / (wall - ttft), 2) if ttft else None,
        "distinct_word_ratio": round(
            len(set(text.split())) / max(len(text.split()), 1), 4
        ),
    }
    r.update(spec_stats(a, b, expected_reqs=1))
    return r


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    started = time.time()
    env_snapshot = {
        k: v
        for k, v in os.environ.items()
        if k.startswith(("VLLM_", "QWEN38_", "NCCL_", "CUDA_", "PYTORCH_"))
    }
    print(f"== bench {tag} ==", flush=True)
    results = {}
    order = [
        ("warmup", lambda: scn_single("warmup", "Diz apenas: pronto.", 16)),
        ("single_decode_text", lambda: scn_single("single_decode_text", TEXT_PROMPT, 400)),
        ("single_decode_code", lambda: scn_single("single_decode_code", CODE_PROMPT, 400)),
        ("single_decode_agent", lambda: scn_single("single_decode_agent", AGENT_PROMPT, 300)),
        ("concurrent4_text", lambda: scn_concurrent("concurrent4_text", TEXT_PROMPT, 200, 4)),
        ("prefill_warm_8k", lambda: scn_prefill_and_warm("prefill_warm_8k", 6000)),
        ("prefill_warm_32k", lambda: scn_prefill_and_warm("prefill_warm_32k", 26000)),
        ("longctx_32k_decode", lambda: scn_longctx("longctx_32k_decode", 26000, 250)),
    ]
    for name, fn in order:
        t0 = time.perf_counter()
        # Ate 4 tentativas: o cenario so conta se nenhum pedido alheio entrou
        # nos contadores durante a medicao.
        for attempt in range(1, 5):
            try:
                res = fn()
            except Exception as e:  # um cenario falhado nao mata a corrida
                res = {"name": name, "error": repr(e)}
                print(f"  {name}: ERRO {e!r}", flush=True)
                break
            foreign = res.get("foreign_requests") or 0
            if foreign <= 0:
                break
            print(
                f"  {name}: descartado (tentativa {attempt}, "
                f"{foreign} pedido(s) alheio(s) na janela)",
                flush=True,
            )
            res["discarded_attempts"] = attempt
            time.sleep(6)
        results[name] = res
        if "error" not in res:
            shown = {
                k: v
                for k, v in res.items()
                if k in (
                    "tokens_per_step", "acceptance_pct", "itl_ms_per_step",
                    "decode_tok_s_from_metrics", "client_ttft_s", "cold_ttft_s",
                    "warm_ttft_s", "aggregate_chunks_per_s", "foreign_requests",
                )
            }
            print(f"  {name}: {round(time.perf_counter()-t0,1)}s {json.dumps(shown)}", flush=True)
        time.sleep(1.5)

    payload = {
        "tag": tag,
        "started_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "duration_s": round(time.time() - started, 1),
        "env": env_snapshot,
        "results": results,
    }
    path = os.path.join(OUT_DIR, f"resultados_{tag}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"escrito: {path}")


if __name__ == "__main__":
    main()
