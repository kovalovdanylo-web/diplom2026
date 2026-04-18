# Receipt Bot — Telegram-бот для обліку фіскальних чеків

Автоматичне розпізнавання та облік українських фіскальних чеків для ФОП через Telegram.

## Можливості

- 📸 Розпізнавання чеків з фото через Claude Vision AI
- 📊 Статистика витрат (загальна, помісячна)
- 📤 Експорт чеків у CSV для Excel
- 🔍 Підтримка кількох чеків на одному фото
- 🇺🇦 Підтримка всіх популярних форматів українських чеків

## Технології

- Python 3.11+, aiogram 3.7
- Claude Vision API (Anthropic) для розпізнавання
- SQLite для зберігання даних

## Швидкий старт

### 1. Отримання токенів

**Telegram Bot Token:**
- Відкрийте [@BotFather](https://t.me/BotFather) в Telegram
- Напишіть `/newbot`, вкажіть назву та username
- Скопіюйте токен

**Anthropic API Key:**
- Зареєструйтесь на [console.anthropic.com](https://console.anthropic.com)
- API Keys → Create Key
- Поповніть баланс мінімум $5

### 2. Встановлення

```bash
cd receipt_bot

# Віртуальне середовище (рекомендовано)
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Залежності
pip install -r requirements.txt
```

### 3. Налаштування

Скопіюйте `.env.example` в `.env` та вкажіть свої токени:

```bash
cp .env.example .env
# Відредагуйте .env — вкажіть BOT_TOKEN та ANTHROPIC_API_KEY
```

### 4. Запуск

```bash
python main.py
```

Або через скрипт:
```bash
./start.sh      # Linux/Mac
start.bat        # Windows
```

## Команди бота

| Команда   | Опис                                    |
|-----------|-----------------------------------------|
| `/start`  | Привітання та реєстрація                |
| `/help`   | Довідка по використанню                 |
| `/total`  | Загальна статистика                     |
| `/month`  | Статистика за поточний місяць           |
| `/last`   | Останні 5 чеків                         |
| `/export` | Завантажити CSV з усіма чеками          |
| 📸 Фото   | Розпізнати чек(и) та зберегти           |

## Тестування розпізнавання

```bash
python test_scanner.py test_images/example.jpg
```

## Структура проєкту

```
receipt_bot/
├── main.py                 # Точка входу
├── config.py               # Конфігурація
├── bot/
│   ├── __init__.py
│   └── handlers/
│       ├── commands.py     # Обробка команд
│       └── photo.py        # Обробка фото
├── database/
│   └── db.py               # Робота з БД
└── utils/
    └── claude_scanner.py   # Розпізнавання через Claude Vision
```

## Вартість

| Модель         | Ціна за чек |
|----------------|-------------|
| Claude Haiku   | ~$0.002     |
| Claude Sonnet  | ~$0.006     |
| Claude Opus    | ~$0.010     |

$5 кредитів ≈ 2500 чеків на Haiku.
