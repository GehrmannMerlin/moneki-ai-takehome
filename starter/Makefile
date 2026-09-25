# 经营看板 + 问答服务
#
# 平台兼容：Windows 的虚拟环境可执行文件在 .venv/Scripts/，POSIX 在 .venv/bin/，
# 这里用 make 的条件判断自动选择，两边都能直接 make。
# 本机没有 make 也不影响：README 里有一张等价的直接命令表。
#
# 换数据或知识库：make rebuild DATA_DIR=/path/data KB_DIR=/path/kb

ifeq ($(OS),Windows_NT)
  PY := .venv/Scripts/python.exe
else
  PY := .venv/bin/python
endif

# 建虚拟环境用哪个解释器（这台机器上 python3 可能不存在）
PYTHON ?= python
PORT   ?= 8000
TOP    ?= ..

export DATA_DIR KB_DIR VAR_DIR LLM_BASE_URL LLM_API_KEY LLM_MODEL

.PHONY: help setup rebuild run test eval baseline regression swaptest clean

help:            ## 列出所有可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:           ## 建虚拟环境并装依赖
	$(PYTHON) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt

rebuild:         ## 从 DATA_DIR 与 KB_DIR 重建清洗表与检索索引
	$(PY) -m kbqa.rebuild

run:             ## 启动服务，默认 8000 端口
	$(PY) -m uvicorn kbqa.server:app --host 127.0.0.1 --port $(PORT)

test:            ## 跑测试
	$(PY) -m pytest tests -q

eval:            ## 跑公开题库（需要服务已在跑）
	cd $(TOP) && $(PYTHON) eval/run_eval.py --base-url http://localhost:$(PORT) \
		--questions eval/public_questions.jsonl

swaptest:        ## 换库自验：造变体数据/知识库，验证零写死
	$(PY) scripts/swap_check.py

baseline:        ## 跑公开题库并按 category 分解成 baseline_report.json
	$(PY) scripts/baseline_report.py --base-url http://localhost:$(PORT)

regression:      ## 评测即回归：pytest → 起服务 → 题库 → 与基线分类对比
	$(PY) scripts/regression.py $(if $(QUESTIONS),--questions $(QUESTIONS),)

clean:           ## 删掉清洗表与索引缓存
	$(PY) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ('var', '.cache')]"
