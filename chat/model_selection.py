"""Read and update the website's active local chat model."""

from .models import ChatModel, ChatModelConfiguration


CONFIGURATION_PK = 1


class ModelSelectionError(RuntimeError):
    """No enabled model is available to the local assistant."""


def available_models():
    return ChatModel.objects.filter(enabled=True)


def selected_model():
    configuration = (
        ChatModelConfiguration.objects.select_related("active_model")
        .filter(pk=CONFIGURATION_PK, active_model__enabled=True)
        .first()
    )
    model = configuration.active_model if configuration else None
    if model is None:
        model = available_models().first()
    if model is None:
        raise ModelSelectionError(
            "No enabled local chat model is configured."
        )
    return model.to_spec()


def save_selected_model(model_id):
    """Persist one enabled model as the website selection."""
    try:
        model = available_models().get(pk=model_id)
    except ChatModel.DoesNotExist as exc:
        raise ModelSelectionError("Select an enabled chat model.") from exc

    ChatModelConfiguration.objects.update_or_create(
        pk=CONFIGURATION_PK,
        defaults={"active_model": model},
    )
    return model.to_spec()
