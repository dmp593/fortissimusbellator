from collections import defaultdict
from .models import Answer


def calculate_result(answer_ids):
    scores = defaultdict(int)

    for ans_id in answer_ids:
        answer = Answer.objects.get(id=ans_id)
        for weight in answer.answerweight_set.all():
            scores[weight.breed] += weight.weight

    # Return best breed by score
    return max(scores.items(), key=lambda x: x[1])[0]
