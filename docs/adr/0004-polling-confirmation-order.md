# ADR 0004: Порядок подтверждения updates (offset) при long polling

* **Статус:** принято
* **Дата:** 2026-11-16
* **Основание:** ТЗ §7

## Контекст

Telegram считает update доставленным, когда следующий `getUpdates` приходит с
`offset = update_id + 1`. Требование ТЗ: подтверждать update только после надёжного
сохранения в БД, иначе при аварии сообщение потеряется безвозвратно.

## Что установлено (по исходникам aiogram 3.32.0)

`Dispatcher._listen_updates` (`.venv/Lib/site-packages/aiogram/dispatcher/dispatcher.py:252-258`):

```python
for update in updates:
    yield update
    get_updates.offset = update.update_id + 1
```

`Dispatcher._polling` (там же, строки 424-445) по умолчанию вызывает
`_process_update` **через `asyncio.create_task`** (`handle_as_tasks=True`), то есть
продолжает итерацию немедленно. Следовательно, offset сдвигается в момент **постановки
задачи**, а не завершения обработки: подтверждение опережает сохранение.

Формально `handle_as_tasks=False` меняет поведение (offset сдвигается после `await`),
но это недокументированная деталь генератора, зависящая от версии библиотеки.

## Решение

1. Использовать `aiogram` как клиент Bot API (типы, `getFile`, скачивание), но **не**
   `Dispatcher.start_polling`.
2. Собственный цикл:

```text
offset = last_update_id + 1
updates = bot.get_updates(offset=offset, timeout=50, allowed_updates=[...])
для каждого update в порядке возрастания update_id:
    BEGIN IMMEDIATE
        INSERT deliveries(update_id …) ON CONFLICT DO NOTHING
        INSERT raw_messages / message_versions
        INSERT jobs(...)
    COMMIT
    last_update_id = update.update_id          # только после COMMIT
    сохранить last_update_id в meta (в той же БД)
```

3. `allowed_updates` — явный список типов; остальные игнорируются и логируются.
4. Команды, служебные сообщения и сообщения самого бота классифицируются отдельно (ТЗ §7).
5. Проверка webhook — `getWebhookInfo` при старте; webhook не удаляется автоматически.

## Последствия

* Гарантия: подтверждённый update уже лежит в БД (SQLite WAL, commit).
* Возможны повторные доставки после аварии между COMMIT и следующим `getUpdates` —
  их отсекает `UNIQUE(source, update_id)`.
* Потеря возможна только в окне «update получен, но не закоммичен» — это неизбежно
  и компенсируется повторной доставкой Telegram.

## Проверка

* Код aiogram изучен (см. ссылки выше); тест на реальном токене **не выполнен**:
  `api.telegram.org` недоступен с машины разработки (TCP-таймаут).
* На Этапе 3 добавляется unit-тест цикла с поддельным `get_updates`, проверяющий,
  что коммит предшествует сдвигу offset.
