from collections import defaultdict

from .models import Answer


def calculate_result(answer_ids):
    answer_ids = tuple(answer_ids)
    if not answer_ids:
        return None

    answers = {
        answer.pk: answer
        for answer in Answer.objects.filter(
            pk__in=answer_ids,
        ).prefetch_related('answerweight_set__breed')
    }
    if set(answer_ids) - answers.keys():
        raise Answer.DoesNotExist('One or more quiz answers do not exist.')

    scores = defaultdict(int)
    for ans_id in answer_ids:
        answer = answers[ans_id]
        for weight in answer.answerweight_set.all():
            scores[weight.breed] += weight.weight

    if not scores:
        return None

    return min(
        scores,
        key=lambda breed: (
            -scores[breed],
            breed.order,
            str(breed.name).casefold(),
            breed.pk,
        ),
    )
