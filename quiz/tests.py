from django.test import TestCase, override_settings
from django.urls import reverse

from breeding.models import AnimalKind, Breed

from .models import Answer, AnswerWeight, Question
from .utils import calculate_result


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class QuizWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        kind = AnimalKind.objects.create(name='Dog')
        cls.first_breed = Breed.objects.create(
            kind=kind,
            name='First breed',
            cover='breeds/first.jpg',
            order=2,
        )
        cls.preferred_tie_breed = Breed.objects.create(
            kind=kind,
            name='Preferred tie breed',
            cover='breeds/preferred.jpg',
            order=1,
        )
        cls.first_question = Question.objects.create(
            text='Energy level?',
            order=1,
        )
        cls.second_question = Question.objects.create(
            text='Home size?',
            order=2,
        )
        cls.active_answer = Answer.objects.create(
            question=cls.first_question,
            text='Very active',
            order=1,
        )
        cls.home_answer = Answer.objects.create(
            question=cls.second_question,
            text='Large home',
            order=1,
        )
        AnswerWeight.objects.create(
            answer=cls.active_answer,
            breed=cls.first_breed,
            weight=5,
        )
        AnswerWeight.objects.create(
            answer=cls.active_answer,
            breed=cls.preferred_tie_breed,
            weight=2,
        )
        AnswerWeight.objects.create(
            answer=cls.home_answer,
            breed=cls.first_breed,
            weight=1,
        )
        AnswerWeight.objects.create(
            answer=cls.home_answer,
            breed=cls.preferred_tie_breed,
            weight=5,
        )

    def test_get_builds_questions_in_configured_order(self):
        response = self.client.get(reverse('quiz:start_quiz'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['form'].fields),
            [
                f'question_{self.first_question.pk}',
                f'question_{self.second_question.pk}',
            ],
        )
        self.assertContains(response, self.active_answer.text)

    def test_valid_answers_render_highest_scoring_breed(self):
        response = self.client.post(
            reverse('quiz:start_quiz'),
            {
                f'question_{self.first_question.pk}': self.active_answer.pk,
                f'question_{self.second_question.pk}': self.home_answer.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'quiz/result.html')
        self.assertEqual(response.context['breed'], self.preferred_tie_breed)

    def test_answer_from_another_question_is_rejected(self):
        response = self.client.post(
            reverse('quiz:start_quiz'),
            {
                f'question_{self.first_question.pk}': self.home_answer.pk,
                f'question_{self.second_question.pk}': self.home_answer.pk,
            },
        )

        form = response.context['form']
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'quiz/quiz.html')
        self.assertIn(
            f'question_{self.first_question.pk}',
            form.errors,
        )

    def test_equal_scores_use_breed_order_as_stable_tie_breaker(self):
        tie_answer = Answer.objects.create(
            question=self.first_question,
            text='Tie',
        )
        AnswerWeight.objects.create(
            answer=tie_answer,
            breed=self.first_breed,
            weight=3,
        )
        AnswerWeight.objects.create(
            answer=tie_answer,
            breed=self.preferred_tie_breed,
            weight=3,
        )

        result = calculate_result([tie_answer.pk])

        self.assertEqual(result, self.preferred_tie_breed)

    def test_answer_without_weights_returns_form_error_instead_of_500(self):
        first_no_weight = Answer.objects.create(
            question=self.first_question,
            text='No first configured result',
        )
        second_no_weight = Answer.objects.create(
            question=self.second_question,
            text='No second configured result',
        )

        response = self.client.post(
            reverse('quiz:start_quiz'),
            {
                f'question_{self.first_question.pk}': first_no_weight.pk,
                f'question_{self.second_question.pk}': second_no_weight.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'The quiz is not fully configured yet.',
        )

    def test_unknown_answer_is_rejected_by_result_calculation(self):
        with self.assertRaises(Answer.DoesNotExist):
            calculate_result([999999])


@override_settings(STATIC_ROOT=None, STORAGES=TEST_STORAGES)
class EmptyQuizTests(TestCase):
    def test_empty_quiz_has_safe_empty_state(self):
        response = self.client.get(reverse('quiz:start_quiz'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'The quiz is not available yet. Please try again later.',
        )
        self.assertNotContains(response, 'id="quiz-form"')

    def test_empty_quiz_post_does_not_raise(self):
        response = self.client.post(reverse('quiz:start_quiz'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'quiz/quiz.html')
