@echo off
set "TEMP=D:\PacificGym\tmp"
set "TMP=D:\PacificGym\tmp"
set "CUDA_CACHE_PATH=D:\PacificGym\cache\cuda"
set "WARP_CACHE_PATH=D:\PacificGym\cache\warp"
set "PYTHONPYCACHEPREFIX=D:\PacificGym\cache\python"
cd /d D:\PacificGym
call D:\IsaacSim\python.bat %*
