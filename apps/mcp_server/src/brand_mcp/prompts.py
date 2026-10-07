"""Generation prompts (Russian content). Kept deterministic: MCP sampling requests must render
identically across protocol retry rounds."""

from datetime import date

COMMON_RULES = (
    "Ты — старший SMM-специалист креативного агентства. Пиши по-русски, строго в тоне голоса "
    "бренда. Используй только факты из профиля и фрагментов брендбука; не выдумывай акции, цены, "
    "адреса и обещания. Никогда не затрагивай запрещённые темы бренда."
)


def content_plan(
    brand: str,
    *,
    start: date,
    end: date,
    platforms: list[str],
    posts_per_week: int,
    goal: str,
) -> tuple[str, str]:
    system = COMMON_RULES + " Ты составляешь контент-планы: разнообразные рубрики, без повторов."
    user = (
        f"{brand}\n\n"
        f"Составь контент-план на период с {start.isoformat()} по {end.isoformat()} "
        f"(даты публикаций строго внутри периода).\n"
        f"Площадки: {', '.join(platforms)}.\n"
        f"Частота: примерно {posts_per_week} публикаций в неделю на весь бренд.\n"
        f"Цель периода: {goal or 'вовлечение и узнаваемость бренда'}.\n"
        "Для каждой публикации: дата, площадка, формат (post, carousel, reels, stories, video, "
        "article), рубрика из рубрик бренда, заголовок, черновик текста до 600 символов, "
        "3–6 хэштегов (включая фирменные), идея визуала."
    )
    return system, user


def post(brand: str, *, topic: str, platform: str, fmt: str, key_message: str) -> tuple[str, str]:
    system = COMMON_RULES + " Ты пишешь готовые к публикации тексты."
    user = (
        f"{brand}\n\n"
        f"Напиши {fmt} для {platform} на тему: {topic}.\n"
        f"Ключевое сообщение: {key_message or 'на твоё усмотрение в рамках темы'}.\n"
        "Учти ограничения площадки по длине и стилю. Добавь призыв к действию, хэштеги и идею "
        "визуала."
    )
    return system, user


def designer_brief(
    brand: str, *, post_text: str, platform: str, fmt: str, notes: str
) -> tuple[str, str]:
    system = COMMON_RULES + " Ты готовишь точные брифы для дизайнеров."
    user = (
        f"{brand}\n\n"
        f"Подготовь бриф дизайнеру для публикации ({fmt}, {platform}).\n"
        f"Текст/идея публикации:\n{post_text}\n\n"
        f"Дополнительно: {notes or 'нет'}.\n"
        "Укажи размеры под площадку, цель, ключевое сообщение, текст на визуале, описание "
        "визуала, настроение, цвета из айдентики бренда, что делать и чего избегать, "
        "список файлов на выходе."
    )
    return system, user


def repair(error: str) -> str:
    return (
        "Ответ не прошёл проверку схемы:\n"
        f"{error[:1500]}\n"
        "Верни исправленный JSON-объект целиком, без пояснений."
    )
