"""Mede ITL/step e L com gate de contaminacao.

Regra (harness de 12/Set): contar request_success_total antes e depois.
Se entraram pedidos alheios, a amostra e descartada e repetida -- caso
contrario mede-se fila do LiteLLM em vez de velocidade do modelo.
"""

import json, subprocess, time

BASE = "localhost:8000"


def metrics():
    r = subprocess.run(["curl", "-s", "-m", "20", f"{BASE}/metrics"],
                       capture_output=True, text=True).stdout
    out = {"success": 0.0, "running": 0.0, "waiting": 0.0,
           "gen_tokens": 0.0, "iter": 0.0}
    for line in r.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("vllm:request_success_total"):
            out["success"] += float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:num_requests_running"):
            out["running"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:num_requests_waiting{"):
            out["waiting"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:generation_tokens_total"):
            out["gen_tokens"] += float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:iteration_tokens_total_count"):
            out["iter"] += float(line.rsplit(" ", 1)[1])
    return out


def wait_idle(timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = metrics()
        if m["running"] == 0 and m["waiting"] == 0:
            return True
        time.sleep(5)
    return False


def one_run(prompt, max_tokens=700):
    if not wait_idle():
        return None, "servidor nunca ficou idle"
    before = metrics()
    payload = {"model": "qwen38",
               "messages": [{"role": "user", "content": prompt}],
               "max_tokens": max_tokens, "temperature": 0.0}
    t0 = time.time()
    r = subprocess.run(["curl", "-s", "-m", "300", f"{BASE}/v1/chat/completions",
                        "-H", "Content-Type: application/json",
                        "-d", json.dumps(payload)],
                       capture_output=True, text=True)
    wall = time.time() - t0
    after = metrics()

    # Gate: exactamente 1 pedido concluido = so o nosso.
    delta_success = after["success"] - before["success"]
    if delta_success != 1:
        return None, f"contaminado (delta_success={delta_success:.0f})"

    d = json.loads(r.stdout)
    tok = d["usage"]["completion_tokens"]
    steps = after["iter"] - before["iter"]
    L = tok / steps if steps else float("nan")
    return {"wall": wall, "tokens": tok, "tok_s": tok / wall,
            "steps": steps, "L": L,
            "itl_ms": wall / steps * 1000 if steps else float("nan")}, None


PROMPT = ("Escreve uma funcao Python que implementa uma cache LRU com "
          "dicionario e lista ligada dupla. Inclui docstring.")

print("run   wall_s  tokens  tok/s   steps      L   ITL/step_ms")
ok = 0
for attempt in range(8):
    res, err = one_run(PROMPT)
    if err:
        print(f"  -- descartado: {err}")
        time.sleep(10)
        continue
    ok += 1
    print(f"{ok}     {res['wall']:6.1f}  {res['tokens']:6d}  "
          f"{res['tok_s']:5.1f}  {res['steps']:6.0f}  {res['L']:5.2f}  "
          f"{res['itl_ms']:8.1f}")
    if ok == 3:
        break
