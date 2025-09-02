from django.shortcuts import render
from .forms import QuizForm
from .utils import calculate_result


def pet_finder(request):
    if request.method == "POST":
        form = QuizForm(request.POST)
        if form.is_valid():

            answer_ids = [
                a.id for a in form.cleaned_data.values()
            ]

            breed = calculate_result(answer_ids)

            return render(request, "quiz/result.html", {"breed": breed, })
    else:
        form = QuizForm()

    return render(
        request,
        "quiz/quiz.html",
        {"form": form, }
    )
