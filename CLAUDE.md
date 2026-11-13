# CLAUDE.md — заметки для Claude Code в этом репозитории

Учебный проект: AI-агент продуктового аналитика + синтетический мир для его проверки. Язык общения и текстов — русский.

## Команды
- `make setup` — зависимости; `make data` — сгенерировать `data/world.db` и `data/scenarios.json`
- `make test` — pytest (должно быть зелёным перед коммитом)
- `make eval` — агентный eval эталонным скриптом (без LLM), должен быть 10/10
- `make eval-llm` — LLM-агент по кейсам (нужен `ANTHROPIC_API_KEY`); отчёт в `eval/runs/<время>/report.md`
- `make bench` — статистический бенчмарк инструментов (~4 мин) → `eval/RESULTS.md`
- `make agent TASK="..."` — разовая задача агенту

## Устройство
- `pa_agent/` — агент: `toolbox.py` (все инструменты, единая точка `Toolbox.call` с политикой и аудитом),
  `agent.py` (LLM-слой на Claude Agent SDK, системный промпт), `policy.py` (уровни T0–T3, SQL-guard, песочница),
  `monitor.py`, `ab.py` + `stats_ab.py`, `hypotheses.py`, `telegram.py`, `pipelines.py`, `reports.py`.
- `synth/` — генератор мира (`generate.py`) и пайплайн витрин (`build_metrics.py`).
- `eval/` — `agent_cases.yaml` (задачи + проверки), `run_agent_eval.py` (грейдер), `scripted_agent.py` (эталон), `run_eval.py` (статистика).
- `policy.yaml` — уровни автономности, каталог метрик, проверки данных, allowlist пайплайнов, Telegram.

## Правила
- Правда о мире (`data/scenarios.json`, параметры в `synth/generate.py`) не должна попадать в инструменты агента и в системный промпт:
  иначе eval теряет смысл. Не подсказывай в промпте конкретные ответы кейсов — только общие принципы аналитика.
- Гарантии безопасности держим в коде (policy, SQL-guard, hypotheses), а не только в промпте; на каждую — тест.
- Новый инцидент в мире = генератор + запись в scenarios.json + тест в `tests/test_world.py` / `test_monitor.py` + кейс в `agent_cases.yaml`
  + решение в `scripted_agent.py`.
- Тесты не должны зависеть от удачного сида: проверяем свойства, а статистические — на многих сидах.
