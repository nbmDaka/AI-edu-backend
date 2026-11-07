"""Таблицы модуля «Виртуальные лаборатории» (ТЗ, п. 31).

Основные сущности ТЗ: User (пользователь платформы), Discipline, Course, VirtualWork, TrainingSession,
Prompt, Generation, Evaluation, Recommendation, PortfolioItem, Progress, UploadedFile.
Дополнительно: профиль преподавателя, роли модуля, группы тьютора, задания, рецензии,
шаблоны промптов, критерии экспертизы, подключения к ИИ, события, журнал запросов к ИИ, LTI.

Пользователь берётся из settings.AUTH_USER_MODEL, поэтому модуль работает и с моделью
пользователя AI edu (вход по email), и со стандартной моделью Django.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

USER = settings.AUTH_USER_MODEL


class LabRole(models.Model):
    """Роль пользователя внутри модуля. Таблица отдельная, чтобы не менять модель пользователя платформы."""
    class Role(models.TextChoices):
        TEACHER = 'teacher', 'Преподаватель'
        TUTOR = 'tutor', 'Тьютор'
        ADMIN = 'admin', 'Администратор'

    user = models.OneToOneField(USER, on_delete=models.CASCADE, related_name='lab_role')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.TEACHER)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vl_role'


class Discipline(models.Model):
    """Дисциплина (предмет) преподавателя."""
    owner = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_disciplines')
    name = models.CharField(max_length=300)
    program = models.CharField(max_length=300, blank=True)
    level = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_discipline'
        unique_together = ('owner', 'name')
        ordering = ('name',)


class TeacherProfile(models.Model):
    """Профиль преподавателя (ТЗ, п. 9)."""
    user = models.OneToOneField(USER, on_delete=models.CASCADE, related_name='lab_profile')
    fio = models.CharField(max_length=200, blank=True)
    organization = models.CharField(max_length=300, blank=True)
    position = models.CharField(max_length=200, blank=True)
    discipline = models.ForeignKey(Discipline, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    data = models.JSONField(default=dict, blank=True)   # программа, уровень, курс, язык, длительность, результаты
    settings = models.JSONField(default=dict, blank=True)   # настройки интерфейса без ключей API
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vl_profile'


class Course(models.Model):
    """Курс (раздел каталога), объединяющий виртуальные работы. Управляется администратором."""
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'vl_course'
        ordering = ('position', 'id')


class VirtualWork(models.Model):
    """Виртуальная работа (модуль VirtualWork01…14). Описание работы хранится в реестре интерфейса,
    в таблице — то, чем управляет администратор: курс, порядок, доступность."""
    code = models.CharField(max_length=32, unique=True)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=300)
    course = models.ForeignKey(Course, null=True, blank=True, on_delete=models.SET_NULL, related_name='works')
    position = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        db_table = 'vl_work'
        ordering = ('position', 'number')


class PromptTemplate(models.Model):
    """Системный шаблон промпта (ТЗ, п. 8.3, 25). Пустой work — общая системная инструкция для всех работ."""
    work = models.OneToOneField(VirtualWork, null=True, blank=True, on_delete=models.CASCADE, related_name='prompt_template')
    role = models.TextField(blank=True)
    task = models.TextField(blank=True)
    constraints = models.TextField(blank=True)     # дополнительные ограничения, по одному в строке
    system = models.TextField(blank=True)          # системная инструкция для языковой модели
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vl_prompt_template'


class EvaluationCriterion(models.Model):
    """Критерий AI-экспертизы (ТЗ, п. 27). Администратор меняет название, вес и включает или выключает критерий."""
    key = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    weight = models.FloatField(default=1.0)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'vl_criterion'
        ordering = ('position', 'id')


class AIProvider(models.Model):
    """Подключение к внешнему сервису ИИ (ТЗ, п. 26, 35). Ключ никогда не отдаётся интерфейсу."""
    class Kind(models.TextChoices):
        OPENAI = 'openai', 'OpenAI (ChatGPT)'
        GEMINI = 'gemini', 'Google Gemini'
        COMPATIBLE = 'compatible', 'OpenAI-совместимый API'
        ANTHROPIC = 'anthropic', 'Anthropic Claude'
        OPENAI_TTS = 'openai_tts', 'OpenAI TTS'
        ELEVENLABS = 'elevenlabs', 'ElevenLabs'

    class Task(models.TextChoices):
        TEXT = 'text', 'Текст'
        TTS = 'tts', 'Озвучивание'

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    task = models.CharField(max_length=10, choices=Task.choices, default=Task.TEXT)
    base_url = models.CharField(max_length=500, blank=True)
    model = models.CharField(max_length=200, blank=True)
    voice = models.CharField(max_length=120, blank=True)
    api_key = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vl_ai_provider'
        ordering = ('task', 'name')


class StudyGroup(models.Model):
    """Группа слушателей тьютора (ТЗ, п. 8.2)."""
    tutor = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_groups')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    invite_code = models.CharField(max_length=12, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_group'
        ordering = ('name',)

    def save(self, *args, **kwargs):
        if not self.invite_code:
            self.invite_code = new_invite_code()
        super().save(*args, **kwargs)


def new_invite_code():
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(8))
        if not StudyGroup.objects.filter(invite_code=code).exists():
            return code


class GroupMembership(models.Model):
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_memberships')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_group_member'
        unique_together = ('group', 'user')


class Assignment(models.Model):
    """Задание тьютора группе: выполнить виртуальную работу к сроку (ТЗ, п. 8.2)."""
    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='assignments')
    work = models.ForeignKey(VirtualWork, on_delete=models.CASCADE, related_name='assignments')
    title = models.CharField(max_length=300, blank=True)
    instructions = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_assignment'
        ordering = ('-created_at',)


class TrainingSession(models.Model):
    """Прохождение одной виртуальной работы пользователем (TrainingSession)."""
    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', 'В работе'
        COMPLETED = 'completed', 'Выполнена'

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_sessions')
    work = models.ForeignKey(VirtualWork, on_delete=models.CASCADE, related_name='sessions')
    discipline = models.ForeignKey(Discipline, null=True, blank=True, on_delete=models.SET_NULL, related_name='sessions')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    step = models.PositiveSmallIntegerField(default=0)
    inputs = models.JSONField(default=dict, blank=True)
    current_prompt = models.TextField(blank=True)
    state = models.JSONField(default=dict, blank=True)   # чат ассистента, самопроверка, найденные публикации и т. п.
    started_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    time_spent_ms = models.BigIntegerField(default=0)
    generations = models.PositiveIntegerField(default=0)
    prompt_improvements = models.PositiveIntegerField(default=0)
    edits = models.PositiveIntegerField(default=0)
    current_generation = models.ForeignKey('Generation', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'vl_session'
        unique_together = ('user', 'work')


class Prompt(models.Model):
    """Версия промпта (Prompt v1, v2 …) с анализом структуры."""
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='prompts')
    number = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, default='initial')   # initial | improved | edited
    text = models.TextField()
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    analysis = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_prompt'
        ordering = ('number',)
        unique_together = ('session', 'number')


class Generation(models.Model):
    """Версия материала (Result v1, v2 …): генерация моделью или правка преподавателя."""
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='generations_set')
    client_id = models.CharField(max_length=64)
    number = models.PositiveIntegerField()
    kind = models.CharField(max_length=20, default='generation')   # generation | edit
    prompt = models.ForeignKey(Prompt, null=True, blank=True, on_delete=models.SET_NULL, related_name='generations')
    prompt_text = models.TextField(blank=True)
    prompt_score = models.FloatField(null=True, blank=True)
    text = models.TextField()
    provider = models.CharField(max_length=40, blank=True)
    model = models.CharField(max_length=200, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_generation'
        ordering = ('number',)
        unique_together = ('session', 'client_id')


class Evaluation(models.Model):
    """Результат AI-экспертизы версии материала: критерий → балл → комментарий → рекомендация."""
    generation = models.OneToOneField(Generation, on_delete=models.CASCADE, related_name='evaluation')
    total = models.FloatField()
    level = models.CharField(max_length=60, blank=True)
    criteria = models.JSONField(default=list)
    sections = models.JSONField(default=list)
    flags = models.JSONField(default=list)
    stats = models.JSONField(default=dict)
    engine = models.CharField(max_length=20, default='server')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_evaluation'


class Recommendation(models.Model):
    """Рекомендация Recommendation Engine по сессии (ТЗ, п. 28)."""
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='recommendations')
    generation = models.ForeignKey(Generation, null=True, blank=True, on_delete=models.CASCADE, related_name='recommendations')
    kind = models.CharField(max_length=20, default='rule')     # rule | llm | next_work
    text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_recommendation'
        ordering = ('id',)


class PortfolioItem(models.Model):
    """Материал электронного портфолио (ТЗ, п. 32)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_portfolio')
    client_id = models.CharField(max_length=64)
    session = models.ForeignKey(TrainingSession, null=True, blank=True, on_delete=models.SET_NULL, related_name='portfolio_items')
    generation = models.ForeignKey(Generation, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    work_code = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=100)
    title = models.CharField(max_length=500)
    text = models.TextField()
    score = models.FloatField(null=True, blank=True)
    prompt_score = models.FloatField(null=True, blank=True)
    prompt = models.TextField(blank=True)
    version_number = models.PositiveIntegerField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_portfolio_item'
        ordering = ('created_at', 'id')
        unique_together = ('user', 'client_id')


class Progress(models.Model):
    """Сводный прогресс пользователя по работе (для панели слушателя, тьютора и аналитики)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_progress')
    work = models.ForeignKey(VirtualWork, on_delete=models.CASCADE, related_name='progress')
    is_completed = models.BooleanField(default=False)
    first_score = models.FloatField(null=True, blank=True)
    last_score = models.FloatField(null=True, blank=True)
    best_score = models.FloatField(null=True, blank=True)
    prompt_score = models.FloatField(null=True, blank=True)
    quiz_score = models.FloatField(null=True, blank=True)
    generations = models.PositiveIntegerField(default=0)
    prompt_improvements = models.PositiveIntegerField(default=0)
    edits = models.PositiveIntegerField(default=0)
    time_spent_ms = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vl_progress'
        unique_together = ('user', 'work')


class Review(models.Model):
    """Комментарий и оценка тьютора по выполненной работе (ТЗ, п. 8.2)."""
    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='reviews')
    assignment = models.ForeignKey(Assignment, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviews')
    reviewer = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='+')
    comment = models.TextField(blank=True)
    grade = models.PositiveSmallIntegerField(null=True, blank=True)   # 0–100
    created_at = models.DateTimeField(default=timezone.now)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'vl_review'
        ordering = ('-created_at',)


def upload_path(instance, filename):
    from .lab_settings import conf
    from pathlib import Path
    stamp = timezone.now()
    return f"{stamp.strftime(conf('UPLOAD_DIR'))}/{secrets.token_hex(12)}{Path(filename).suffix.lower()}"


class UploadedFile(models.Model):
    """Загруженный документ преподавателя: силлабус, лекция, программа, методичка, учебник, презентация (ТЗ, п. 9, 34.4)."""
    class Kind(models.TextChoices):
        SYLLABUS = 'syllabus', 'Силлабус'
        LECTURE = 'lecture', 'Лекция'
        PROGRAM = 'program', 'Рабочая программа'
        GUIDELINES = 'guidelines', 'Методические указания'
        TEXTBOOK = 'textbook', 'Учебник'
        PRESENTATION = 'presentation', 'Презентация'
        OTHER = 'other', 'Другое'

    owner = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_files')
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    file = models.FileField(upload_to=upload_path, max_length=300)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    text = models.TextField(blank=True)
    pages = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_uploaded_file'
        ordering = ('-created_at',)


class LearningEvent(models.Model):
    """Событие обучения для Learning Analytics (ТЗ, п. 33)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_events')
    type = models.CharField(max_length=40)
    work_code = models.CharField(max_length=32, blank=True)
    data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_event'
        ordering = ('id',)


class AIRequest(models.Model):
    """Журнал обращений к сервисам ИИ (ТЗ, п. 38: журналирование)."""
    user = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='lab_ai_requests')
    kind = models.CharField(max_length=20)     # generate | chat | tts | literature
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=200, blank=True)
    ok = models.BooleanField(default=True)
    duration_ms = models.PositiveIntegerField(default=0)
    chars = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_ai_request'
        ordering = ('-id',)


class LtiPlatform(models.Model):
    """LMS, подключённая по LTI 1.3 (Moodle, Open edX, Canvas …) (ТЗ, п. 37)."""
    name = models.CharField(max_length=200)
    issuer = models.CharField(max_length=500)
    client_id = models.CharField(max_length=255)
    deployment_ids = models.JSONField(default=list, blank=True)
    auth_login_url = models.URLField(max_length=500)
    auth_token_url = models.URLField(max_length=500, blank=True)
    jwks_url = models.URLField(max_length=500)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_lti_platform'
        unique_together = ('issuer', 'client_id')


class LtiToolKey(models.Model):
    """Ключ RSA инструмента для подписи (публикуется через JWKS)."""
    kid = models.CharField(max_length=64, unique=True)
    private_pem = models.TextField()
    public_jwk = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'vl_lti_key'


class LtiUserLink(models.Model):
    """Связь пользователя LMS (iss + sub) с пользователем платформы."""
    platform = models.ForeignKey(LtiPlatform, on_delete=models.CASCADE, related_name='users')
    subject = models.CharField(max_length=255)
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='lab_lti_links')
    last_launch_at = models.DateTimeField(default=timezone.now)
    context_title = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = 'vl_lti_user'
        unique_together = ('platform', 'subject')


class BackupRecord(models.Model):
    """Журнал резервных копий базы (ТЗ, п. 38)."""
    path = models.CharField(max_length=500)
    method = models.CharField(max_length=20)       # sqlite | pg_dump | json
    size = models.BigIntegerField(default=0)
    ok = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'vl_backup'
        ordering = ('-created_at',)
