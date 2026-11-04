"""Seed dictionary for the first lessons.

The data lives in one place (``SEED_EXPRESSIONS``) used both by Alembic
migration 0003 and by the async :func:`seed_expressions` helper, so dev,
CI and production databases receive identical rows. Phonetic hints are
Russian approximations with the stressed vowel in capitals; they are
deliberately approximate and never claimed to reproduce English sounds
exactly.
"""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Expression
from app.domain.profile import Level, Topic


class ExpressionSeed(BaseModel):
    """One validated dictionary entry of the seed set."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field(pattern=r"^[a-z]{2,8}$")
    text: str = Field(min_length=1, max_length=128)
    translation: str = Field(min_length=1, max_length=256)
    ipa: str = Field(min_length=1, max_length=64)
    phonetic_hint_ru: str = Field(min_length=1, max_length=128)
    example: str = Field(min_length=1)
    example_translation: str = Field(min_length=1)
    level: Level
    topic: Topic


SEED_EXPRESSIONS: tuple[ExpressionSeed, ...] = (
    ExpressionSeed(
        language="en",
        text="Good morning!",
        translation="Доброе утро!",
        ipa="ɡʊd ˈmɔːrnɪŋ",
        phonetic_hint_ru="гуд мОрнинг",
        example="Good morning! Did you sleep well?",
        example_translation="Доброе утро! Ты хорошо спал?",
        level=Level.A1,
        topic=Topic.EVERYDAY,
    ),
    ExpressionSeed(
        language="en",
        text="How are you?",
        translation="Как дела?",
        ipa="haʊ ɑːr juː",
        phonetic_hint_ru="хАу а ю",
        example="Hi Tom, how are you today?",
        example_translation="Привет, Том, как ты сегодня?",
        level=Level.A1,
        topic=Topic.EVERYDAY,
    ),
    ExpressionSeed(
        language="en",
        text="Thank you very much",
        translation="Большое спасибо",
        ipa="θæŋk juː ˈveri mʌtʃ",
        phonetic_hint_ru="сЭнк ю вЭри мач",
        example="Thank you very much for your help.",
        example_translation="Большое спасибо за помощь.",
        level=Level.A1,
        topic=Topic.EVERYDAY,
    ),
    ExpressionSeed(
        language="en",
        text="See you later",
        translation="До встречи",
        ipa="siː juː ˈleɪtər",
        phonetic_hint_ru="си ю лЭйтэ",
        example="I have to go now. See you later!",
        example_translation="Мне пора. До встречи!",
        level=Level.A1,
        topic=Topic.EVERYDAY,
    ),
    ExpressionSeed(
        language="en",
        text="It's up to you",
        translation="Как решишь; решать тебе",
        ipa="ɪts ʌp tuː juː",
        phonetic_hint_ru="итс Ап ту ю",
        example="Pizza or sushi? It's up to you.",
        example_translation="Пицца или суши? Решать тебе.",
        level=Level.A2,
        topic=Topic.EVERYDAY,
    ),
    ExpressionSeed(
        language="en",
        text="Could you repeat that, please?",
        translation="Повторите, пожалуйста",
        ipa="kʊd juː rɪˈpiːt ðæt pliːz",
        phonetic_hint_ru="куд ю рипИт зэт плиз",
        example="Could you repeat that, please? I didn't catch it.",
        example_translation="Повторите, пожалуйста? Я не расслышал.",
        level=Level.A2,
        topic=Topic.TRAVEL,
    ),
    ExpressionSeed(
        language="en",
        text="How much does it cost?",
        translation="Сколько это стоит?",
        ipa="haʊ mʌtʃ dʌz ɪt kɒst",
        phonetic_hint_ru="хАу мач даз ит кост",
        example="This bag looks nice. How much does it cost?",
        example_translation="Эта сумка выглядит мило. Сколько она стоит?",
        level=Level.A2,
        topic=Topic.TRAVEL,
    ),
    ExpressionSeed(
        language="en",
        text="Where is the train station?",
        translation="Где железнодорожный вокзал?",
        ipa="wɛər ɪz ðə treɪn ˈsteɪʃən",
        phonetic_hint_ru="уЭр из зэ трейн стЭйшэн",
        example="Excuse me, where is the train station?",
        example_translation="Извините, где железнодорожный вокзал?",
        level=Level.A2,
        topic=Topic.TRAVEL,
    ),
    ExpressionSeed(
        language="en",
        text="I'm working on it",
        translation="Я работаю над этим",
        ipa="aɪm ˈwɜːrkɪŋ ɒn ɪt",
        phonetic_hint_ru="аим вЁркин он ит",
        example="Don't worry about the report — I'm working on it.",
        example_translation="Не беспокойся об отчёте — я работаю над этим.",
        level=Level.A2,
        topic=Topic.WORK,
    ),
    ExpressionSeed(
        language="en",
        text="Let's schedule a meeting",
        translation="Давай назначим встречу",
        ipa="lɛts ˈskɛdʒuːl ə ˈmiːtɪŋ",
        phonetic_hint_ru="лэтс скЭджул э мИтинг",
        example="Let's schedule a meeting to discuss the plan.",
        example_translation="Давай назначим встречу, чтобы обсудить план.",
        level=Level.A2,
        topic=Topic.WORK,
    ),
    ExpressionSeed(
        language="en",
        text="Keep me posted",
        translation="Держи меня в курсе",
        ipa="kiːp miː ˈpoʊstɪd",
        phonetic_hint_ru="кип ми пОустид",
        example="Keep me posted about the results.",
        example_translation="Держи меня в курсе результатов.",
        level=Level.B1,
        topic=Topic.WORK,
    ),
    ExpressionSeed(
        language="en",
        text="It's over my head",
        translation="Это выше моего понимания",
        ipa="ɪts ˈoʊvər maɪ hɛd",
        phonetic_hint_ru="итс Оувэ май хэд",
        example="I read the documentation, but it's over my head.",
        example_translation="Я прочитал документацию, но это выше моего понимания.",
        level=Level.B1,
        topic=Topic.TECHNOLOGY,
    ),
    ExpressionSeed(
        language="en",
        text="Back to square one",
        translation="Вернуться к началу; с нуля",
        ipa="bæk tuː skwɛər wʌn",
        phonetic_hint_ru="бэк ту сквЭр уан",
        example="The fix didn't work, so we're back to square one.",
        example_translation="Исправление не сработало, так что всё сначала.",
        level=Level.B1,
        topic=Topic.TECHNOLOGY,
    ),
    ExpressionSeed(
        language="en",
        text="I couldn't put it down",
        translation="Не мог оторваться (о книге, фильме)",
        ipa="aɪ ˈkʊdənt pʊt ɪt daʊn",
        phonetic_hint_ru="ай кУдант пут ит дАун",
        example="The book was gripping — I couldn't put it down.",
        example_translation="Книга захватывающая — я не мог оторваться.",
        level=Level.B1,
        topic=Topic.MOVIES,
    ),
    ExpressionSeed(
        language="en",
        text="on the same page",
        translation="Одинаково понимать ситуацию",
        ipa="ɒn ðə seɪm peɪdʒ",
        phonetic_hint_ru="он зэ сэйм пЭйдж",
        example="Let's make sure we're all on the same page before we start.",
        example_translation="Давайте убедимся, что все всё понимают одинаково.",
        level=Level.B2,
        topic=Topic.WORK,
    ),
    ExpressionSeed(
        language="en",
        text="get the hang of it",
        translation="Приноровиться, понять, как делается",
        ipa="ɡɛt ðə hæŋ ɒv ɪt",
        phonetic_hint_ru="гэт зэ хЭнг ов ит",
        example="It's tricky at first, but you'll get the hang of it.",
        example_translation="Сначала непросто, но ты приноровишься.",
        level=Level.B2,
        topic=Topic.EVERYDAY,
    ),
)


async def seed_expressions(session: AsyncSession) -> int:
    """Insert missing seed rows; existing ones are left untouched.

    Idempotent thanks to the unique (language, text) constraint with
    ``ON CONFLICT DO NOTHING``. Returns the number of inserted rows.
    """
    if not SEED_EXPRESSIONS:
        return 0
    rows = [entry.model_dump(mode="json") for entry in SEED_EXPRESSIONS]
    statement = pg_insert(Expression).values(rows)
    statement = statement.on_conflict_do_nothing(index_elements=["language", "text"])
    result = await session.execute(statement)
    return int(getattr(result, "rowcount", 0) or 0)
