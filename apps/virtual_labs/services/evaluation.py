"""AI Evaluation Engine (ТЗ, п. 23, 27): автоматическая экспертиза учебного материала по 10 критериям.
Формат результата: критерий → балл → комментарий → рекомендация.
Алгоритм совпадает с браузерной версией (js/core/evaluation.js); совпадение проверяется тестом test_parity."""
import math
import re

from .text_utils import js_fixed1, js_round, sentences, stems, to_str, words

I = re.IGNORECASE
GENERIC_OUTCOME = {"знать", "уметь", "владет", "понима", "облада", "должны", "смогут", "будут", "обучаю", "студен", "умение", "навыки", "навык"}

HEAD_RE = re.compile(r"^\s*#{1,4}\s+(.+)$")
BOLD_HEAD_RE = re.compile(r"^\s*\*\*([^*]{3,80})\*\*\s*:?\s*$")
DEFS_RE = re.compile(r"\s[—–-]\s?(это|является)\s|называ[а-яёa-z]+|понима[а-яёa-z]+ под|определя[а-яёa-z]+ как|\*\*[^*]+\*\*\s*[—–-]", I)
ABSOLUTES_RE = re.compile(r"(?<![а-яёa-z])(всегда|никогда|абсолютно|доказано|все без исключения|единственно верн[а-яё]+|гарантирует)(?![а-яёa-z])", I)
STATS_RE = re.compile(r"\d+[.,]?\d*\s?%")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s)]+")
URL_RE = re.compile(r"https?://[^\s)]+")
CITE_RE = re.compile(r"\(([А-ЯЁA-Z][а-яёa-z]+(\s(et al\.|и др\.))?,?\s(19|20)\d{2})\)|[А-ЯЁ][а-яё]+\s[А-ЯЁ]\.\s?[А-ЯЁ]\.|[A-Z][a-z]+,\s[A-Z]\.\s")


def split_sections(text: str) -> list[dict]:
    out, cur = [], {"head": "", "body": []}
    for line in to_str(text).split("\n"):
        m = HEAD_RE.match(line) or BOLD_HEAD_RE.match(line)
        if m:
            out.append(cur)
            cur = {"head": m.group(1).lower(), "body": []}
        else:
            cur["body"].append(line)
    out.append(cur)
    return [{"head": s["head"], "body": "\n".join(s["body"]), "words": len(words(" ".join(s["body"])))} for s in out]


def check_sections(mod: dict, text: str) -> list[dict]:
    secs = split_sections(text)
    low = to_str(text).lower()
    result = []
    for s in mod.get("sections", []):
        keys = s.get("keys") or [s["title"].lower()[:6]]
        hit = next((x for x in secs if x["head"] and any(k in x["head"] for k in keys)), None)
        if hit and hit["words"] >= 12:
            status, value = "ok", 1
        elif hit:
            status, value = "thin", 0.6
        elif any(k in low for k in keys):
            status, value = "mention", 0.4
        else:
            status, value = "missing", 0
        result.append({"title": s["title"], "status": status, "value": value, "words": hit["words"] if hit else 0})
    return result


def apply_config(crit: list, config: list) -> tuple[list, int]:
    """Настройки администратора: название, вес и включение критерия. При весах 1 итог совпадает с исходным."""
    by_key = {c["key"]: c for c in config}
    out, weighted, weights = [], 0.0, 0.0
    for c in crit:
        cfg = by_key.get(c["key"])
        if cfg and (cfg.get("is_active") is False or cfg.get("isActive") is False):
            continue
        weight = float(cfg.get("weight", 1)) if cfg else 1.0
        if cfg and cfg.get("name"):
            c = {**c, "name": cfg["name"]}
        if weight != 1:
            c = {**c, "weight": weight}
        out.append(c)
        weighted += c["score"] * weight
        weights += weight
    total = js_round(weighted / weights * 10) if weights else 0
    return out, total


def clamp(x: float) -> float:
    v = max(0, min(10, math.floor(x * 10 + 0.5) / 10))
    return int(v) if v == int(v) else v


def evaluate(mod: dict, text: str, inputs: dict | None = None, criteria_config: list | None = None) -> dict:
    inputs = inputs or {}
    t = to_str(text)
    low = t.lower()
    W = words(t)
    S = sentences(re.sub(r"^\s*[-*|].*$", "", re.sub(r"^#.*$", "", t, flags=re.M), flags=re.M))
    crit, flags = [], []

    def add(key, name, score, comment, rec):
        crit.append({"key": key, "name": name, "score": clamp(score), "comment": comment, "rec": rec})

    # 1. Структура
    secs = check_sections(mod, t)
    sec_score = sum(s["value"] for s in secs) / len(secs) * 10 if secs else 0
    miss = [s["title"] for s in secs if s["status"] == "missing"]
    thin = [s["title"] for s in secs if s["status"] in ("thin", "mention")]
    add("structure", "Структура", sec_score,
        f"Отсутствуют разделы: {', '.join(miss)}." if miss else f"Разделы раскрыты слабо: {', '.join(thin)}." if thin else "Все обязательные разделы присутствуют.",
        "Добавьте в блок ФОРМАТ промпта перечень обязательных разделов и выполните перегенерацию." if miss else
        "Попросите ИИ расширить слабые разделы или допишите их вручную в редакторе." if thin else "Сохраните структуру как образец для следующих работ.")

    # 2. Полнота
    min_words = mod.get("minWords") or 450
    ratio = len(W) / min_words
    add("completeness", "Полнота", 10 if ratio >= 1 else 8 if ratio >= 0.75 else 6 if ratio >= 0.5 else 4 if ratio >= 0.25 else 2,
        f"Объём: {len(W)} слов (ожидается от {min_words}).",
        "Объём достаточный; проверьте, нет ли повторов." if ratio >= 1 else "Укажите в промпте требуемый объём и детализацию разделов.")

    # 3. Соответствие теме
    topic_st = stems(inputs.get("topic"))
    if topic_st:
        hits = [s for s in topic_st if s in low]
        freq = sum(low.count(s) for s in topic_st)
        sc = len(hits) / len(topic_st) * 7 + min(3, freq / 3)
        add("topic", "Соответствие теме", sc,
            "Материал последовательно раскрывает заявленную тему." if sc >= 8 else "Тема раскрыта не полностью или упоминается редко.",
            "Сохраните формулировку темы." if sc >= 8 else "Уточните тему в блоке ЗАДАЧА и попросите привязать каждый раздел к теме.")
    else:
        add("topic", "Соответствие теме", 6, "Тема не указана в исходных данных.", "Заполните поле «Тема».")

    # 4. Соответствие результатам обучения
    out_st = [s for s in stems(inputs.get("outcomes")) if s not in GENERIC_OUTCOME]
    if out_st:
        hits = [s for s in out_st if s in low]
        sc = len(hits) / len(out_st) * 10
        add("outcomes", "Соответствие результатам обучения", sc,
            f"Отражено ключевых понятий из результатов обучения: {len(hits)} из {len(out_st)}.",
            "Результаты обучения учтены." if sc >= 8 else "Добавьте блок ЦЕЛИ ОБУЧЕНИЯ и попросите связать задания с каждым результатом.")
    else:
        add("outcomes", "Соответствие результатам обучения", 5, "Результаты обучения не заданы — проверка невозможна.", "Сформулируйте 2–4 измеримых результата обучения.")

    # 5. Соответствие аудитории
    aud_st = stems(inputs.get("audience"))[:2]
    aud_hit = any(s in low for s in aud_st) or bool(re.search(r"обучающ|студент|учащ|слушател|школьник|магистрант", low))
    lvl_hit = to_str(inputs.get("difficulty")).lower()[:5] in low if inputs.get("difficulty") else False
    add("audience", "Соответствие аудитории", (7 if aud_hit else 3) + (3 if lvl_hit else 1),
        "Материал ориентирован на указанную аудиторию." if aud_hit else "Аудитория в тексте не учитывается.",
        "Проверьте сложность примеров для этой группы." if aud_hit else "Добавьте блок АУДИТОРИЯ с уровнем подготовки обучающихся.")

    # 6. Понятность
    avg = sum(len(words(s)) for s in S) / len(S) if S else 0
    long_w = len([w for w in W if len(w) > 13]) / len(W) if W else 0
    cl = 5 if avg == 0 else 10 if avg <= 18 else 8 if avg <= 24 else 6 if avg <= 30 else 4
    if long_w > 0.12:
        cl -= 2
    add("clarity", "Понятность", cl, f"Средняя длина предложения: {js_fixed1(avg)} слов.",
        "Изложение доступное." if cl >= 8 else "Попросите ИИ упростить формулировки и разбить длинные предложения.")

    # 7. Корректность терминов
    defs = len(list(DEFS_RE.finditer(t)))
    add("terms", "Корректность терминов", 10 if defs >= 4 else 8 if defs >= 2 else 6 if defs == 1 else 4,
        f"Найдено определений терминов: {defs}.",
        "Сверьте определения с учебником или стандартом." if defs >= 2 else "Добавьте глоссарий: термин — определение.")

    # 8. Педагогическая ценность
    feats = [
        ("вопросы", t.count("?") >= 3),
        ("задания", bool(re.search(r"задани|упражнен", low))),
        ("примеры", bool(re.search(r"пример|кейс|ситуац", low))),
        ("оценивание", bool(re.search(r"критери|балл|оценив", low))),
        ("рефлексия / обсуждение", bool(re.search(r"рефлекс|обсужд|дискус", low))),
    ]
    has = [f[0] for f in feats if f[1]]
    lack = [f[0] for f in feats if not f[1]]
    add("pedagogy", "Педагогическая ценность", len(has) * 2,
        f"Есть: {', '.join(has)}." if has else "Нет активных элементов обучения.",
        f"Добавьте: {', '.join(lack)}." if lack else "Материал методически насыщен.")

    # 9. Научная корректность
    absolutes = [m.group(0) for m in ABSOLUTES_RE.finditer(t)]
    stats = [m.group(0) for m in STATS_RE.finditer(t)]
    sci = 10 - len(absolutes) * 1.5 - (2 if len(stats) > 2 else 0)
    if absolutes:
        flags.append(f"Категоричные утверждения ({', '.join(absolutes[:3])}) — проверьте их обоснованность.")
    if stats:
        flags.append(f"В тексте есть числовые данные ({', '.join(stats[:3])}) — укажите источник или удалите.")
    add("science", "Научная корректность", sci,
        "Обнаружены утверждения, требующие обоснования." if absolutes or stats else "Явных некорректных утверждений не выявлено (требуется проверка преподавателем).",
        "Смягчите категоричность и подтвердите данные источниками." if absolutes or stats else "Проверьте факты по учебной литературе.")

    # 10. Достоверность и риск галлюцинаций
    ref_count = len(DOI_RE.findall(t)) + len(URL_RE.findall(t)) + len(list(CITE_RE.finditer(t)))
    verified = bool(re.search(r"openalex|crossref|проверено по", t, I))
    marked = bool(re.search(r"требует проверки|требуют проверки|проверить по", t, I))
    if not ref_count:
        src = 10
    elif verified:
        src = 9
    elif marked:
        src = 8
    else:
        src = 4
        flags.append(f"Найдено ссылок/DOI/авторов: {ref_count}. Сгенерированные ИИ библиографические данные могут быть вымышленными (галлюцинации) — проверьте по официальным базам.")
    add("sources", "Достоверность и риск галлюцинаций", src,
        f"Ссылок на источники: {ref_count}{' (часть проверена по OpenAlex/Crossref)' if verified else ''}." if ref_count else "Библиографических ссылок нет — риск вымышленных источников отсутствует.",
        "Проверьте каждую ссылку и DOI в OpenAlex, Crossref или в библиотеке вуза." if ref_count and not verified else "При добавлении источников используйте только проверенные публикации.")

    total = js_round(sum(c["score"] for c in crit) / len(crit) * 10)
    if criteria_config:
        crit, total = apply_config(crit, criteria_config)
    level = "Отлично" if total >= 85 else "Хорошо" if total >= 70 else "Удовлетворительно" if total >= 50 else "Требует доработки"
    return {"total": total, "level": level, "criteria": crit, "sections": secs, "flags": flags,
            "stats": {"words": len(W), "sentences": len(S), "avgSentence": float(js_fixed1(avg))}, "engine": "server"}
