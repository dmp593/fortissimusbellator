from django.db import migrations


UPDATED_ANSWERS = {
    'Is the pre-reservation fee refundable?': {
        'answer_en': (
            'No. If you choose to cancel, the pre-reservation fee is '
            'non-refundable. You must explicitly accept this condition before '
            'payment. If our team cancels a paid pre-reservation, we will '
            'process a refund. If you complete the purchase, the fee is '
            'deducted in full from the final price of the dog.'
        ),
        'answer_pt': (
            'Não. Se decidir cancelar, o valor da pré-reserva não é '
            'reembolsável. Tem de aceitar expressamente esta condição antes do '
            'pagamento. Se a nossa equipa cancelar uma pré-reserva paga, '
            'processaremos o reembolso. Se concluir a compra, o valor da '
            'pré-reserva é deduzido na totalidade do preço final do cão.'
        ),
        'answer_es': (
            'No. Si decide cancelar, el importe de la prerreserva no es '
            'reembolsable. Debe aceptar expresamente esta condición antes del '
            'pago. Si nuestro equipo cancela una prerreserva pagada, '
            'tramitaremos el reembolso. Si completa la compra, el importe se '
            'descuenta íntegramente del precio final del perro.'
        ),
        'answer_fr': (
            "Non. Si vous décidez d'annuler, le montant de la pré-réservation "
            "n'est pas remboursable. Vous devez accepter expressément cette "
            "condition avant le paiement. Si notre équipe annule une "
            "pré-réservation payée, nous la rembourserons. Si l'achat est "
            'finalisé, le montant est intégralement déduit du prix final du chien.'
        ),
        'answer_de': (
            'Nein. Wenn Sie stornieren, wird der Betrag der Vorreservierung '
            'nicht erstattet. Sie müssen dies vor der Zahlung ausdrücklich '
            'akzeptieren. Wenn unser Team eine bezahlte Vorreservierung '
            'storniert, erstatten wir den Betrag. Beim Kauf wird der Betrag '
            'vollständig vom Endpreis des Hundes abgezogen.'
        ),
        'answer_it': (
            "No. Se decide di annullare, l'importo della pre-prenotazione non è "
            'rimborsabile. Deve accettare espressamente questa condizione prima '
            'del pagamento. Se il nostro team annulla una pre-prenotazione '
            "pagata, rimborseremo l'importo. Se completa l'acquisto, l'importo "
            'viene detratto integralmente dal prezzo finale del cane.'
        ),
    },
    'Can I reserve a puppy before it is ready to go home?': {
        'answer_en': (
            'Yes. When an eligible dog or a place in a born litter is available, '
            'you can complete a paid pre-reservation online. The fee is '
            'non-refundable if you cancel and is deducted in full from the final '
            'price of the dog if the purchase is completed.'
        ),
        'answer_pt': (
            'Sim. Quando estiver disponível um cão elegível ou um lugar numa '
            'ninhada já nascida, pode fazer uma pré-reserva paga online. O valor '
            'não é reembolsável se cancelar e é deduzido na totalidade do preço '
            'final do cão se a compra for concluída.'
        ),
        'answer_es': (
            'Sí. Cuando esté disponible un perro elegible o una plaza de una '
            'camada ya nacida, puede realizar una prerreserva pagada en línea. '
            'El importe no es reembolsable si cancela y se descuenta íntegramente '
            'del precio final del perro si se completa la compra.'
        ),
        'answer_fr': (
            "Oui. Lorsqu'un chien éligible ou une place dans une portée déjà née "
            'est disponible, vous pouvez effectuer une pré-réservation payante '
            "en ligne. Le montant n'est pas remboursable si vous annulez et est "
            "intégralement déduit du prix final du chien si l'achat est finalisé."
        ),
        'answer_de': (
            'Ja. Wenn ein geeigneter Hund oder ein Platz in einem bereits '
            'geborenen Wurf verfügbar ist, können Sie online eine kostenpflichtige '
            'Vorreservierung vornehmen. Der Betrag wird bei einer Stornierung '
            'durch Sie nicht erstattet und bei abgeschlossenem Kauf vollständig '
            'vom Endpreis des Hundes abgezogen.'
        ),
        'answer_it': (
            'Sì. Quando è disponibile un cane idoneo o un posto in una '
            'cucciolata già nata, può effettuare online una pre-prenotazione a '
            "pagamento. L'importo non è rimborsabile in caso di annullamento e "
            'viene detratto integralmente dal prezzo finale del cane se '
            "l'acquisto viene completato."
        ),
    },
}


def update_pre_reservation_faqs(apps, schema_editor):
    faq_model = apps.get_model('frontoffice', 'FrequentlyAskedQuestion')
    for question_en, answers in UPDATED_ANSWERS.items():
        faq_model.objects.filter(question_en=question_en).update(
            answer=answers['answer_en'],
            **answers,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('frontoffice', '0004_pre_reservation_faqs'),
    ]

    operations = [
        migrations.RunPython(
            update_pre_reservation_faqs,
            migrations.RunPython.noop,
        ),
    ]
