import io
import zipfile

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from ..models import LabRole


def make_user(login, role=None, password='S3cure-pass!'):
    """Пользователь платформы. Поле логина берётся из модели: username в автономной версии, email в AI edu."""
    User = get_user_model()
    user = User.objects.create_user(**{User.USERNAME_FIELD: login}, password=password)
    if role:
        LabRole.objects.create(user=user, role=role)
    return user


def find_users(**lookup):
    """Поиск по полю логина модели пользователя: find_users(startswith='lti-'), find_users(exact='admin')."""
    User = get_user_model()
    return User.objects.filter(**{f'{User.USERNAME_FIELD}__{k}': v for k, v in lookup.items()})


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def docx_bytes(paragraphs, heading='Силлабус дисциплины'):
    import docx
    doc = docx.Document()
    doc.add_heading(heading, level=1)
    for p in paragraphs:
        doc.add_paragraph(p)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def pptx_bytes(titles):
    from pptx import Presentation
    prs = Presentation()
    for t in titles:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = t
        s.placeholders[1].text = 'Тезис слайда ' + t
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def pdf_bytes(text):
    from ..services.exporting import to_pdf
    return to_pdf('Лекция', text)


def zip_with(names):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name in names:
            z.writestr(name, 'x')
    return out.getvalue()


LECTURE = """## Цель лекции
Сформировать представление о методах активного обучения и их применении в вузе для студентов бакалавриата.

## Результаты обучения
- объяснять сущность методов активного обучения;
- применять кейс-метод при проектировании занятия;

## План
1. Понятие активного обучения. 2. Классификация методов. 3. Примеры.

## Введение
Активное обучение — это подход, при котором обучающиеся участвуют в работе. Почему он важен? Как его применять?

## Основной материал
Методы активного обучения включают дискуссию, кейс-метод, деловую игру и проектную работу. Каждый метод требует подготовки.

## Примеры
Пример: кейс о выборе стратегии обучения для студентов первого курса. Ситуация разбирается в группах.

## Ключевые понятия
**Кейс-метод** — это метод анализа ситуации. **Дискуссия** — это обсуждение спорного вопроса.

## Выводы
Активные методы повышают вовлечённость. Что изменится в вашей практике?

## Контрольные вопросы
1. Что такое активное обучение? 2. Чем кейс отличается от задачи?

## Задания для самостоятельной работы
Разработайте задание с критериями оценивания в баллах и проведите рефлексию.
"""
