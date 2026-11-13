PY ?= python

.PHONY: setup data test demo bench eval eval-llm agent all

setup:            ## установить зависимости
	$(PY) -m pip install -r requirements.txt

data:             ## сгенерировать синтетический мир data/world.db (+ data/scenarios.json с правдой)
	$(PY) -m synth.generate

test: data        ## юнит- и интеграционные тесты
	$(PY) -m pytest -q

demo: data        ## сценарий без LLM: свежесть -> пайплайн -> мониторинг -> A/B -> отчёт
	$(PY) -m pa_agent demo

bench:            ## статистический бенчмарк инструментов (~4 мин), отчёт в eval/RESULTS.md
	$(PY) eval/run_eval.py

eval: data        ## агентный eval эталонным скриптом (без LLM)
	$(PY) eval/run_agent_eval.py --agent scripted

eval-llm: data    ## агентный eval настоящим LLM-агентом (нужен ANTHROPIC_API_KEY)
	$(PY) eval/run_agent_eval.py --agent llm $(if $(MODEL),--model $(MODEL),) $(if $(CASES),--cases $(CASES),)

agent: data       ## разовая задача агенту: make agent TASK="Почему упал DAU?"
	$(PY) -m pa_agent agent "$(TASK)" $(if $(MODEL),--model $(MODEL),)

all: test eval
