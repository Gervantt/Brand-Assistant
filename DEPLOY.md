# Деплой Brand Assistant (всё бесплатно)

Пошаговая инструкция для первого деплоя. Займёт около 40–60 минут. Ничего не нужно знать заранее —
просто идите по шагам сверху вниз и отмечайте галочки.

## Что и где будет работать

| Компонент | Сервис | Тариф |
|---|---|---|
| Фронтенд (React) | **Vercel** | Hobby (бесплатно) |
| API + MCP-сервер (один Docker-контейнер) | **Render** | Free |
| PostgreSQL + pgvector | **Neon** | Free |
| Redis | **Upstash** | Free |
| Трейсинг LLM-вызовов | **Langfuse Cloud** | Hobby (бесплатно) |
| LLM | **Groq** | Free |

Почему API и MCP-сервер в одном контейнере: на бесплатном Render нельзя создать приватный сервис,
а у бесплатного инстанса всего 512 МБ памяти. Поэтому MCP-сервер встроен в процесс API
(`MCP_MODE=embedded`), но общается с ним по настоящему MCP-протоколу через `localhost`.
Подробнее — в [docs/decisions.md](docs/decisions.md).

Порядок важен: сначала получаем все ключи и адреса (шаги 1–5), потом деплоим бэкенд (шаг 6),
потом фронтенд (шаг 7), и в конце связываем их (шаг 8).

> **Правило безопасности.** Ключи и пароли вставляйте **только** в панели сервисов (Render, Vercel)
> или в локальный `.env`. Никогда не коммитьте их в git и не отправляйте в чаты.
> Если ключ случайно засветился — сразу отзовите его и создайте новый.

Заведите себе заметку (например, в менеджере паролей), куда будете складывать значения по ходу:

```
GROQ_API_KEY=
GEMINI_API_KEY=
DATABASE_URL=
REDIS_URL=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
DEMO_PASSWORD=
```

---

## Шаг 0. Код на GitHub

Render и Vercel деплоят прямо из репозитория.

1. Убедитесь, что код запушен: `git push origin main`.
2. Если пуш падает с ошибкой про `workflow` scope — ваш токен GitHub не может менять
   `.github/workflows`. Создайте новый токен: GitHub → Settings → Developer settings →
   Personal access tokens → *Tokens (classic)* → галочки **repo** и **workflow**.

- [ ] Код в репозитории на GitHub

---

## Шаг 1. Groq — ключ для LLM

1. Зайдите на https://console.groq.com и войдите через Google или GitHub.
2. Слева **API Keys** → **Create API Key** → назовите `brand-assistant` → **Submit**.
3. Скопируйте ключ (начинается с `gsk_`). **Он показывается один раз.** Сохраните в заметку как `GROQ_API_KEY`.
4. **Важно:** откройте https://console.groq.com/settings/limits и проверьте, что модели
   `openai/gpt-oss-120b`, `openai/gpt-oss-20b` и `qwen/qwen3.8-27b` **разрешены** для организации.
   Если они заблокированы, API будет отвечать `403 model_permission_blocked_org`.

Проверка (в терминале, подставьте свой ключ):

```bash
curl -s https://api.groq.com/openai/v1/chat/completions \
  -H "Authorization: Bearer gsk_ВАШ_КЛЮЧ" -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-oss-20b","messages":[{"role":"user","content":"привет"}]}' | head -c 300
```

Должен прийти JSON с `"choices"`, а не с `"error"`.

- [ ] `GROQ_API_KEY` сохранён, модели разрешены

---

## Шаг 2. Neon — PostgreSQL с pgvector

1. Зайдите на https://neon.tech → **Sign up** (удобно через GitHub).
2. **Create project**:
   - Name: `brand-assistant`
   - Postgres version: **16** или новее
   - Region: **AWS Europe Central (Frankfurt)** — тот же регион, что будет у Render, чтобы было быстрее.
3. После создания откроется **Connection details**:
   - Переключатель **Connection pooling** — **выключите** (нужна *прямая* строка подключения,
     в адресе **не должно быть** `-pooler`). Пулер PgBouncer плохо дружит с миграциями.
   - Скопируйте строку вида
     `postgresql://neondb_owner:...@ep-xxx-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require`
   - Сохраните как `DATABASE_URL`. Ничего в ней менять не нужно: приложение само переведёт
     `sslmode=require` в формат драйвера asyncpg.
4. **Включите pgvector.** Слева **SQL Editor** → вставьте и нажмите **Run**:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   SELECT extversion FROM pg_extension WHERE extname = 'vector';
   ```
   Должна вернуться версия (например, `0.8.0`). Миграции тоже выполняют эту команду, но лучше
   убедиться заранее, что расширение доступно.

- [ ] `DATABASE_URL` сохранён (без `-pooler`), pgvector включён

---

## Шаг 3. Upstash — Redis

1. Зайдите на https://upstash.com → **Sign up** → **Create Database** (раздел Redis).
2. Настройки:
   - Name: `brand-assistant`
   - Type: **Regional**, Region: **eu-central-1 (Frankfurt)**
   - TLS: **включён** (по умолчанию)
3. На странице базы найдите блок **Connect** → вкладка с URL. Скопируйте адрес вида
   `rediss://default:ПАРОЛЬ@xxx.upstash.io:6379` — обратите внимание на **две буквы `s`**
   в `rediss://` (это TLS). Сохраните как `REDIS_URL`.

- [ ] `REDIS_URL` сохранён (начинается с `rediss://`)

---

## Шаг 4. Langfuse Cloud — трейсинг (можно пропустить)

Без Langfuse всё работает, просто не будет трейсов. Рекомендую подключить — это красиво для портфолио.

1. https://cloud.langfuse.com → **Sign up**. Регион **EU**.
2. Создайте организацию и проект `brand-assistant`.
3. **Settings** проекта → **API Keys** → **Create new API keys**.
4. Сохраните **Public Key** (`pk-lf-...`) как `LANGFUSE_PUBLIC_KEY` и **Secret Key** (`sk-lf-...`) как
   `LANGFUSE_SECRET_KEY`. Host для EU — `https://cloud.langfuse.com` (уже прописан в `render.yaml`;
   если выбрали US-регион, позже поменяйте `LANGFUSE_HOST` на `https://us.cloud.langfuse.com`).

- [ ] Ключи Langfuse сохранены (или осознанно пропущено)

---

## Шаг 5. Gemini — эмбеддинги для поиска по брендбуку

Локально эмбеддинги считает модель fastembed, ключ не нужен. Но на бесплатном Render (512 МБ памяти)
эта мультиязычная модель не помещается: она одна занимает около 560 МБ (это измерено, см.
[docs/decisions.md](docs/decisions.md)). Поэтому в продакшене эмбеддинги считает бесплатный
Gemini API — так процесс занимает около 200 МБ.

1. Откройте https://aistudio.google.com/apikey (войдите Google-аккаунтом).
2. **Create API key** → выберите или создайте проект → скопируйте ключ (`AIza...`).
3. Сохраните как `GEMINI_API_KEY`.

---

## Шаг 6. Render — бэкенд

1. Зайдите на https://render.com → **Sign up with GitHub** и дайте доступ к репозиторию.
2. **New +** → **Blueprint** → выберите репозиторий `Brand-Assistant`. Render найдёт файл
   `render.yaml` и покажет сервис **brand-assistant-api** (Docker, план Free, регион Frankfurt).
3. Render попросит заполнить переменные с `sync: false`. Вставьте значения из заметки:

   | Переменная | Значение |
   |---|---|
   | `GROQ_API_KEY` | ключ из шага 1 |
   | `DATABASE_URL` | строка Neon из шага 2 |
   | `REDIS_URL` | `rediss://…` из шага 3 |
   | `DEMO_PASSWORD` | придумайте пароль для демо-аккаунтов, например 16 случайных символов |
   | `CORS_ORIGINS` | пока поставьте `http://localhost:5173` — исправим на шаге 8 |
   | `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | из шага 4 (или оставьте пустыми) |
   | `GEMINI_API_KEY` | ключ из шага 5 |

   `JWT_SECRET` и `MCP_INTERNAL_SECRET` Render сгенерирует сам (`generateValue: true`) — ничего
   вводить не нужно.
4. **Apply**. Первая сборка идёт 5–10 минут: ставятся зависимости Python. `EMBEDDING_PROVIDER`
   уже выставлен в `gemini` в `render.yaml`.
5. Следите за вкладкой **Logs**. Успешный старт выглядит так:
   - `Running upgrade … -> 0005` — миграции применены;
   - `"event": "seed_complete"` — созданы клиенты и демо-аккаунты;
   - `"event": "Uvicorn running on http://0.0.0.0:10000"`;
   - через 10–30 секунд `"event": "seed_documents_complete", "ingested": 4` — брендбуки проиндексированы.
6. Скопируйте адрес сервиса вверху страницы: `https://brand-assistant-api-xxxx.onrender.com`.
   Проверьте в браузере `https://…onrender.com/health` — должно быть
   `{"status":"ok", … "checks":{"database":"ok","redis":"ok"}}`.

- [ ] `/health` отвечает `ok`, адрес бэкенда сохранён

---

## Шаг 7. Vercel — фронтенд

1. https://vercel.com → **Sign up with GitHub**.
2. **Add New…** → **Project** → импортируйте `Brand-Assistant`.
3. Настройки проекта (это важно — фронтенд лежит в подпапке монорепо):
   - **Root Directory**: `apps/web` (кнопка **Edit** рядом с полем)
   - **Framework Preset**: Vite (подставится сам)
   - **Build Command**: `npm run build` · **Output Directory**: `dist` (уже в `apps/web/vercel.json`)
4. **Environment Variables** → добавьте:
   - `VITE_API_URL` = адрес бэкенда из шага 6, **без слеша в конце**,
     например `https://brand-assistant-api-xxxx.onrender.com`
5. **Deploy**. Через 1–2 минуты получите адрес вида `https://brand-assistant-xxxx.vercel.app`.

> `VITE_*`-переменные встраиваются в сборку. Если поменяете `VITE_API_URL` — нужен **Redeploy**.

- [ ] Фронтенд открывается, адрес сохранён

---

## Шаг 8. Связываем фронтенд и бэкенд (CORS)

Браузер разрешит фронтенду ходить в API, только если домен фронтенда указан в `CORS_ORIGINS`.

1. Render → сервис **brand-assistant-api** → **Environment**.
2. `CORS_ORIGINS` = адрес Vercel **без слеша в конце**, например
   `https://brand-assistant-xxxx.vercel.app`. Несколько адресов — через запятую
   (например, ещё и превью-домены Vercel или свой домен).
3. **Save Changes** — Render сам перезапустит сервис (1–2 минуты).

- [ ] `CORS_ORIGINS` указывает на домен Vercel

---

## Шаг 9. Чек-лист проверки после деплоя

Пройдите по порядку:

- [ ] **Health:** `https://<backend>.onrender.com/health` → `"status":"ok"`.
- [ ] **Логин:** откройте фронтенд, нажмите «Войти как менеджер». Если 30–50 секунд висит баннер
      «Сервер просыпается» — это нормально для бесплатного Render.
- [ ] **Вопрос по брендбуку:** «Какие фирменные цвета у бренда?» → ответ с цитатами `[1]`,
      бейджем уверенности и раскрывающимся списком «Источники».
- [ ] **Генерация плана:** «Составь контент-план на неделю» → статус «Составляю контент-план…»,
      затем таблица. Нажмите «Опубликовать» → план появится в «Утверждённые планы».
- [ ] **Права:** выйдите, войдите как наблюдатель — генерации нет, клиент только Bean There.
- [ ] **Трейс в Langfuse:** cloud.langfuse.com → проект → **Tracing** → трейс `agent.run`
      с вложенными `llm.agent` и `tool.search_brandbook`.
- [ ] **Метрики:** войдите как администратор → «Метрики» — есть вызовы, latency, стоимость.
- [ ] **Rate limit:** отправьте больше 20 сообщений за час одним пользователем → ответ
      «Лимит демо: 20 запросов к ассистенту в час. Попробуйте через N мин.»
      (для быстрой проверки можно временно поставить `RATE_LIMIT_PER_HOUR=2` в Render).

Можно прогнать автоматический смоук прямо на продакшене:

```bash
uv run --with httpx python scripts/smoke.py \
  --api https://<backend>.onrender.com --web https://<frontend>.vercel.app
```

---

## Частые проблемы

### CORS: в консоли браузера «blocked by CORS policy»
- В `CORS_ORIGINS` должен быть **точный** адрес фронтенда: со схемой `https://`, без слеша в
  конце, без пути. `https://app.vercel.app/` (со слешем) — **не** совпадёт.
- После изменения переменной Render должен перезапуститься (проверьте вкладку Events).
- Если открываете превью-деплой Vercel (`…-git-branch-….vercel.app`) — его адрес тоже нужно
  добавить через запятую.

### Ошибки SSL к Neon
- `ssl/sslmode` ошибки или `connection refused` — убедитесь, что скопирована строка из Neon
  целиком, вместе с `?sslmode=require`.
- `prepared statement … does not exist` — вы используете **pooled**-адрес (`-pooler` в хосте).
  Возьмите прямой адрес (Connection pooling выключен). Приложение умеет работать и с пулером,
  но миграциям нужен прямой.
- Neon «засыпает» после 5 минут простоя; первый запрос после этого — на 1–2 секунды дольше. Это нормально.

### Холодный старт Render
- Бесплатный инстанс засыпает после 15 минут без запросов. Первый запрос будит его 30–50 секунд —
  фронтенд показывает баннер «Сервер просыпается». Так и задумано.
- Перед показом проекта откройте `/health`, чтобы разбудить сервер заранее.

### Render: «Out of memory» / перезапуски
- Проверьте, что `EMBEDDING_PROVIDER=gemini`. С `fastembed` процесс занимает ~750 МБ и на бесплатном
  инстансе гарантированно падает. Локальную модель имеет смысл включать только на платном
  инстансе от 2 ГБ (Render Standard).
- Эмбеддинги Gemini и fastembed несовместимы между собой. После смены провайдера очистите индекс в
  Neon (SQL Editor: `DELETE FROM documents;`) и перезапустите сервис — брендбуки проиндексируются заново.

### Поиск по брендбуку «временно недоступен»
Проверьте `GEMINI_API_KEY` (в логах Render будет `brandbook_search_failed` с ошибкой 400/403). Пока
поиск не работает, генерация постов и планов продолжает работать по профилю бренда, а вопросы по
брендбуку получают честный ответ «поиск временно недоступен».

### Ошибки и лимиты Groq
- `403 model_permission_blocked_org` — модели запрещены в настройках организации (шаг 1, пункт 4).
- `429 Rate limit reached … tokens per minute` — бесплатный лимит 8 000 токенов в минуту на модель.
  Роутер сам ждёт и повторяет, а затем переключается на запасную модель Groq (у каждой модели свой
  лимит). Длинная генерация плана может занять до минуты.
- «Дневной лимит демо исчерпан» — сработал наш собственный бюджет `DAILY_TOKEN_BUDGET`
  (по умолчанию 250 000 токенов в сутки), он защищает бесплатную квоту Groq. Сбрасывается в 00:00 UTC.
  Можно поднять значение в Render.

### Сервис не стартует: «JWT_SECRET must be set…»
В prod приложение отказывается работать со слабыми секретами. Убедитесь, что `JWT_SECRET` и
`MCP_INTERNAL_SECRET` сгенерированы Render (`generateValue`) или заданы строками от 32 символов.

### В чате «Не настроен ни один LLM-провайдер»
Не задан `GROQ_API_KEY` в Render → Environment.

### Брендбуки не появились в «Документах»
Посмотрите логи Render: должно быть `seed_documents_complete`. Если там `seed_documents_failed` —
чаще всего это неверный `GEMINI_API_KEY` или `DATABASE_URL`. После исправления перезапустите
сервис (Manual Deploy → Restart): индексация запускается при старте.
