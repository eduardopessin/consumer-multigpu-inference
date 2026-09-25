# P2P evidence

Raw command output, captured 2026-09-25T18:10:28+01:00 on `aisandbox`.

```
$ uname -r
5.15.0-190-generic

$ cat /proc/driver/nvidia/version
NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  595.91.07  Release Build  (eduardopessin@aisandbox)  Sat Sep 12 17:31:32 BST 2026
GCC version:  gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.3) 

$ nvidia-smi --query-gpu=index,name,driver_version,pcie.link.gen.current,pcie.link.width.current,pcie.link.gen.max,pcie.link.width.max --format=csv
index, name, driver_version, pcie.link.gen.current, pcie.link.width.current, pcie.link.gen.max, pcie.link.width.max
0, NVIDIA GeForce RTX 5060 Ti, 595.91.07, 1, 8, 3, 8
1, NVIDIA GeForce RTX 5060 Ti, 595.91.07, 1, 8, 3, 8
2, NVIDIA GeForce RTX 5060 Ti, 595.91.07, 1, 8, 3, 8
3, NVIDIA GeForce RTX 5060 Ti, 595.91.07, 1, 8, 3, 8

$ nvidia-smi topo -m
	[4mGPU0	GPU1	GPU2	GPU3	CPU Affinity	NUMA Affinity	GPU NUMA ID[0m
GPU0	 X 	PHB	PHB	PHB	0-7	0		N/A
GPU1	PHB	 X 	PHB	PHB	0-7	0		N/A
GPU2	PHB	PHB	 X 	PHB	0-7	0		N/A
GPU3	PHB	PHB	PHB	 X 	0-7	0		N/A


$ nvidia-smi topo -p2p rw
 	[4mGPU0	GPU1	GPU2	GPU3	[0m
 GPU0	X	OK	OK	OK	
 GPU1	OK	X	OK	OK	
 GPU2	OK	OK	X	OK	
 GPU3	OK	OK	OK	X	

Legend:

$ nvidia-smi -q | grep -A3 "BAR1 Memory Usage" | head -4
    BAR1 Memory Usage
        Total                                          : 16384 MiB
        Used                                           : 15729 MiB
        Free                                           : 655 MiB

$ grep EnableResizableBar /proc/driver/nvidia/params
EnableResizableBar: 1

$ ls -l /lib/modules/$(uname -r)/updates/dkms/nvidia.ko
-rw-r--r-- 1 root root 33191048 Sep 12 17:33 /lib/modules/5.15.0-190-generic/updates/dkms/nvidia.ko

$ ./check-nvidia-p2p
OK: P2P BAR1 activo nos 12 pares (4 GPUs), kernel 5.15.0-190-generic

$ python - <<EOF ... torch.cuda.can_device_access_peer ... EOF
devices=4
     GPU0   GPU1   GPU2   GPU3   
GPU0   X     OK    OK    OK  
GPU1   OK    X     OK    OK  
GPU2   OK    OK    X     OK  
GPU3   OK    OK    OK    X   
pairs with P2P: 12/12
```
