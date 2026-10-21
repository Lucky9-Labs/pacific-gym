@echo off
set "TEMP=D:\PacificGym\tmp"
set "TMP=D:\PacificGym\tmp"
set "OLLAMA_MODELS=D:\Ollama\models"
set "OLLAMA_HOST=127.0.0.1:11435"
cd /d D:\PacificGym
D:\PacificGym\tools\Ollama\ollama.exe %*
