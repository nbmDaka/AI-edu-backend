from django.conf import settings
from django.db import models

class Test(models.Model):
    lesson = models.OneToOneField('education.Lesson', null=True, blank=True, on_delete=models.PROTECT, related_name='test')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    passing_percent = models.PositiveSmallIntegerField(default=70)
    max_attempts = models.PositiveIntegerField(null=True, blank=True)
    is_published = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)

class Question(models.Model):
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField()
    position = models.PositiveIntegerField()
    points = models.PositiveIntegerField(default=1)
    competency = models.CharField(max_length=100, default='general')
    class Meta:
        ordering = ['position', 'id']

class Option(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=1000)
    is_correct = models.BooleanField(default=False)
    position = models.PositiveIntegerField()
    class Meta:
        ordering = ['position', 'id']

class TestAttempt(models.Model):
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name='attempts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='attempts')
    test_version = models.PositiveIntegerField()
    answers = models.JSONField()
    snapshot = models.JSONField()
    earned_points = models.PositiveIntegerField()
    total_points = models.PositiveIntegerField()
    percent = models.FloatField()
    passed = models.BooleanField()
    adaptive_result = models.JSONField(null=True, blank=True)
    completed_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-completed_at', '-id']
