from django.db import migrations


FAQS = (
    {
        'question_en': 'Is the pre-reservation fee refundable?',
        'answer_en': (
            'No. If you choose to cancel, the pre-reservation fee is non-refundable. '
            'You must explicitly accept this condition before payment. If our team '
            'cancels a paid pre-reservation, we will process a refund. If you complete '
            'the purchase, the fee is deducted in full from the final price of the dog.'
        ),
        'question_pt': 'A taxa de pre-reserva e reembolsavel?',
        'answer_pt': (
            'Nao. Se decidir cancelar, a taxa de pre-reserva nao e reembolsavel. '
            'Tem de aceitar expressamente esta condicao antes do pagamento. Se a '
            'nossa equipa cancelar uma pre-reserva paga, processaremos o reembolso. '
            'Se concluir a compra, a taxa e deduzida na totalidade do preco final do cao.'
        ),
        'question_es': 'Es reembolsable la tarifa de prerreserva?',
        'answer_es': (
            'No. Si decide cancelar, la tarifa de prerreserva no es reembolsable. '
            'Debe aceptar expresamente esta condicion antes del pago. Si nuestro '
            'equipo cancela una prerreserva pagada, tramitaremos el reembolso. Si '
            'completa la compra, la tarifa se descuenta integramente del precio final.'
        ),
        'question_fr': 'Les frais de prereservation sont-ils remboursables?',
        'answer_fr': (
            'Non. Si vous decidez d annuler, les frais de prereservation ne sont pas '
            'remboursables. Vous devez accepter cette condition avant le paiement. '
            'Si notre equipe annule une prereservation payee, nous la rembourserons. '
            'Si l achat est finalise, les frais sont deduits du prix final du chien.'
        ),
        'question_de': 'Ist die Vorreservierungsgebuehr erstattungsfaehig?',
        'answer_de': (
            'Nein. Wenn Sie stornieren, wird die Vorreservierungsgebuehr nicht '
            'erstattet. Sie muessen dies vor der Zahlung ausdruecklich akzeptieren. '
            'Wenn unser Team storniert, erstatten wir eine bezahlte Vorreservierung. '
            'Beim Kauf wird die Gebuehr vollstaendig vom Endpreis des Hundes abgezogen.'
        ),
        'question_it': 'La quota di pre-prenotazione e rimborsabile?',
        'answer_it': (
            'No. Se decide di annullare, la quota di pre-prenotazione non e '
            'rimborsabile. Deve accettare espressamente questa condizione prima del '
            'pagamento. Se annulliamo noi, rimborseremo la quota pagata. Se completa '
            'l acquisto, la quota viene detratta interamente dal prezzo finale del cane.'
        ),
        'order': 200,
    },
    {
        'question_en': 'What happens while I am completing payment?',
        'answer_en': (
            'We create a pending pre-reservation before sending you to Stripe, so '
            'another customer cannot take the same dog or the same available litter '
            'place. A failed or expired payment releases that place.'
        ),
        'question_pt': 'O que acontece enquanto concluo o pagamento?',
        'answer_pt': (
            'Criamos uma pre-reserva pendente antes de o enviar para a Stripe, para '
            'que outro cliente nao possa ocupar o mesmo cao ou lugar da ninhada. Um '
            'pagamento falhado ou expirado liberta o lugar.'
        ),
        'question_es': 'Que ocurre mientras completo el pago?',
        'answer_es': (
            'Creamos una prerreserva pendiente antes de enviarlo a Stripe, para que '
            'otro cliente no pueda ocupar el mismo perro o plaza de camada. Un pago '
            'fallido o caducado libera la plaza.'
        ),
        'question_fr': 'Que se passe-t-il pendant le paiement?',
        'answer_fr': (
            'Nous creons une prereservation en attente avant de vous envoyer vers '
            'Stripe. Personne ne peut alors prendre le meme chien ou la meme place '
            'de portee. Un paiement echoue ou expire libere la place.'
        ),
        'question_de': 'Was geschieht, waehrend ich bezahle?',
        'answer_de': (
            'Vor der Weiterleitung zu Stripe erstellen wir eine ausstehende '
            'Vorreservierung. So kann niemand denselben Hund oder Wurfplatz belegen. '
            'Eine fehlgeschlagene oder abgelaufene Zahlung gibt den Platz frei.'
        ),
        'question_it': 'Cosa succede mentre completo il pagamento?',
        'answer_it': (
            'Creiamo una pre-prenotazione in attesa prima di inviarla a Stripe, '
            'impedendo ad altri di occupare lo stesso cane o posto nella cucciolata. '
            'Un pagamento fallito o scaduto libera il posto.'
        ),
        'order': 201,
    },
    {
        'question_en': 'How do litter pre-reservations work?',
        'answer_en': (
            'A litter can be pre-reserved only after the puppies are born. The shown '
            'capacity may be lower than the number born. You reserve one available '
            'place in the litter, not a specific puppy, unless we later agree otherwise.'
        ),
        'question_pt': 'Como funcionam as pre-reservas de ninhadas?',
        'answer_pt': (
            'Uma ninhada so pode ser pre-reservada depois do nascimento dos cachorros. '
            'A capacidade apresentada pode ser inferior ao numero de nascidos. Reserva '
            'um lugar disponivel na ninhada, nao um cachorro especifico.'
        ),
        'question_es': 'Como funcionan las prerreservas de camadas?',
        'answer_es': (
            'Una camada solo puede prerreservarse despues del nacimiento. La capacidad '
            'mostrada puede ser menor que el numero de cachorros nacidos. Reserva una '
            'plaza disponible, no un cachorro concreto.'
        ),
        'question_fr': 'Comment fonctionnent les prereservations de portee?',
        'answer_fr': (
            'Une portee ne peut etre prereservee qu apres la naissance. La capacite '
            'affichee peut etre inferieure au nombre de chiots nes. Vous reservez une '
            'place disponible, pas un chiot precis.'
        ),
        'question_de': 'Wie funktionieren Vorreservierungen fuer Wuerfe?',
        'answer_de': (
            'Ein Wurf kann erst nach der Geburt vorreserviert werden. Die angezeigte '
            'Kapazitaet kann kleiner als die Zahl der geborenen Welpen sein. Sie '
            'reservieren einen Platz, keinen bestimmten Welpen.'
        ),
        'question_it': 'Come funzionano le pre-prenotazioni delle cucciolate?',
        'answer_it': (
            'Una cucciolata puo essere pre-prenotata solo dopo la nascita. La capacita '
            'mostrata puo essere inferiore al numero di cuccioli nati. Prenota un posto '
            'disponibile, non un cucciolo specifico.'
        ),
        'order': 202,
    },
    {
        'question_en': 'Where can I see or cancel my pre-reservations?',
        'answer_en': (
            'Use My Reservations in your account. It includes active reservations and '
            'history even if a dog or litter is no longer public. You may cancel an '
            'eligible reservation there; customer cancellations are non-refundable.'
        ),
        'question_pt': 'Onde posso ver ou cancelar as minhas pre-reservas?',
        'answer_pt': (
            'Use As Minhas Reservas na sua conta. Inclui reservas ativas e historico, '
            'mesmo que o cao ou a ninhada ja nao estejam publicos. Pode cancelar uma '
            'reserva elegivel; cancelamentos do cliente nao sao reembolsaveis.'
        ),
        'question_es': 'Donde puedo ver o cancelar mis prerreservas?',
        'answer_es': (
            'Use Mis Reservas en su cuenta. Incluye reservas activas e historial, '
            'aunque el perro o la camada ya no sean publicos. Puede cancelar una '
            'reserva elegible; las cancelaciones del cliente no se reembolsan.'
        ),
        'question_fr': 'Ou consulter ou annuler mes prereservations?',
        'answer_fr': (
            'Utilisez Mes reservations dans votre compte. Vous y trouverez les '
            'reservations actives et l historique, meme si l animal n est plus public. '
            'Une annulation par le client n est pas remboursable.'
        ),
        'question_de': 'Wo sehe oder storniere ich meine Vorreservierungen?',
        'answer_de': (
            'Unter Meine Reservierungen sehen Sie aktive und fruehere Reservierungen, '
            'auch wenn Hund oder Wurf nicht mehr oeffentlich sind. Dort koennen Sie '
            'stornieren; Kundenstornierungen werden nicht erstattet.'
        ),
        'question_it': 'Dove vedo o annullo le mie pre-prenotazioni?',
        'answer_it': (
            'Usi Le mie prenotazioni nel suo account. Include prenotazioni attive e '
            'storico anche se il cane o la cucciolata non sono piu pubblici. Le '
            'cancellazioni del cliente non sono rimborsabili.'
        ),
        'order': 203,
    },
    {
        'question_en': 'How do I get my fiscal document?',
        'answer_en': (
            'After payment, we create the fiscal document separately and email its PDF '
            'when available. You can also download it from My Reservations. If the ERP '
            'or PDF service is temporarily unavailable, your payment remains confirmed '
            'and the operation can be retried safely.'
        ),
        'question_pt': 'Como obtenho o meu documento fiscal?',
        'answer_pt': (
            'Depois do pagamento, criamos separadamente o documento fiscal e enviamos '
            'o PDF por email quando estiver disponivel. Tambem pode descarrega-lo nas '
            'Minhas Reservas. Uma falha temporaria nao altera o pagamento confirmado.'
        ),
        'question_es': 'Como obtengo mi documento fiscal?',
        'answer_es': (
            'Tras el pago creamos el documento fiscal por separado y enviamos el PDF '
            'cuando este disponible. Tambien puede descargarlo en Mis Reservas. Un '
            'fallo temporal no modifica el pago confirmado y puede reintentarse.'
        ),
        'question_fr': 'Comment obtenir mon document fiscal?',
        'answer_fr': (
            'Apres le paiement, nous creons le document fiscal separement et envoyons '
            'son PDF lorsqu il est disponible. Vous pouvez aussi le telecharger dans '
            'Mes reservations. Une panne temporaire ne modifie pas le paiement confirme.'
        ),
        'question_de': 'Wie erhalte ich meinen Steuerbeleg?',
        'answer_de': (
            'Nach der Zahlung erstellen wir den Steuerbeleg separat und senden die PDF, '
            'sobald sie verfuegbar ist. Sie koennen sie auch unter Meine Reservierungen '
            'laden. Eine temporaere Stoerung aendert die bestaetigte Zahlung nicht.'
        ),
        'question_it': 'Come ricevo il documento fiscale?',
        'answer_it': (
            'Dopo il pagamento creiamo separatamente il documento fiscale e inviamo il '
            'PDF quando disponibile. Puo anche scaricarlo da Le mie prenotazioni. Un '
            'errore temporaneo non modifica il pagamento confermato.'
        ),
        'order': 204,
    },
)


def create_pre_reservation_faqs(apps, schema_editor):
    faq_model = apps.get_model('frontoffice', 'FrequentlyAskedQuestion')
    for faq in FAQS:
        defaults = {
            **faq,
            'question': faq['question_en'],
            'answer': faq['answer_en'],
            'active': True,
        }
        faq_model.objects.update_or_create(
            question_en=faq['question_en'],
            defaults=defaults,
        )


def remove_pre_reservation_faqs(apps, schema_editor):
    faq_model = apps.get_model('frontoffice', 'FrequentlyAskedQuestion')
    faq_model.objects.filter(
        question_en__in=[faq['question_en'] for faq in FAQS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('frontoffice', '0003_faq_chat_search_aliases'),
    ]

    operations = [
        migrations.RunPython(
            create_pre_reservation_faqs,
            remove_pre_reservation_faqs,
        ),
    ]
