#!/bin/bash
export CUDA_VISIBLE_DEVICES=0,1
export LD_LIBRARY_PATH=/home/eduardopessin/nccl-cu13/nvidia/nccl/lib:/usr/local/cuda/lib64:$LD_LIBRARY_PATH
export GGML_CUDA_NO_PINNED=1
G=/home/eduardopessin/.cache/huggingface/hub/models--bartowski--Qwen_Qwen3.6-27B-GGUF/snapshots/4612927928b49982f8319dc3e2e6f62b9b73b192/Qwen_Qwen3.6-27B-Q8_0.gguf
cd /home/eduardopessin/llamacpp-tq
REP=/home/eduardopessin/p2p_profile
nsys profile --trace=cuda --sample=none --force-overwrite=true -o "$REP" \
  ./build/bin/llama-cli -m "$G" -sm row -c 4096 -ngl 99 -fa on -no-cnv -n 32 \
  -p "Escreva um ensaio sobre exploracao espacial:" 2>&1 | tail -8
echo "=== MEMCPY por tipo (PtoP=P2P direto! HtoD/DtoH=host-staged) ==="
nsys stats --report cuda_gpu_mem_time_sum "$REP".nsys-rep 2>/dev/null | sed -n '1,15p'
